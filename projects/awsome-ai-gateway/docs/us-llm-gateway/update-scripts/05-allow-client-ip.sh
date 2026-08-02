#!/bin/bash
# ---------------------------------------------------------------------------
# 05-allow-client-ip.sh
#
# WHAT: add or remove a client PC's public IP in the Ingress inbound-cidrs
#       annotation
# WHY:  gateway-cli login and api-key-helper talk to admin-api, which is IP
#       restricted. A client that is not on the list simply cannot obtain a VK.
#       (The data plane goes out through CloudFront, so this is purely about
#        the VK issuance path.)
# UNDO: --remove <cidr>
#
# Do NOT edit the security group directly — the AWS Load Balancer Controller
# reconciles SG rules against these annotations, so manual rules quietly
# disappear (observed on this stack).
#
# Usage:
#   bash 05-allow-client-ip.sh --show
#   bash 05-allow-client-ip.sh --add 203.0.113.10/32              # dry-run
#   bash 05-allow-client-ip.sh --add 203.0.113.10/32 --apply
#   bash 05-allow-client-ip.sh --remove 203.0.113.10/32 --apply
#   ... --targets admin-api,gateway   (default: admin-api only)
# ---------------------------------------------------------------------------
set -uo pipefail
source "$(dirname "$(readlink -f "$0")")/_lib.sh"

ANN="alb.ingress.kubernetes.io/inbound-cidrs"
ADD=""; REMOVE=""; APPLY=0; TARGETS="admin-api"

while [ $# -gt 0 ]; do
  case "$1" in
    --add)     ADD="$2";     shift 2 ;;
    --remove)  REMOVE="$2";  shift 2 ;;
    --targets) TARGETS="$2"; shift 2 ;;
    --apply)   APPLY=1;      shift ;;
    --show)    ADD=""; REMOVE=""; shift ;;
    -h|--help) sed -n '2,26p' "$0"; exit 0 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

require_env

ing_name() {
  case "$1" in
    gateway)   echo "$ING_GATEWAY" ;;
    admin-api) echo "$ING_ADMIN_API" ;;
    admin-ui)  echo "$ING_ADMIN_UI" ;;
    *) die "Unknown target: $1 (gateway|admin-api|admin-ui)" ;;
  esac
}

get_cidrs() {
  kubectl get ingress "$1" -n "$NS" \
    -o jsonpath="{.metadata.annotations.alb\\.ingress\\.kubernetes\\.io/inbound-cidrs}" 2>/dev/null
}

show_all() {
  hdr "Current inbound-cidrs"
  local t n c
  for t in gateway admin-api admin-ui; do
    n=$(ing_name "$t"); c=$(get_cidrs "$n")
    printf '  %-12s %s\n' "$t" "${c:-<none>}"
  done
  echo
  note "The controller reconciles the SG from these values — editing the SG directly gets reverted"
}

[ -z "$ADD" ] && [ -z "$REMOVE" ] && { show_all; exit 0; }

CIDR="${ADD:-$REMOVE}"
[[ "$CIDR" =~ ^[0-9.]+/[0-9]+$ ]] || die "Not a CIDR: $CIDR  (example: 203.0.113.10/32)"

show_all

hdr "Planned change"
IFS=',' read -ra TLIST <<< "$TARGETS"
declare -A NEWVAL
for t in "${TLIST[@]}"; do
  n=$(ing_name "$t")
  cur=$(get_cidrs "$n")
  if [ -n "$ADD" ]; then
    if grep -qF "$CIDR" <<<"$cur"; then
      note "$t : already present — no change"
      continue
    fi
    NEWVAL[$n]="${cur:+$cur,}$CIDR"
    printf '  %-12s %s\n              -> %s\n' "$t" "${cur:-<none>}" "${NEWVAL[$n]}"
  else
    if ! grep -qF "$CIDR" <<<"$cur"; then
      note "$t : not present — no change"
      continue
    fi
    nv=$(tr ',' '\n' <<<"$cur" | grep -vxF "$CIDR" | paste -sd, -)
    # Removing the last CIDR would lock everyone out of this ingress.
    [ -z "$nv" ] && die "$t : removing the last CIDR would block all access. Stopping."
    NEWVAL[$n]="$nv"
    printf '  %-12s %s\n              -> %s\n' "$t" "$cur" "$nv"
  fi
done

[ ${#NEWVAL[@]} -eq 0 ] && { echo; ok "Nothing to change."; exit 0; }

cat <<EOF

Persistence
   These Ingresses belong to helm release '$HELM_RELEASE'. Values set via
   kubectl are lost on the next helm upgrade. To make it permanent, update
   ingress.annotations."$ANN" in values as well.
EOF

if [ "$APPLY" -eq 0 ]; then
  echo
  echo "  Nothing applied yet.  To apply: re-run the same command with --apply"
  exit 0
fi

confirm "Update inbound-cidrs on the Ingresses listed above."

hdr "Applying"
for n in "${!NEWVAL[@]}"; do
  old=$(get_cidrs "$n")
  printf '%s\t%s\n' "$n" "$old" >> "$SNAP_DIR/${TS}-05-inbound-cidrs-rollback.txt"
  kubectl annotate ingress "$n" -n "$NS" "$ANN=${NEWVAL[$n]}" --overwrite >/dev/null \
    || die "Failed to annotate $n"
  ok "$n"
done

# Confirm the controller propagated the change to the actual SG. Resolve the
# SG from whichever ingress we just touched, rather than assuming a name.
FIRST_ING="${!NEWVAL[*]}"; FIRST_ING="${FIRST_ING%% *}"
CHECK_DNS=$(kubectl get ingress "$FIRST_ING" -n "$NS" \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null)
CHECK_SG=$(aws elbv2 describe-load-balancers \
  --query "LoadBalancers[?DNSName=='$CHECK_DNS'].SecurityGroups[0]" --output text 2>/dev/null)
hdr "Waiting for the controller to reconcile (up to 90s)"
TARGET_IP="${CIDR}"
for i in $(seq 1 18); do
  hit=$(aws ec2 describe-security-group-rules \
        --filters "Name=group-id,Values=$CHECK_SG" \
        --query "SecurityGroupRules[?CidrIpv4=='$TARGET_IP'].CidrIpv4" --output text 2>/dev/null)
  if [ -n "$ADD" ] && [ -n "$hit" ]; then ok "confirmed in the SG"; break; fi
  if [ -n "$REMOVE" ] && [ -z "$hit" ]; then ok "confirmed removed from the SG"; break; fi
  printf '\r  waited %2d0s...' "$i"; sleep 5
done
echo
show_all
