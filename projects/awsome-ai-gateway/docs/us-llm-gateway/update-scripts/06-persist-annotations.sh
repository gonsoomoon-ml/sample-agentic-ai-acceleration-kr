#!/bin/bash
# ---------------------------------------------------------------------------
# 06-persist-annotations.sh
#
# WHAT: write the Ingress `inbound-cidrs` allow-list into the helm values file
#       so it survives the next `helm upgrade`
# WHY:  helm rebuilds the Ingress from values and the AWS Load Balancer
#       Controller rebuilds the SG from the Ingress. A CIDR added with
#       `kubectl annotate` (05) exists only in the cluster and is dropped on
#       the next upgrade — VK issuance then fails for that client with no
#       diff to explain it.
# UNDO: the file is copied to snapshots/ before anything is written
#
# This edits a file. It does not run helm.
#
# Two keys, two destinations:
#   inbound-cidrs                → ingress.annotations         (shared)
#   security-group-prefix-lists  → ingress.gateway.annotations (gateway only)
#
# The prefix list must NOT go in the shared map: all three Ingresses render
# from it, so admin-api and admin-ui would open to anything routed through any
# CloudFront distribution, bypassing their IP restriction. Charts that lack the
# per-Ingress map cannot express this at all — there the rule stays cluster-only
# and this script says so instead of writing it somewhere unsafe.
# ---------------------------------------------------------------------------
set -uo pipefail
source "$(dirname "$(readlink -f "$0")")/_lib.sh"

require_env

KEY="inbound-cidrs"
PL_KEY="security-group-prefix-lists"

# The values file helm was installed with. Blank => derived from DEPLOY_ENV in
# the standard repo layout (this script lives 3 levels below the chart root).
VALUES_FILE="${HELM_VALUES_FILE:-$LIB_DIR/../../../deployment/charts/llm-gateway/values-eks-fargate-${DEPLOY_ENV}.yaml}"
[ -f "$VALUES_FILE" ] || die "values file not found: $VALUES_FILE
     Set HELM_VALUES_FILE in config.env if your chart lives elsewhere."
VALUES_FILE=$(readlink -f "$VALUES_FILE")

ann_of() {
  kubectl get ingress "$1" -n "$NS" \
    -o jsonpath="{.metadata.annotations.alb\\.ingress\\.kubernetes\\.io/$2}" 2>/dev/null
}

# Value in the values file. Commented-out lines are ignored — the file ships
# with a fully commented "option B" ingress block that would otherwise match.
file_ann() {
  grep -E "^[[:space:]]*alb\.ingress\.kubernetes\.io/$1:[[:space:]]" "$VALUES_FILE" \
    | grep -vE '^[[:space:]]*#' | head -1 \
    | sed -E 's/^[^:]*:[[:space:]]*//; s/^"//; s/"[[:space:]]*$//'
}

echo
printf '%s Persist the Ingress allow-list into helm values%s\n' "$c_bold" "$c_reset"
hdr "Files"
echo "  ingresses    $ING_GATEWAY, $ING_ADMIN_API, $ING_ADMIN_UI"
echo "  values file  $VALUES_FILE"

# One shared annotations map means one shared allow-list. Take the union so
# that whichever Ingress 05 touched keeps its CIDR; first-seen order is kept
# so the result reads like the file the operator already knows.
hdr "Live allow-lists"
UNION=""
for ing in "$ING_GATEWAY" "$ING_ADMIN_API" "$ING_ADMIN_UI"; do
  v=$(ann_of "$ing" "$KEY")
  printf '  %-32s %s\n' "$ing" "${v:-<unset>}"
  IFS=, read -r -a parts <<<"$v"
  for c in "${parts[@]}"; do
    [ -n "$c" ] || continue
    case ",$UNION," in *",$c,"*) ;; *) UNION="${UNION:+$UNION,}$c" ;; esac
  done
done
echo
echo "  union        $UNION"
note "This chart gives all three Ingresses the same annotations, so the union"
note "is what helm can express. Anything narrower cannot be written to values."

FILE_VAL=$(file_ann "$KEY")
hdr "values file"
echo "  current      ${FILE_VAL:-<unset>}"

# Two independent keys are persisted, so neither section may exit early — the
# prefix-list section below must run even when the CIDRs need no change.
CIDR_TODO=1
if [ -z "$UNION" ]; then
  warn "no inbound-cidrs set on any Ingress — nothing to persist"
  CIDR_TODO=0
elif [ "$UNION" = "$FILE_VAL" ]; then
  ok "already matches. Nothing to do."
  CIDR_TODO=0
fi

if [ "$CIDR_TODO" = "1" ]; then

# Replace the key if an active line already exists, otherwise insert it just
# above the `scheme` annotation (present in every active ingress block this
# chart ships, and the anchor gives us the right indentation for free).
rewrite() {
  # \037 (unit separator) is fixed inside the program because command-line
  # operands are not yet assigned when BEGIN runs.
  awk -v pair="$1" '
    BEGIN { p = index(pair, "\037"); key = substr(pair, 1, p - 1); val = substr(pair, p + 1) }
    {
      line = $0
      if (!done && line ~ ("^[[:space:]]*alb\\.ingress\\.kubernetes\\.io/" key ":")) {
        match(line, /^[[:space:]]*/); ind = substr(line, 1, RLENGTH)
        print ind "alb.ingress.kubernetes.io/" key ": \"" val "\""
        done = 1; next
      }
      if (!done && line ~ /^[[:space:]]*alb\.ingress\.kubernetes\.io\/scheme:/) {
        match(line, /^[[:space:]]*/); ind = substr(line, 1, RLENGTH)
        print ind "alb.ingress.kubernetes.io/" key ": \"" val "\""
        done = 1
      }
      print line
    }
    # No active `scheme` annotation means the insert branch had nothing to
    # anchor to. Fail rather than write a file that silently dropped the key.
    END { if (!done) { print "UNPLACED" > "/dev/stderr"; exit 1 } }
  ' "$VALUES_FILE"
}

NEW=$(mktemp)
if ! rewrite "$KEY"$'\037'"$UNION" > "$NEW" 2>/dev/null; then
  rm -f "$NEW"
  die "Could not place the annotation. The values file has no active
     'alb.ingress.kubernetes.io/scheme' line to anchor the insert on —
     add the key by hand under ingress.annotations."
fi

hdr "Planned change (not applied yet)"
diff -u "$VALUES_FILE" "$NEW" | sed -n '3,$p' | sed 's/^/  /'

if [ "${1:-}" != "--apply" ]; then
  echo
  note "Nothing written."
  note "Apply:  bash $(basename "$0") --apply"
  rm -f "$NEW"
else
  confirm "Write this allow-list into $VALUES_FILE (helm is NOT run)."

  BAK="$SNAP_DIR/${TS}-06-$(basename "$VALUES_FILE")"
  cp "$VALUES_FILE" "$BAK" || die "backup failed"
  cp "$NEW" "$VALUES_FILE" || die "write failed"
  rm -f "$NEW"
  ok "written"
  note "previous file: $BAK"
fi
fi   # end CIDR_TODO

# ── gateway-only: the CloudFront prefix list ────────────────────────────────
hdr "Gateway-only allow-list (prefix list)"
PL_LIVE=$(ann_of "$ING_GATEWAY" "$PL_KEY")

# Only charts with a per-Ingress annotations map can hold this safely. Detect
# support from the template rather than the chart version, which operators fork.
ING_TPL="$LIB_DIR/../../../deployment/charts/llm-gateway/templates/common/ingress.yaml"
SUPPORTS_PER_INGRESS=0
[ -f "$ING_TPL" ] && grep -q 'Values\.ingress\.gateway\.annotations' "$ING_TPL" \
  && SUPPORTS_PER_INGRESS=1

if [ -z "$PL_LIVE" ]; then
  ok "no prefix list on $ING_GATEWAY — nothing to persist"
elif [ "$SUPPORTS_PER_INGRESS" = "0" ]; then
  bad "$PL_KEY = $PL_LIVE  (cluster-only — this chart cannot hold it)"
  cat <<EOF

  This is what lets CloudFront reach the gateway. This chart renders all three
  Ingresses from one shared annotations map, so writing it to values would also
  open admin-api and admin-ui to any CloudFront distribution.

  Consequence: after every 'helm upgrade' the rule is gone and every request
  through CloudFront returns 502. Re-apply it by hand with:

      bash 03-create-cloudfront.sh --allow-cloudfront

  The durable fix is the per-Ingress annotations map. Update the chart.
EOF
else
  # Nested write: ingress: -> gateway: -> annotations: -> <key>. Done as a text
  # edit, not a YAML round-trip, because the file is full of comments that
  # operators rely on and a reserializer would drop them.
  PL_NEW=$(mktemp)
  if python3 - "$VALUES_FILE" "$PL_KEY" "$PL_LIVE" > "$PL_NEW" <<'PY'
import re, sys
path, key, val = sys.argv[1], sys.argv[2], sys.argv[3]
full = f'alb.ingress.kubernetes.io/{key}'
lines = open(path, encoding='utf-8').read().split('\n')

def block(start, indent):
    """Index range of the sub-block under lines[start], by indentation."""
    i = start + 1
    while i < len(lines):
        l = lines[i]
        if l.strip() and not l.startswith(' ' * indent) or re.match(r'^\S', l):
            break
        i += 1
    return start + 1, i

top = next((i for i, l in enumerate(lines) if re.match(r'^ingress:\s*$', l)), None)
if top is None:
    sys.exit('no top-level "ingress:" key')
ts, te = block(top, 1)

gw = next((i for i in range(ts, te) if re.match(r'^  gateway:\s*$', lines[i])), None)
if gw is None:
    sys.exit('no "gateway:" under ingress')
gs, ge = block(gw, 3)

ann = next((i for i in range(gs, ge) if re.match(r'^    annotations:\s*', lines[i])), None)
if ann is None:
    lines[gw + 1:gw + 1] = ['    annotations:', f'      {full}: "{val}"']
else:
    if re.match(r'^    annotations:\s*\{\s*\}\s*$', lines[ann]):
        lines[ann:ann + 1] = ['    annotations:', f'      {full}: "{val}"']
    else:
        as_, ae = block(ann, 5)
        cur = next((i for i in range(as_, ae)
                    if re.match(r'^\s*' + re.escape(full) + r':', lines[i])), None)
        if cur is None:
            lines[ann + 1:ann + 1] = [f'      {full}: "{val}"']
        else:
            lines[cur] = f'      {full}: "{val}"'
sys.stdout.write('\n'.join(lines))
PY
  then
    if diff -q "$VALUES_FILE" "$PL_NEW" >/dev/null; then
      ok "already in values: $PL_KEY = $PL_LIVE"
      rm -f "$PL_NEW"
    else
      printf '  live   %s = %s\n' "$PL_KEY" "$PL_LIVE"
      hdr "Planned change (gateway only)"
      diff -u "$VALUES_FILE" "$PL_NEW" | sed -n '3,$p' | sed 's/^/  /'
      if [ "${1:-}" = "--apply" ]; then
        cp "$VALUES_FILE" "$SNAP_DIR/${TS}-06-pl-$(basename "$VALUES_FILE")"
        cp "$PL_NEW" "$VALUES_FILE" && ok "written to ingress.gateway.annotations"
      else
        note "Nothing written. Apply: bash $(basename "$0") --apply"
      fi
      rm -f "$PL_NEW"
    fi
  else
    rm -f "$PL_NEW"
    warn "Could not place $PL_KEY under ingress.gateway.annotations."
    note "Add it by hand, then 'helm upgrade' will keep it:"
    note "  ingress:"
    note "    gateway:"
    note "      annotations:"
    note "        alb.ingress.kubernetes.io/$PL_KEY: \"$PL_LIVE\""
  fi
fi

hdr "Next steps"
cat <<EOF
  Nothing was deployed. Verify the file with:
    grep -n 'alb.ingress.kubernetes.io' $VALUES_FILE | grep -v '#'

  This file holds account-specific values and is not committed, so it lives
  only on this machine — keep a copy somewhere safe.
EOF
