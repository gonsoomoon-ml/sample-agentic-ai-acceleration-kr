# ---------------------------------------------------------------------------
# https-env.sh — `source` me. Exports every value the ALB-HTTPS procedure
#                (US-06, docs/us-llm-gateway/ops/8-H-alb-https.md) needs, read
#                from config.env, the cluster and AWS — the operator types the
#                domain and nothing else.
#
#   source https-env.sh <domain>          # first time (e.g. mygw.click) — also saved to config.env as HTTPS_DOMAIN
#   source https-env.sh                   # later: restores from config.env; re-run any time (new shell, after
#                                         #        step 1 created the zone/cert) — every 8-H block starts with it
#   source https-env.sh -q                # same, without the summary table
#
# Values that do not exist yet print as "(none yet)" — that is expected before
# the matching step (ZONE_ID/CERT_ARN appear after 8-H step 1, GW_HOST after step 3).
# Sourced, not executed: no set -e / exit here — a failure must not kill your shell.
# ---------------------------------------------------------------------------
_HE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_HE_CFG="$_HE_DIR/config.env"
if [ ! -f "$_HE_CFG" ]; then
  echo "https-env: $_HE_CFG not found — copy config.env.example to config.env first" >&2
else
  # config.env is plain KEY="value" lines; export them all
  set -a; . "$_HE_CFG"; set +a
fi

_HE_Q=0; _HE_ARG=""
for _a in "$@"; do case "$_a" in -q|--quiet) _HE_Q=1 ;; *) _HE_ARG="$_a" ;; esac; done
if [ -n "$_HE_ARG" ]; then
  DOMAIN="$_HE_ARG"
  # remember it, so later `source https-env.sh` (no arg) restores the same domain
  if [ -f "$_HE_CFG" ] && [ "${HTTPS_DOMAIN:-}" != "$DOMAIN" ]; then
    if grep -q '^HTTPS_DOMAIN=' "$_HE_CFG"; then
      sed -i "s|^HTTPS_DOMAIN=.*|HTTPS_DOMAIN=\"$DOMAIN\"|" "$_HE_CFG"
    else
      printf '\n# set by https-env.sh (8-H)\nHTTPS_DOMAIN="%s"\n' "$DOMAIN" >> "$_HE_CFG"
    fi
    [ "$_HE_Q" = 1 ] || echo "https-env: saved HTTPS_DOMAIN=$DOMAIN to config.env"
  fi
fi
DOMAIN="${DOMAIN:-${HTTPS_DOMAIN:-}}"
if [ -z "$DOMAIN" ]; then
  echo "https-env: 먼저  source https-env.sh <domain>   (8-H §0-2, e.g. mygw.click)" >&2
else
  export DOMAIN
  export ENV="${DEPLOY_ENV:-dev}"
  export NS="${K8S_NAMESPACE:-llm-gateway}"
  export REL="${HELM_RELEASE:-llm-gateway}"
  export REGION="${AWS_REGION:-$(aws configure get region 2>/dev/null)}"
  export ACCOUNT_ID="${AWS_ACCOUNT_ID:-$(aws sts get-caller-identity --query Account --output text 2>/dev/null)}"

  export GW_HOST_TARGET="gateway-$ENV.$DOMAIN"
  export UI_HOST_TARGET="admin-$ENV.$DOMAIN"
  export API_HOST_TARGET="admin-api-$ENV.$DOMAIN"

  _z=$(aws route53 list-hosted-zones-by-name --dns-name "$DOMAIN" \
        --query "HostedZones[?Name=='${DOMAIN}.'].Id | [0]" --output text 2>/dev/null | sed 's#/hostedzone/##')
  [ "$_z" = "None" ] && _z=""; export ZONE_ID="$_z"

  _c=$(aws acm list-certificates --region "$REGION" \
        --query "CertificateSummaryList[?DomainName=='*.${DOMAIN}'].CertificateArn | [0]" --output text 2>/dev/null)
  [ "$_c" = "None" ] && _c=""; export CERT_ARN="$_c"
  _cs=""; [ -n "$CERT_ARN" ] && _cs=$(aws acm describe-certificate --region "$REGION" --certificate-arn "$CERT_ARN" \
        --query Certificate.Status --output text 2>/dev/null)

  export GW_DNS=$(kubectl get ingress "$REL-gateway" -n "$NS" -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null)
  export GW_HOST=$(kubectl get ingress "$REL-gateway" -n "$NS" -o jsonpath='{.spec.rules[0].host}' 2>/dev/null)
  export GW_SG=""
  [ -n "$GW_DNS" ] && GW_SG=$(aws elbv2 describe-load-balancers \
        --query "LoadBalancers[?DNSName=='$GW_DNS'].SecurityGroups[0]" --output text 2>/dev/null)
  _rules=""; [ -n "$GW_SG" ] && _rules=$(aws ec2 describe-security-group-rules --filters "Name=group-id,Values=$GW_SG" \
        --query 'length(SecurityGroupRules[?IsEgress==`false`])' --output text 2>/dev/null)

  _p() { [ "$_HE_Q" = 1 ] || printf '  %-16s %s\n' "$1" "${2:-(none yet)}"; }
  [ "$_HE_Q" = 1 ] || echo "https-env — exported:"
  _p DOMAIN "$DOMAIN"; _p ENV "$ENV"; _p NS "$NS"; _p REL "$REL"; _p REGION "$REGION"; _p ACCOUNT_ID "$ACCOUNT_ID"
  _p ZONE_ID "$ZONE_ID"; _p CERT_ARN "${CERT_ARN:+$CERT_ARN (${_cs:-?})}"
  _p GW_DNS "$GW_DNS"; _p GW_HOST "${GW_HOST:-(none yet — 방식 A)}"; _p GW_SG "${GW_SG:+$GW_SG (inbound rules: ${_rules:-?})}"
  _p "targets" "$GW_HOST_TARGET · $UI_HOST_TARGET · $API_HOST_TARGET"
  unset _z _c _cs _rules _p
fi
unset _HE_DIR _HE_CFG _HE_Q _HE_ARG _a
