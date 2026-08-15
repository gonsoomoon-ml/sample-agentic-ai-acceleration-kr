#!/bin/bash
# ---------------------------------------------------------------------------
# 11-route53-cname.sh
#
# WHAT: point the three Ingress hostnames (gateway / admin-ui / admin-api) at
#       their ALB DNS names with CNAME records in the Route 53 hosted zone —
#       US-06 step after install-eks.sh (docs/us-llm-gateway/ops/8-H-alb-https.md)
# WHY:  the hostnames come from the Ingress objects and the ALB DNS from their
#       status; copying either by hand is where a typo or a stale ALB name
#       ends up in DNS.
# UNDO: --delete (removes the same three records)
#
# Usage:
#   bash 11-route53-cname.sh                       # dry-run: show planned records
#   bash 11-route53-cname.sh --apply               # UPSERT, wait INSYNC, dig check
#   bash 11-route53-cname.sh --delete --apply      # remove the three records
#   ... --domain example.com   (or HTTPS_DOMAIN in config.env; else derived from the gateway host)
#   ... --zone-id Z0123...     (skip the lookup)
# ---------------------------------------------------------------------------
set -uo pipefail
source "$(dirname "$(readlink -f "$0")")/_lib.sh"

DOMAIN="${HTTPS_DOMAIN:-}"; ZONE_ID=""; APPLY=0; ACTION="UPSERT"
while [ $# -gt 0 ]; do
  case "$1" in
    --domain)  DOMAIN="$2"; shift 2 ;;
    --zone-id) ZONE_ID="$2"; shift 2 ;;
    --delete)  ACTION="DELETE"; shift ;;
    --apply)   APPLY=1; shift ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
    *) die "Unknown argument: $1" ;;
  esac
done

require_env

# host + ALB DNS per Ingress, straight from the cluster
declare -a NAMES=() TARGETS=()
for ing in "$ING_GATEWAY" "$ING_ADMIN_UI" "$ING_ADMIN_API"; do
  h=$(kubectl get ingress "$ing" -n "$NS" -o jsonpath='{.spec.rules[0].host}' 2>/dev/null)
  d=$(kubectl get ingress "$ing" -n "$NS" -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null)
  [ -n "$h" ] || die "Ingress $ing has no host — run 10-switch-https.sh + install-eks.sh first"
  [ -n "$d" ] || die "Ingress $ing has no ALB yet — wait for the controller (kubectl get ingress -n $NS)"
  NAMES+=("$h"); TARGETS+=("$d")
done

# hosted zone: explicit id, else by domain (config/--domain), else derive from
# the gateway host by dropping its first label (gateway-dev.example.com → example.com)
[ -n "$DOMAIN" ] || DOMAIN="${NAMES[0]#*.}"
if [ -z "$ZONE_ID" ]; then
  ZONE_ID=$(aws route53 list-hosted-zones-by-name --dns-name "$DOMAIN" \
    --query "HostedZones[?Name=='${DOMAIN}.'].Id | [0]" --output text 2>/dev/null | sed 's#/hostedzone/##')
  [ -n "$ZONE_ID" ] && [ "$ZONE_ID" != "None" ] || die "no hosted zone for $DOMAIN in this account.
     Register the domain here, or delegate it (ops/8-H-alb-https.md 1-②-보충), or pass --zone-id."
fi

hdr "Planned records  (zone $ZONE_ID · $DOMAIN · $ACTION)"
CHANGES=""
for i in 0 1 2; do
  printf '  %-36s CNAME  %s\n' "${NAMES[$i]}" "${TARGETS[$i]}"
  CHANGES="$CHANGES{\"Action\":\"$ACTION\",\"ResourceRecordSet\":{\"Name\":\"${NAMES[$i]}\",\"Type\":\"CNAME\",\"TTL\":300,\"ResourceRecords\":[{\"Value\":\"${TARGETS[$i]}\"}]}},"
done
CHANGES="{\"Changes\":[${CHANGES%,}]}"

if [ "$APPLY" = 0 ]; then
  echo; note "Nothing changed."; note "Apply:  bash $(basename "$0") $* --apply"; echo; exit 0
fi

confirm "$ACTION these 3 CNAME records in hosted zone $ZONE_ID."
CHANGE_ID=$(aws route53 change-resource-record-sets --hosted-zone-id "$ZONE_ID" \
  --change-batch "$CHANGES" --query 'ChangeInfo.Id' --output text) || die "change-resource-record-sets failed"
ok "submitted ($CHANGE_ID)"
printf '     waiting for INSYNC'
for _ in $(seq 1 30); do
  st=$(aws route53 get-change --id "$CHANGE_ID" --query 'ChangeInfo.Status' --output text 2>/dev/null)
  [ "$st" = "INSYNC" ] && break; printf '.'; sleep 5
done
echo; [ "$st" = "INSYNC" ] && ok "INSYNC" || warn "still $st — records will propagate shortly"

if [ "$ACTION" = "UPSERT" ] && command -v dig >/dev/null 2>&1; then
  hdr "Resolve check"
  for i in 0 1 2; do
    got=$(dig +short "${NAMES[$i]}" @8.8.8.8 2>/dev/null | head -1)
    case "$got" in
      "${TARGETS[$i]}"*) ok "${NAMES[$i]} → $got" ;;
      "")                warn "${NAMES[$i]} not resolving yet (TTL/propagation) — retry: dig +short ${NAMES[$i]}" ;;
      *)                 warn "${NAMES[$i]} → $got (expected ${TARGETS[$i]})" ;;
    esac
  done
  echo; note "next: curl -sI https://${NAMES[0]}/health | head -1   (expect HTTP/2 200)"
fi
