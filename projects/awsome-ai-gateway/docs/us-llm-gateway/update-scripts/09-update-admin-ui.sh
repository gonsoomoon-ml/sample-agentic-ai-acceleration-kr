#!/bin/bash
# ---------------------------------------------------------------------------
# 09-update-admin-ui.sh
#
# WHAT: build the admin-ui image from this checkout, push it to ECR, and roll
#       the running Deployment onto it
# WHY:  upstream merged full ko/en i18n (PR #43) — the header KO/EN toggle now
#       actually translates the screens. Getting it into the cluster needs a
#       new image; the repo alone changes nothing.
# UNDO: bash 09-update-admin-ui.sh --rollback   (uses the snapshot below)
#
# ⚠️ This is the FALLBACK path, not the preferred one.
#
# `kubectl set image` leaves helm's stored release pointing at the old tag, so
# the declared state and the cluster disagree until someone runs helm again.
# Use it only where `helm upgrade` is unsafe: charts without per-Ingress
# annotations (`ingress.gateway.annotations`) cannot hold the gateway's
# security-group-prefix-lists, so an upgrade drops it and every request through
# CloudFront returns 502 — the data plane goes down for a dashboard change.
#
# If the chart HAS the per-Ingress map and `06-persist-annotations.sh` has been
# run, prefer:
#     helm upgrade <release> <chart> -f <values> --set adminUi.image.tag=<tag>
# The script warns when it detects that case.
#
# Either way the values file is updated, so the next `helm upgrade` carries the
# same tag instead of reverting the rollout.
# ---------------------------------------------------------------------------
set -uo pipefail
source "$(dirname "$(readlink -f "$0")")/_lib.sh"

SUFFIX="i18n"
TAG=""
MODE="dryrun"

while [ $# -gt 0 ]; do
  case "$1" in
    --apply)    MODE="apply" ;;
    --status)   MODE="status" ;;
    --rollback) MODE="rollback" ;;
    --tag)      TAG="${2:-}"; shift ;;
    --suffix)   SUFFIX="${2:-}"; shift ;;
    *) die "unknown argument: $1
     usage: $(basename "$0") [--tag <tag>] [--suffix <word>] [--apply|--status|--rollback]" ;;
  esac
  shift
done

require_env

SVC="admin-ui"
DEPLOY="${HELM_RELEASE}-${SVC}"
CONTAINER="admin-ui"
BUILD_CTX="$LIB_DIR/../../../${SVC}"
ECR_BASE="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/llm-gateway"
VALUES_FILE="${HELM_VALUES_FILE:-$LIB_DIR/../../../deployment/charts/llm-gateway/values-eks-fargate-${DEPLOY_ENV}.yaml}"

[ -d "$BUILD_CTX" ] || die "build context not found: $BUILD_CTX"
[ -f "$VALUES_FILE" ] || die "values file not found: $VALUES_FILE"
BUILD_CTX=$(readlink -f "$BUILD_CTX"); VALUES_FILE=$(readlink -f "$VALUES_FILE")

live_image() {
  kubectl get deploy "$DEPLOY" -n "$NS" \
    -o jsonpath="{.spec.template.spec.containers[?(@.name=='$CONTAINER')].image}" 2>/dev/null
}

# The tag helm would apply. Anchored to the adminUi block so the many other
# `tag:` lines in this file cannot match.
values_tag() {
  awk '/^adminUi:/{inblk=1} inblk && /^[a-zA-Z]/ && !/^adminUi:/{inblk=0}
       inblk && /^[[:space:]]*tag:[[:space:]]/ {
         sub(/^[^:]*:[[:space:]]*/, ""); sub(/[[:space:]]*#.*$/, "");
         gsub(/"/, ""); print; exit }' "$VALUES_FILE"
}

LIVE=$(live_image)
LIVE_TAG="${LIVE##*:}"
VTAG=$(values_tag)

# Default tag: bump the patch of whatever is deployed and mark why it changed.
# Derived rather than hardcoded so the script survives future versions.
if [ -z "$TAG" ]; then
  base="${LIVE_TAG%%-*}"
  if [[ "$base" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
    TAG="${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.$(( BASH_REMATCH[3] + 1 ))-${SUFFIX}"
  else
    die "Cannot derive a tag from the running image ('$LIVE_TAG').
     Pass one explicitly:  bash $(basename "$0") --tag <tag> --apply"
  fi
fi
NEW_IMAGE="$ECR_BASE/$SVC:$TAG"

echo
printf '%s admin-ui — rebuild and roll out%s\n' "$c_bold" "$c_reset"
hdr "Current state"
printf '  deployment       %s (ns %s)\n' "$DEPLOY" "$NS"
printf '  running image    %s\n' "${LIVE:-<not found>}"
printf '  values tag       %s\n' "${VTAG:-<not found>}"
printf '  build context    %s\n' "$BUILD_CTX"
printf '  new image        %s\n' "$NEW_IMAGE"

# The reason this script avoids helm — report it every run, not just on failure.
hdr "Is helm safe here? (what values holds vs what the cluster runs)"
PL=$(kubectl get ingress "$ING_GATEWAY" -n "$NS" \
     -o jsonpath='{.metadata.annotations.alb\.ingress\.kubernetes\.io/security-group-prefix-lists}' 2>/dev/null)
if [ -n "$PL" ]; then
  warn "gateway prefix-list  $PL   (cluster-only — this is the CloudFront path)"
else
  ok "no prefix-list set on $ING_GATEWAY"
fi
LIVE_CIDRS=$(kubectl get ingress "$ING_ADMIN_UI" -n "$NS" \
             -o jsonpath='{.metadata.annotations.alb\.ingress\.kubernetes\.io/inbound-cidrs}' 2>/dev/null)
FILE_CIDRS=$(grep -E "^[[:space:]]*alb\.ingress\.kubernetes\.io/inbound-cidrs:[[:space:]]" "$VALUES_FILE" \
             | grep -vE '^[[:space:]]*#' | head -1 | sed -E 's/^[^:]*:[[:space:]]*//; s/^"//; s/"[[:space:]]*$//')
printf '  live  inbound-cidrs  %s\n' "${LIVE_CIDRS:-<unset>}"
printf '  file  inbound-cidrs  %s\n' "${FILE_CIDRS:-<unset>}"

# Is helm actually unsafe here, or is this script just the habit? Decide from
# the chart and the values file, and say which, rather than assuming the worst.
ING_TPL="$LIB_DIR/../../../deployment/charts/llm-gateway/templates/common/ingress.yaml"
PER_INGRESS=0
[ -f "$ING_TPL" ] && grep -q 'Values\.ingress\.gateway\.annotations' "$ING_TPL" && PER_INGRESS=1
PL_IN_FILE=0
grep -qE '^[[:space:]]*alb\.ingress\.kubernetes\.io/security-group-prefix-lists:' "$VALUES_FILE" \
  && PL_IN_FILE=1

if [ "$LIVE_CIDRS" != "$FILE_CIDRS" ] || { [ -n "$PL" ] && [ "$PL_IN_FILE" = "0" ]; }; then
  warn "values does not yet hold everything the cluster has"
  note "A 'helm upgrade' right now would drop the difference. Fix it with:"
  note "  bash 06-persist-annotations.sh --apply"
  note "This script does not need it — kubectl set image ignores Ingresses."
elif [ "$PER_INGRESS" = "1" ]; then
  ok "values already holds them, and this chart has per-Ingress annotations"
  warn "so 'helm upgrade' is safe here — prefer it over this script"
  note "  helm upgrade $HELM_RELEASE <chart> -f $(basename "$VALUES_FILE") \\"
  note "       --set adminUi.image.tag=$TAG"
  note "kubectl set image leaves helm's stored release stale. Continue only if"
  note "you specifically want to avoid re-rendering the other resources."
fi

# ── --status ────────────────────────────────────────────────────────────────
if [ "$MODE" = "status" ]; then
  hdr "Rollout"
  kubectl get deploy "$DEPLOY" -n "$NS" -o wide 2>/dev/null
  kubectl get pods -n "$NS" -l "app.kubernetes.io/name=$SVC" 2>/dev/null | head -5
  echo; exit 0
fi

# ── --rollback ──────────────────────────────────────────────────────────────
if [ "$MODE" = "rollback" ]; then
  SNAP=$(ls -1t "$SNAP_DIR"/*-09-admin-ui.json 2>/dev/null | head -1)
  [ -n "$SNAP" ] || die "No snapshot to roll back to (looked for $SNAP_DIR/*-09-admin-ui.json)."
  PREV=$(jq -r '.image' "$SNAP")
  hdr "Rollback"
  printf '  snapshot   %s\n' "$SNAP"
  printf '  restore to %s\n' "$PREV"
  confirm "Roll $DEPLOY back to the image above."
  kubectl set image "deploy/$DEPLOY" "$CONTAINER=$PREV" -n "$NS" \
    || die "kubectl set image failed"
  kubectl rollout status "deploy/$DEPLOY" -n "$NS" --timeout=5m
  note "The values file was not reverted — check its adminUi tag by hand if the"
  note "rollback is meant to be permanent."
  echo; exit 0
fi

# ── dry-run ─────────────────────────────────────────────────────────────────
hdr "What --apply would do"
cat <<EOF
  1. snapshot   $SNAP_DIR/${TS}-09-admin-ui.json  (+ a copy of the values file)
  2. build      docker build --platform linux/amd64 -t $NEW_IMAGE
                  $BUILD_CTX
                (this is also the typecheck — 'next build' fails on TS errors)
  3. push       $NEW_IMAGE
  4. values     adminUi.image.tag: "$VTAG"  ->  "$TAG"
  5. rollout    kubectl set image deploy/$DEPLOY $CONTAINER=$NEW_IMAGE
                (helm is NOT run — see the header of this script)
EOF

if [ "$MODE" != "apply" ]; then
  echo
  note "Nothing was changed."
  note "Apply:  bash $(basename "$0") --apply"
  echo; exit 0
fi

# ── --apply ─────────────────────────────────────────────────────────────────
[ -n "$LIVE" ] || die "Could not read the running image for $DEPLOY — refusing to
     proceed, because there would be nothing to roll back to."

if [ "$LIVE_TAG" = "$TAG" ]; then
  die "The running image is already tagged '$TAG'.
     Pushing over a tag that pods already run does not restart them and makes
     the rollback snapshot meaningless. Pick another tag with --tag."
fi

confirm "Build $SVC, push it as $TAG, and roll $DEPLOY onto it."

SNAP="$SNAP_DIR/${TS}-09-admin-ui.json"
jq -n --arg img "$LIVE" --arg dep "$DEPLOY" --arg ns "$NS" --arg vtag "$VTAG" \
      '{image:$img, deployment:$dep, namespace:$ns, values_tag:$vtag}' > "$SNAP" \
  || die "could not write snapshot"
cp "$VALUES_FILE" "$SNAP_DIR/${TS}-09-$(basename "$VALUES_FILE")"
ok "snapshot  $SNAP"

hdr "1/3  Build"
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com" \
  || die "ECR login failed"
docker build --platform linux/amd64 -t "$NEW_IMAGE" "$BUILD_CTX" \
  || die "Build failed. Nothing was pushed and the cluster is untouched.
     A TypeScript error here is a real failure — 'next build' typechecks."
ok "built $NEW_IMAGE"

hdr "2/3  Push"
docker push "$NEW_IMAGE" || die "Push failed. The cluster is untouched."
ok "pushed"

hdr "3/3  Roll out"
# Values first: if the rollout is interrupted, the file already agrees with the
# image, so a later helm upgrade converges instead of reverting.
python3 - "$VALUES_FILE" "$TAG" "$LIVE_TAG" <<'PY' || die "could not update the values file"
import re, sys
path, tag, prev = sys.argv[1], sys.argv[2], sys.argv[3]
lines = open(path, encoding='utf-8').read().split('\n')
inblk = done = False
for i, l in enumerate(lines):
    if l.startswith('adminUi:'):
        inblk = True; continue
    if inblk and re.match(r'^[a-zA-Z]', l):
        break
    if inblk and re.match(r'^\s*tag:\s', l):
        ind = re.match(r'^\s*', l).group(0)
        # The old trailing comment described the old build. Keeping it on a new
        # tag is worse than having none, so it is replaced rather than carried.
        lines[i] = f'{ind}tag: "{tag}"   # 09-update-admin-ui.sh 가 설정 (이전 {prev})'
        done = True; break
if not done:
    sys.exit('no adminUi.image.tag line found')
open(path, 'w', encoding='utf-8').write('\n'.join(lines))
PY
ok "values tag -> $TAG"

kubectl set image "deploy/$DEPLOY" "$CONTAINER=$NEW_IMAGE" -n "$NS" \
  || die "kubectl set image failed. The image is in ECR and the values file is
     updated, so re-running this script with --tag $TAG is safe."
kubectl rollout status "deploy/$DEPLOY" -n "$NS" --timeout=10m \
  || die "Rollout did not become ready. Roll back with:
     bash $(basename "$0") --rollback"
ok "rolled out"

hdr "Result"
printf '  running image  %s\n' "$(live_image)"
kubectl get pods -n "$NS" -l "app.kubernetes.io/name=$SVC" 2>/dev/null | head -5

hdr "Next steps"
cat <<EOF
  Open the admin UI and click the globe / KO-EN button in the header. Screens
  that upstream converted (dashboard, models, keys, users, budgets, analytics,
  monitoring, rate limits) switch to English.

  Still Korean regardless of the toggle — upstream has not converted them:
    the CLI download page, the Chat screens, My usage, the 403/error pages.
  Say so before showing this to a customer.

  Roll back:   bash $(basename "$0") --rollback
  Not done:    the values file holds account values and is not committed —
               it lives only on this machine.
EOF
echo
