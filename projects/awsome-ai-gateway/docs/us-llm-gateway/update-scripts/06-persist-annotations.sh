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
# ⚠️ It deliberately does NOT persist `security-group-prefix-lists`.
#    This chart renders all three Ingresses (gateway / admin-api / admin-ui)
#    from one shared `.Values.ingress.annotations` map — templates/common/
#    ingress.yaml:22,58,100 — so writing the CloudFront prefix list there
#    would also open admin-api and admin-ui to anything routed through any
#    CloudFront distribution, bypassing their IP restriction entirely.
#    That rule therefore stays cluster-only and must be re-applied by hand
#    after every helm upgrade. The script says so at the end.
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

if [ -z "$UNION" ]; then
  warn "no inbound-cidrs set on any Ingress — nothing to persist"
  exit 0
fi
if [ "$UNION" = "$FILE_VAL" ]; then
  ok "already matches. Nothing to do."
  exit 0
fi

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
  rm -f "$NEW"; exit 0
fi

confirm "Write this allow-list into $VALUES_FILE (helm is NOT run)."

BAK="$SNAP_DIR/${TS}-06-$(basename "$VALUES_FILE")"
cp "$VALUES_FILE" "$BAK" || die "backup failed"
cp "$NEW" "$VALUES_FILE" || die "write failed"
rm -f "$NEW"
ok "written"
note "previous file: $BAK"

# The part this script cannot fix — say it plainly, every run.
hdr "Still not persisted"
PL_LIVE=$(ann_of "$ING_GATEWAY" "$PL_KEY")
PL_FILE=$(file_ann "$PL_KEY")
if [ -n "$PL_LIVE" ] && [ -z "$PL_FILE" ]; then
  bad "$PL_KEY = $PL_LIVE  (gateway Ingress only, cluster-only)"
  cat <<EOF

  This is what lets CloudFront reach the gateway. It is NOT written to values
  on purpose: this chart shares one annotations map across all three
  Ingresses, so persisting it would also open admin-api and admin-ui to any
  CloudFront distribution — bypassing their IP restriction.

  Consequence: after every 'helm upgrade' the rule is gone and every request
  through CloudFront returns 502. Re-apply it with:

      bash 03-create-cloudfront.sh --allow-cloudfront

  The durable fix is a chart change — per-Ingress annotations, so the gateway
  can carry the prefix list without the control plane inheriting it.
EOF
else
  ok "nothing outstanding"
fi

hdr "Next steps"
cat <<EOF
  Nothing was deployed. Verify the file with:
    grep -n 'alb.ingress.kubernetes.io' $VALUES_FILE | grep -v '#'

  This file holds account-specific values and is not committed, so it lives
  only on this machine — keep a copy somewhere safe.
EOF
