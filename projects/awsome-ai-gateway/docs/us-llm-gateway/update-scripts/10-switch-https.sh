#!/bin/bash
# ---------------------------------------------------------------------------
# 10-switch-https.sh
#
# WHAT: rewrite the helm values Ingress block from 방식 A (http:80, ALB DNS)
#       to 방식 B (https:443 on a custom domain with an ACM certificate) —
#       US-06, docs/us-llm-gateway/ops/8-H-alb-https.md
# WHY:  the switch is six edits spread over the ingress block, and the file
#       is the only copy of this deployment's real values. Editing by hand is
#       where a lost inbound-cidrs line or a stray tab comes from.
# UNDO: --revert (back to 방식 A), and the file is snapshotted before writing
#
# This edits a file. It does not run helm — that is install-eks.sh's job.
#
# Usage:
#   bash 10-switch-https.sh --domain example.com --cert-arn arn:aws:acm:...     # dry-run, show diff
#   bash 10-switch-https.sh --domain example.com --cert-arn arn:aws:acm:... --apply
#   bash 10-switch-https.sh ... --drop-cloudfront   # also remove the CloudFront prefix-list
#                                                   # from the gateway Ingress (CloudFront retired)
#   bash 10-switch-https.sh --revert [--apply]      # back to 방식 A (http:80, no host)
#
# Hostnames: gateway-<env>.<domain> · admin-<env>.<domain> · admin-api-<env>.<domain>
#            (env = DEPLOY_ENV from config.env; override with --gateway-host etc.)
# The domain and cert can also live in config.env as HTTPS_DOMAIN / HTTPS_CERT_ARN.
# ---------------------------------------------------------------------------
set -uo pipefail
source "$(dirname "$(readlink -f "$0")")/_lib.sh"

DOMAIN="${HTTPS_DOMAIN:-${DOMAIN:-}}"; CERT_ARN="${HTTPS_CERT_ARN:-${CERT_ARN:-}}"   # config.env, or exported by https-env.sh
ORIG_ARGS="$*"
GW_H=""; UI_H=""; API_H=""
APPLY=0; DROP_CF=0; REVERT=0
while [ $# -gt 0 ]; do
  case "$1" in
    --domain)          DOMAIN="$2"; shift 2 ;;
    --cert-arn)        CERT_ARN="$2"; shift 2 ;;
    --gateway-host)    GW_H="$2"; shift 2 ;;
    --admin-ui-host)   UI_H="$2"; shift 2 ;;
    --admin-api-host)  API_H="$2"; shift 2 ;;
    --drop-cloudfront) DROP_CF=1; shift ;;
    --revert)          REVERT=1; shift ;;
    --apply)           APPLY=1; shift ;;
    -h|--help)         sed -n '2,25p' "$0"; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

require_env

VALUES_FILE="${HELM_VALUES_FILE:-$LIB_DIR/../../../deployment/charts/llm-gateway/values-eks-fargate-${DEPLOY_ENV}.yaml}"
[ -f "$VALUES_FILE" ] || die "values file not found: $VALUES_FILE
     Set HELM_VALUES_FILE in config.env if your chart lives elsewhere."
VALUES_FILE=$(readlink -f "$VALUES_FILE")
CHART_DIR="$LIB_DIR/../../../deployment/charts/llm-gateway"

if [ "$REVERT" = 0 ]; then
  [ -n "$DOMAIN" ]   || die "--domain <domain> is required (or HTTPS_DOMAIN in config.env)"
  [ -n "$CERT_ARN" ] || die "--cert-arn <arn> is required (or HTTPS_CERT_ARN in config.env)"
  case "$CERT_ARN" in arn:aws:acm:*) ;; *) die "--cert-arn must be an ACM certificate ARN" ;; esac
  # The certificate must live in the ALB's region — an ACM cert in us-east-1
  # (the CloudFront region) is invisible to an ALB in us-west-2.
  CERT_REGION=$(cut -d: -f4 <<<"$CERT_ARN")
  [ "$CERT_REGION" = "$AWS_REGION" ] || die "certificate is in $CERT_REGION but the ALB is in $AWS_REGION.
     ALB can only attach a certificate from its own region — request it with --region $AWS_REGION."
  ST=$(aws acm describe-certificate --region "$AWS_REGION" --certificate-arn "$CERT_ARN" \
        --query Certificate.Status --output text 2>/dev/null)
  [ "$ST" = "ISSUED" ] || die "certificate status is '${ST:-unknown}' — needs ISSUED (DNS validation done?)"
  : "${GW_H:=gateway-${DEPLOY_ENV}.${DOMAIN}}"
  : "${UI_H:=admin-${DEPLOY_ENV}.${DOMAIN}}"
  : "${API_H:=admin-api-${DEPLOY_ENV}.${DOMAIN}}"
fi

hdr "Target"
if [ "$REVERT" = 1 ]; then
  printf '  방식 A 로 복귀 — listen HTTP:80, host 없음, TLS 없음 (prefix-list 는 되돌리지 않음: 06 으로)\n'
else
  printf '  %-14s %s\n' "values"    "$VALUES_FILE"
  printf '  %-14s %s\n' "cert"      "$CERT_ARN  ($ST)"
  printf '  %-14s %s\n' "gateway"   "https://$GW_H"
  printf '  %-14s %s\n' "admin-ui"  "https://$UI_H"
  printf '  %-14s %s\n' "admin-api" "https://$API_H"
  [ "$DROP_CF" = 1 ] && printf '  %-14s %s\n' "cloudfront" "prefix-list removed from gateway Ingress (retired)"
fi

NEW=$(mktemp)
# Text edits, never a YAML round-trip: the file's comments are documentation.
python3 - "$VALUES_FILE" "$NEW" "$REVERT" "$DROP_CF" "$GW_H" "$UI_H" "$API_H" "$CERT_ARN" <<'PY'
import re, sys
src, dst, revert, drop_cf, gw_h, ui_h, api_h, cert = sys.argv[1:9]
revert = revert == "1"; drop_cf = drop_cf == "1"
L = open(src, encoding="utf-8").read().split("\n")

def indent(l): return len(l) - len(l.lstrip(" "))
def is_code(l): return l.strip() and not l.lstrip().startswith("#")

# top-level `ingress:` block (uncommented) — ends at the next uncommented top-level key
try:
    top = next(i for i, l in enumerate(L) if re.match(r"^ingress:\s*(#.*)?$", l))
except StopIteration:
    sys.exit("values: no top-level 'ingress:' block")
end = next((i for i in range(top + 1, len(L)) if is_code(L[i]) and indent(L[i]) == 0), len(L))

def sub_block(start, level):
    """lines (start+1 ..) belonging to the key at `start` with child indent > level"""
    j = start + 1
    while j < end and (not is_code(L[j]) or indent(L[j]) > level):
        j += 1
    return j

def find_key(a, b, level, key):
    for i in range(a, b):
        if is_code(L[i]) and indent(L[i]) == level and re.match(rf"^\s*{re.escape(key)}:", L[i]):
            return i
    return None

changes = 0
# ---- ingress.annotations (indent 2)
ann = find_key(top + 1, end, 2, "annotations")
if ann is None:
    sys.exit("values: ingress.annotations not found")
ann_end = sub_block(ann, 2)
LP = "alb.ingress.kubernetes.io/listen-ports"
CA = "alb.ingress.kubernetes.io/certificate-arn"
SP = "alb.ingress.kubernetes.io/ssl-policy"
SR = "alb.ingress.kubernetes.io/ssl-redirect"
def ann_line(k): return find_key(ann + 1, ann_end, 4, k)
lp = ann_line(LP)
if lp is None:
    sys.exit("values: listen-ports annotation not found under ingress.annotations")
if revert:
    L[lp] = "    " + LP + ": '[{\"HTTP\":80}]'"; changes += 1
    for k in (CA, SP, SR):
        i = ann_line(k)
        if i is not None:
            del L[i]; end -= 1; ann_end -= 1; changes += 1
else:
    L[lp] = "    " + LP + ": '[{\"HTTPS\":443}]'"; changes += 1
    ins = lp + 1
    for k, v in ((SP, "ELBSecurityPolicy-TLS13-1-2-2021-06"), (CA, f'"{cert}"')):
        i = ann_line(k)
        line = f"    {k}: {v}"
        if i is None:
            L.insert(ins, line); end += 1; ann_end += 1
        else:
            L[i] = line
        changes += 1
    i = ann_line(SR)
    if i is not None:   # no :80 listener → redirect annotation is meaningless
        del L[i]; end -= 1; ann_end -= 1; changes += 1

# ---- per-Ingress sections
for sec, host in (("gateway", gw_h), ("adminUi", ui_h), ("adminApi", api_h)):
    s = find_key(top + 1, end, 2, sec)
    if s is None:
        sys.exit(f"values: ingress.{sec} not found")
    s_end = sub_block(s, 2)
    h = find_key(s + 1, s_end, 4, "host")
    if h is None:
        sys.exit(f"values: ingress.{sec}.host not found")
    L[h] = f'    host: "{"" if revert else host}"'; changes += 1
    t = find_key(s + 1, s_end, 4, "tls")
    if t is not None:
        t_end = sub_block(t, 4)
        e = find_key(t + 1, t_end, 6, "enabled")
        if e is not None:
            L[e] = f"      enabled: {'false' if revert else 'true'}"; changes += 1
    if sec == "gateway" and drop_cf and not revert:
        a = find_key(s + 1, s_end, 4, "annotations")
        if a is not None:
            a_end = sub_block(a, 4)
            p = find_key(a + 1, a_end, 6, "alb.ingress.kubernetes.io/security-group-prefix-lists")
            if p is not None:
                del L[p]; end -= 1; changes += 1
                a_end -= 1
                if not any(is_code(L[i]) for i in range(a + 1, a_end)):
                    L[a] = "    annotations: {}"

open(dst, "w", encoding="utf-8").write("\n".join(L))
PY
rc=$?
[ $rc -eq 0 ] || { rm -f "$NEW"; die "edit failed"; }

if diff -q "$VALUES_FILE" "$NEW" >/dev/null; then
  hdr "Result"; ok "values already in the target state. Nothing to do."; rm -f "$NEW"; echo; exit 0
fi

hdr "Planned change (not applied yet)"
diff -u "$VALUES_FILE" "$NEW" | sed -n '3,$p' | sed 's/^/  /'

# Render check: does the chart accept the result? (helm optional — skip if absent)
if command -v helm >/dev/null 2>&1 && [ -d "$CHART_DIR" ]; then
  hdr "Render check (helm template)"
  if OUT=$(helm template llm-gateway "$CHART_DIR" -f "$NEW" 2>&1); then
    grep -E 'kind: Ingress|certificate-arn|listen-ports|^\s+- host:' <<<"$OUT" | sed 's/^/  /'
    grep -A1 -E '^\s+- name: NEXTAUTH_URL' <<<"$OUT" | grep value | sed 's/^\s*/  NEXTAUTH_URL /' 
    ok "chart renders"
  else
    warn "helm template failed — do not apply until this renders:"; sed 's/^/  /' <<<"$OUT" | tail -15
  fi
fi

if [ "$APPLY" = 0 ]; then
  echo; note "Nothing written."; note "Apply:  bash $(basename "$0") $ORIG_ARGS --apply"; rm -f "$NEW"; echo; exit 0
fi

confirm "Write the Ingress block into $VALUES_FILE (helm is NOT run — next: ./deployment/scripts/install-eks.sh $DEPLOY_ENV)."
BAK="$SNAP_DIR/${TS}-10-$(basename "$VALUES_FILE")"
cp "$VALUES_FILE" "$BAK" || { rm -f "$NEW"; die "backup failed"; }
cp "$NEW" "$VALUES_FILE" || { rm -f "$NEW"; die "write failed"; }
rm -f "$NEW"
ok "written"; note "previous file: $BAK"
echo
hdr "Next"
cat <<EOF
  1) cd $LIB_DIR/../../..  &&  ./deployment/scripts/install-eks.sh $DEPLOY_ENV
     (watch: kubectl get events -n $NS --sort-by=.lastTimestamp | tail — no RulesPerSecurityGroupLimitExceeded)
  2) bash 11-route53-cname.sh --apply       # names → ALB DNS
  3) bash 07-client-values.sh               # hand out the new https URLs
EOF
