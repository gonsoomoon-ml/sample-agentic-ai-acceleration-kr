#!/bin/bash
# ---------------------------------------------------------------------------
# 06-persist-annotations.sh
#
# WHAT: write the Ingress allow-lists into the helm values file so they survive
#       the next `helm upgrade`
# WHY:  helm rebuilds the Ingress from values and the AWS Load Balancer
#       Controller rebuilds the SG from the Ingress. Anything added with
#       `kubectl annotate` (05) or by 03 exists only in the cluster and is
#       dropped on the next upgrade — VK issuance or the whole CloudFront path
#       then fails with no diff to explain it.
# UNDO: the file is copied to snapshots/ before anything is written
#
# This edits a file. It does not run helm.
#
# Two keys, and each goes where it actually applies:
#   inbound-cidrs                → ingress.annotations  when all three Ingresses
#                                  agree; otherwise each one's exact list goes
#                                  to ingress.<name>.annotations
#   security-group-prefix-lists  → ingress.gateway.annotations (gateway only)
#
# Writing the UNION into the shared map would be simpler but wrong: it grants
# every Ingress the widest list anyone has. A client IP added for admin-api
# would silently also open the gateway. Charts without the per-Ingress map
# cannot express anything narrower — there this falls back to the union and
# says so, and it cannot hold the prefix list at all.
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

# Only charts with a per-Ingress annotations map can hold a narrower list, or
# the prefix list at all. Detect from the template, not the chart version,
# which operators fork.
ING_TPL="$LIB_DIR/../../../deployment/charts/llm-gateway/templates/common/ingress.yaml"
PER_INGRESS=0
[ -f "$ING_TPL" ] && grep -q 'Values\.ingress\.gateway\.annotations' "$ING_TPL" && PER_INGRESS=1

ann_of() {
  kubectl get ingress "$1" -n "$NS" \
    -o jsonpath="{.metadata.annotations.alb\\.ingress\\.kubernetes\\.io/$2}" 2>/dev/null
}

# ── Writers ─────────────────────────────────────────────────────────────────
# Both read a file and emit the result on stdout, so several can be chained
# through temp files and the operator sees ONE diff at the end.
#
# Text edits, never a YAML round-trip: this file is full of comments the
# operator relies on and a reserializer would drop every one of them.

# Shared map. Replaces an active line, else inserts above `scheme` — present in
# every active ingress block this chart ships, and the anchor gives us the
# right indentation for free. Commented-out lines are skipped: the file ships a
# fully commented "option B" block that would otherwise match.
write_shared_ann() {
  awk -v pair="$2"$'\037'"$3" '
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
  ' "$1"
}

# Per-Ingress map: ingress: -> <section>: -> annotations: -> <key>.
# An empty value removes the key (and the annotations block if it empties out),
# which is how a section that no longer needs an override gets cleaned up.
write_ingress_ann() {
  python3 - "$1" "$2" "$3" "$4" <<'PY'
import re, sys
path, section, key, val = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
full = f'alb.ingress.kubernetes.io/{key}'
lines = open(path, encoding='utf-8').read().split('\n')

def block(start, indent):
    """Index range of the sub-block under lines[start], by indentation."""
    i = start + 1
    while i < len(lines):
        l = lines[i]
        if l.strip() and not l.startswith(' ' * indent):
            break
        i += 1
    return start + 1, i

top = next((i for i, l in enumerate(lines) if re.match(r'^ingress:\s*$', l)), None)
if top is None:
    sys.exit('no top-level "ingress:" key')
ts, te = block(top, 1)

sec = next((i for i in range(ts, te) if re.match(rf'^  {re.escape(section)}:\s*$', lines[i])), None)
if sec is None:
    if not val:
        sys.stdout.write('\n'.join(lines)); sys.exit(0)   # nothing to remove
    sys.exit(f'no "{section}:" under ingress')
ss, se = block(sec, 3)

ann = next((i for i in range(ss, se) if re.match(r'^    annotations:\s*', lines[i])), None)

if ann is None:
    if val:
        lines[sec + 1:sec + 1] = ['    annotations:', f'      {full}: "{val}"']
elif re.match(r'^    annotations:\s*\{\s*\}\s*$', lines[ann]):
    if val:
        lines[ann:ann + 1] = ['    annotations:', f'      {full}: "{val}"']
else:
    as_, ae = block(ann, 5)
    cur = next((i for i in range(as_, ae)
                if re.match(r'^\s*' + re.escape(full) + r':', lines[i])), None)
    if val:
        if cur is None:
            lines[ann + 1:ann + 1] = [f'      {full}: "{val}"']
        else:
            lines[cur] = f'      {full}: "{val}"'
    elif cur is not None:
        del lines[cur]
        # The block may now be empty; collapse it so no dangling key is left.
        as2, ae2 = block(ann, 5)
        if not any(l.strip() and not l.strip().startswith('#') for l in lines[as2:ae2]):
            del lines[as2:ae2]
            lines[ann] = '    annotations: {}'
sys.stdout.write('\n'.join(lines))
PY
}

echo
printf '%s Persist the Ingress allow-lists into helm values%s\n' "$c_bold" "$c_reset"
hdr "Files"
echo "  ingresses    $ING_GATEWAY, $ING_ADMIN_API, $ING_ADMIN_UI"
echo "  values file  $VALUES_FILE"
echo "  chart        $([ "$PER_INGRESS" = 1 ] && echo 'per-Ingress annotations supported' \
                       || echo 'shared annotations only (older chart)')"

# values section name  <->  Ingress object name
SECTIONS=(gateway adminApi adminUi)
ING_OF_gateway="$ING_GATEWAY"
ING_OF_adminApi="$ING_ADMIN_API"
ING_OF_adminUi="$ING_ADMIN_UI"

# ── What the cluster currently has ──────────────────────────────────────────
hdr "Live allow-lists"
declare -A LIVE
UNION=""
for s in "${SECTIONS[@]}"; do
  eval "ing=\$ING_OF_$s"
  v=$(ann_of "$ing" "$KEY")
  LIVE[$s]="$v"
  printf '  %-32s %s\n' "$ing" "${v:-<unset>}"
  IFS=, read -r -a parts <<<"$v"
  for c in "${parts[@]}"; do
    [ -n "$c" ] || continue
    case ",$UNION," in *",$c,"*) ;; *) UNION="${UNION:+$UNION,}$c" ;; esac
  done
done

# Common = present on every Ingress. Order follows the first list so the result
# reads like the file the operator already knows.
COMMON=""
IFS=, read -r -a first <<<"${LIVE[gateway]}"
for c in "${first[@]}"; do
  [ -n "$c" ] || continue
  in_all=1
  for s in "${SECTIONS[@]}"; do
    case ",${LIVE[$s]}," in *",$c,"*) ;; *) in_all=0 ;; esac
  done
  [ "$in_all" = 1 ] && COMMON="${COMMON:+$COMMON,}$c"
done

# ── Plan ────────────────────────────────────────────────────────────────────
# SHARED_TARGET goes to ingress.annotations; SEC_TARGET[s] to the per-Ingress
# map ("" = no override needed / remove a stale one).
declare -A SEC_TARGET
if [ "$PER_INGRESS" = "1" ]; then
  SHARED_TARGET="$COMMON"
  for s in "${SECTIONS[@]}"; do
    if [ "${LIVE[$s]}" = "$COMMON" ]; then SEC_TARGET[$s]=""; else SEC_TARGET[$s]="${LIVE[$s]}"; fi
  done
else
  # Nothing narrower is expressible. Say what that costs instead of doing it
  # quietly — the union grants every Ingress the widest list anyone has.
  SHARED_TARGET="$UNION"
  for s in "${SECTIONS[@]}"; do SEC_TARGET[$s]=""; done
  if [ "$UNION" != "$COMMON" ]; then
    warn "This chart has no per-Ingress map, so only the UNION can be written."
    note "That widens access: every Ingress gets every CIDR anyone has."
    note "union   $UNION"
    note "common  ${COMMON:-<none>}"
    note "Update the chart (ingress.<name>.annotations) to keep them exact."
  fi
fi

hdr "Plan"
printf '  ingress.annotations           %s\n' "${SHARED_TARGET:-<unset>}"
for s in "${SECTIONS[@]}"; do
  printf '  ingress.%-22s%s\n' "$s.annotations" "${SEC_TARGET[$s]:-—}"
done
[ "$PER_INGRESS" = "1" ] && note "'—' means the Ingress uses the shared list as-is."

# ── Build the whole new file, then show one diff ────────────────────────────
NEW=$(mktemp); TMP=$(mktemp)
cp "$VALUES_FILE" "$NEW"

if [ -n "$SHARED_TARGET" ]; then
  if write_shared_ann "$NEW" "$KEY" "$SHARED_TARGET" > "$TMP" 2>/dev/null; then
    cp "$TMP" "$NEW"
  else
    rm -f "$NEW" "$TMP"
    die "Could not place $KEY. The values file has no active
     'alb.ingress.kubernetes.io/scheme' line to anchor the insert on —
     add the key by hand under ingress.annotations."
  fi
fi

if [ "$PER_INGRESS" = "1" ]; then
  for s in "${SECTIONS[@]}"; do
    if write_ingress_ann "$NEW" "$s" "$KEY" "${SEC_TARGET[$s]}" > "$TMP" 2>/dev/null; then
      cp "$TMP" "$NEW"
    else
      rm -f "$NEW" "$TMP"
      die "Could not write ingress.$s.annotations. Add it by hand:
     ingress:
       $s:
         annotations:
           alb.ingress.kubernetes.io/$KEY: \"${SEC_TARGET[$s]}\""
    fi
  done

  # The CloudFront prefix list — gateway only, never the shared map. Putting it
  # there would also open admin-api and admin-ui to any CloudFront distribution.
  PL_LIVE=$(ann_of "$ING_GATEWAY" "$PL_KEY")
  if [ -n "$PL_LIVE" ]; then
    if write_ingress_ann "$NEW" gateway "$PL_KEY" "$PL_LIVE" > "$TMP" 2>/dev/null; then
      cp "$TMP" "$NEW"
      printf '  ingress.gateway.annotations   %s = %s\n' "$PL_KEY" "$PL_LIVE"
    else
      rm -f "$NEW" "$TMP"
      die "Could not write the prefix list under ingress.gateway.annotations."
    fi
  fi
else
  PL_LIVE=$(ann_of "$ING_GATEWAY" "$PL_KEY")
  if [ -n "$PL_LIVE" ]; then
    hdr "Not persisted — this chart cannot hold it"
    bad "$PL_KEY = $PL_LIVE  (gateway Ingress, cluster-only)"
    cat <<EOF

  This is what lets CloudFront reach the gateway. This chart renders all three
  Ingresses from one shared annotations map, so writing it to values would also
  open admin-api and admin-ui to any CloudFront distribution.

  Consequence: after every 'helm upgrade' the rule is gone and every request
  through CloudFront returns 502. Re-apply it by hand with:

      bash 03-create-cloudfront.sh --allow-cloudfront

  The durable fix is the per-Ingress annotations map. Update the chart.
EOF
  fi
fi
rm -f "$TMP"

if diff -q "$VALUES_FILE" "$NEW" >/dev/null; then
  hdr "Result"
  ok "values already matches the cluster. Nothing to do."
  rm -f "$NEW"
  echo; exit 0
fi

hdr "Planned change (not applied yet)"
diff -u "$VALUES_FILE" "$NEW" | sed -n '3,$p' | sed 's/^/  /'

if [ "${1:-}" != "--apply" ]; then
  echo
  note "Nothing written."
  note "Apply:  bash $(basename "$0") --apply"
  rm -f "$NEW"; echo; exit 0
fi

confirm "Write the allow-lists into $VALUES_FILE (helm is NOT run)."

BAK="$SNAP_DIR/${TS}-06-$(basename "$VALUES_FILE")"
cp "$VALUES_FILE" "$BAK" || { rm -f "$NEW"; die "backup failed"; }
cp "$NEW" "$VALUES_FILE" || { rm -f "$NEW"; die "write failed"; }
rm -f "$NEW"
ok "written"
note "previous file: $BAK"

hdr "Next steps"
cat <<EOF
  Nothing was deployed. Verify the file with:
    grep -n 'alb.ingress.kubernetes.io' $VALUES_FILE | grep -v '#'

  Then apply it — always through install-eks.sh, never a bare 'helm upgrade
  -f values' (the file still holds placeholders that install-eks.sh fills from
  terraform output):
    ./deployment/scripts/install-eks.sh $DEPLOY_ENV

  This file holds account-specific values and is not committed, so it lives
  only on this machine — keep a copy somewhere safe.
EOF
echo
