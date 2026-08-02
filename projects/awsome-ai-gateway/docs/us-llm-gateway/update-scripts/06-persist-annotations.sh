#!/bin/bash
# ---------------------------------------------------------------------------
# 06-persist-annotations.sh
#
# WHAT: copy the gateway Ingress annotations that 03/05 set with `kubectl`
#       into the helm values file, so they survive the next `helm upgrade`
# WHY:  helm rebuilds the Ingress from values, and the AWS Load Balancer
#       Controller rebuilds the SG from the Ingress. An annotation that exists
#       only in the cluster is dropped on the next upgrade — taking the
#       CloudFront allow rule (03) or the client IP list (05) with it, and the
#       gateway starts returning 502 with nothing in the diff to explain why.
# UNDO: the file is copied to snapshots/ before anything is written
#
# This edits a file. It does not run helm.
# ---------------------------------------------------------------------------
set -uo pipefail
source "$(dirname "$(readlink -f "$0")")/_lib.sh"

require_env

# Which annotations are worth persisting. Both are set by other scripts here
# and both fail closed-or-open in ways that are hard to notice:
#   security-group-prefix-lists  missing -> CloudFront cannot reach the origin (502)
#   inbound-cidrs                missing -> the ALB drops the allow-list entirely
KEYS=(security-group-prefix-lists inbound-cidrs)

# The values file helm was installed with. Blank => derived from DEPLOY_ENV in
# the standard repo layout (this script lives 3 levels below the chart root).
VALUES_FILE="${HELM_VALUES_FILE:-$LIB_DIR/../../../deployment/charts/llm-gateway/values-eks-fargate-${DEPLOY_ENV}.yaml}"
[ -f "$VALUES_FILE" ] || die "values file not found: $VALUES_FILE
     Set HELM_VALUES_FILE in config.env if your chart lives elsewhere."
VALUES_FILE=$(readlink -f "$VALUES_FILE")

# Value currently live on the Ingress (what the controller is acting on).
live_ann() {
  kubectl get ingress "$ING_GATEWAY" -n "$NS" \
    -o jsonpath="{.metadata.annotations.alb\\.ingress\\.kubernetes\\.io/$1}" 2>/dev/null
}

# Value in the values file. Commented-out lines are ignored — the file ships
# with a fully commented "option B" ingress block that would otherwise match.
file_ann() {
  grep -E "^[[:space:]]*alb\.ingress\.kubernetes\.io/$1:[[:space:]]" "$VALUES_FILE" \
    | grep -vE '^[[:space:]]*#' | head -1 \
    | sed -E 's/^[^:]*:[[:space:]]*//; s/^"//; s/"[[:space:]]*$//'
}

echo
printf '%s Persist Ingress annotations into helm values%s\n' "$c_bold" "$c_reset"
hdr "Files"
echo "  ingress      $ING_GATEWAY  (namespace $NS)"
echo "  values file  $VALUES_FILE"

hdr "Live vs file"
CHANGES=()
for k in "${KEYS[@]}"; do
  l=$(live_ann "$k"); f=$(file_ann "$k")
  printf '  %-28s live: %s\n' "$k" "${l:-<unset>}"
  printf '  %-28s file: %s\n' "" "${f:-<unset>}"
  if [ -z "$l" ]; then
    note "not set on the Ingress — nothing to persist"
  elif [ "$l" = "$f" ]; then
    ok "already matches"
  else
    CHANGES+=("$k=$l")
    warn "differs — would be written into the values file"
  fi
  echo
done

if [ ${#CHANGES[@]} -eq 0 ]; then
  ok "values file already reflects the live Ingress. Nothing to do."
  exit 0
fi

# Rewrite: replace the key if an active line already exists, otherwise insert
# it just above the `scheme` annotation (present in every active ingress block
# this chart ships, and the anchor gives us the right indentation for free).
rewrite() {
  # \037 (unit separator) joins the pairs — it cannot occur in an annotation
  # value, and it is fixed inside the program because command-line operands
  # are not yet assigned when BEGIN runs.
  awk -v pairs="$1" '
    BEGIN {
      n = split(pairs, kv, "\037")
      for (i = 1; i <= n; i++) {
        p = index(kv[i], "=")
        key[i] = substr(kv[i], 1, p - 1); val[i] = substr(kv[i], p + 1); done[i] = 0
      }
    }
    {
      line = $0
      # replace in place when the key is already there and not commented out
      for (i = 1; i <= n; i++) {
        if (!done[i] && line ~ ("^[[:space:]]*alb\\.ingress\\.kubernetes\\.io/" key[i] ":")) {
          match(line, /^[[:space:]]*/); ind = substr(line, 1, RLENGTH)
          print ind "alb.ingress.kubernetes.io/" key[i] ": \"" val[i] "\""
          done[i] = 1; next
        }
      }
      # otherwise insert above the scheme annotation, once
      if (line ~ /^[[:space:]]*alb\.ingress\.kubernetes\.io\/scheme:/) {
        match(line, /^[[:space:]]*/); ind = substr(line, 1, RLENGTH)
        for (i = 1; i <= n; i++)
          if (!done[i]) { print ind "alb.ingress.kubernetes.io/" key[i] ": \"" val[i] "\""; done[i] = 1 }
      }
      print line
    }
    # A values file with no active `scheme` annotation gives the insert branch
    # nothing to anchor to. Fail loudly rather than write a file that silently
    # dropped a key.
    END {
      for (i = 1; i <= n; i++)
        if (!done[i]) { print "UNPLACED:" key[i] > "/dev/stderr"; rc = 1 }
      exit rc
    }
  ' "$VALUES_FILE"
}

PAIRS=$(printf '%s\x1f' "${CHANGES[@]}"); PAIRS=${PAIRS%$'\x1f'}
NEW=$(mktemp)
if ! rewrite "$PAIRS" > "$NEW" 2>"$NEW.err"; then
  sed 's/^/  /' "$NEW.err"
  rm -f "$NEW" "$NEW.err"
  die "Could not place every annotation. The values file has no active
     'alb.ingress.kubernetes.io/scheme' line to anchor the insert on —
     add the keys by hand under ingress.annotations."
fi
rm -f "$NEW.err"

hdr "Planned change (not applied yet)"
diff -u "$VALUES_FILE" "$NEW" | sed -n '3,$p' | sed 's/^/  /'

if [ "${1:-}" != "--apply" ]; then
  echo
  note "Nothing written."
  note "Apply:  bash $(basename "$0") --apply"
  rm -f "$NEW"; exit 0
fi

confirm "Write these annotations into $VALUES_FILE (helm is NOT run)."

BAK="$SNAP_DIR/${TS}-06-$(basename "$VALUES_FILE")"
cp "$VALUES_FILE" "$BAK" || die "backup failed"
cp "$NEW" "$VALUES_FILE" || die "write failed"
rm -f "$NEW"
ok "written"
note "previous file: $BAK"

hdr "Next steps"
cat <<EOF
  The file now matches the live Ingress, so the next \`helm upgrade\` keeps
  these annotations instead of dropping them.

  Nothing was deployed. Verify with:
    grep -n 'alb.ingress.kubernetes.io' $VALUES_FILE | grep -v '#'

  Note this file holds account-specific values and is not committed, so it
  lives only on this machine — keep a copy somewhere safe.
EOF
