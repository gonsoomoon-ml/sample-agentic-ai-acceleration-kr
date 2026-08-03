#!/bin/bash
# ---------------------------------------------------------------------------
# 07-client-values.sh
#
# WHAT: print the four env values an employee needs, ready to paste
# WHY:  the employee cannot look these up — the Cognito ids come from
#       terraform state and the two URLs from cluster objects, neither of
#       which they can reach. Handing them over by hand is where typos and
#       stale endpoints come from.
# UNDO: read-only — changes nothing
#
# Usage:
#   bash 07-client-values.sh              # Cowork (CloudFront https) — default
#   bash 07-client-values.sh --claude-code  # Claude Code (gateway ALB http)
# ---------------------------------------------------------------------------
set -uo pipefail
source "$(dirname "$(readlink -f "$0")")/_lib.sh"

TARGET="cowork"
case "${1:-}" in
  --claude-code) TARGET="claude-code" ;;
  --cowork|"")   TARGET="cowork" ;;
  -h|--help)     sed -n '2,15p' "$0"; exit 0 ;;
  *) echo "Unknown argument: $1"; exit 1 ;;
esac

require_env

# ── Cognito — terraform state is the only place these live ──────────────────
# TF_DIR is set in config.env; blank falls back to the standard repo layout.
TFD="${TF_DIR:-$LIB_DIR/../../../deployment/terraform/environments/llm-gateway-${DEPLOY_ENV}}"
[ -d "$TFD" ] || die "terraform directory not found: $TFD
     Set TF_DIR in config.env if your deployment lives elsewhere."

ISSUER=$(terraform -chdir="$TFD" output -raw cognito_issuer_url 2>/dev/null)
CLIENT=$(terraform -chdir="$TFD" output -raw cognito_client_id  2>/dev/null)
[ -n "$ISSUER" ] && [ -n "$CLIENT" ] || die "could not read cognito outputs from $TFD
     Run 'terraform init' there first, or read them from the Cognito console."

# ── The two endpoints ───────────────────────────────────────────────────────
ADMIN_ALB=$(kubectl get ingress "$ING_ADMIN_API" -n "$NS" \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null)
[ -n "$ADMIN_ALB" ] || die "admin-api Ingress '$ING_ADMIN_API' has no load balancer yet"

# GW_ALB_DNS is resolved by _lib.sh from the gateway Ingress.
# Same lookup 03 uses, so both agree on which distribution is ours.
CF_DOMAIN=$(aws cloudfront list-distributions \
  --query "DistributionList.Items[?Origins.Items[0].DomainName=='$GW_ALB_DNS'].DomainName" \
  --output text 2>/dev/null | head -1)
CF_STATUS=$(aws cloudfront list-distributions \
  --query "DistributionList.Items[?Origins.Items[0].DomainName=='$GW_ALB_DNS'].Status" \
  --output text 2>/dev/null | head -1)

if [ "$TARGET" = "cowork" ]; then
  # Cowork refuses a plain-http base URL, so this must be the distribution.
  [ -n "$CF_DOMAIN" ] && [ "$CF_DOMAIN" != "None" ] || die "no CloudFront distribution fronts $GW_ALB_DNS.
     Run: bash 03-create-cloudfront.sh --create"
  BASE_URL="https://$CF_DOMAIN"
else
  BASE_URL="http://$GW_ALB_DNS"
fi

echo
printf '%s Values to hand to the employee — target: %s%s\n' "$c_bold" "$TARGET" "$c_reset"

hdr "Windows (PowerShell) — paste into the employee's shell"
cat <<EOF
  \$env:OIDC_ISSUER_URL="$ISSUER"
  \$env:OIDC_CLIENT_ID="$CLIENT"
  \$env:ADMIN_API_URL="http://$ADMIN_ALB"
  \$env:ANTHROPIC_BASE_URL="$BASE_URL"
EOF

hdr "macOS / Linux"
cat <<EOF
  export OIDC_ISSUER_URL="$ISSUER"
  export OIDC_CLIENT_ID="$CLIENT"
  export ADMIN_API_URL="http://$ADMIN_ALB"
  export ANTHROPIC_BASE_URL="$BASE_URL"
EOF

hdr "Before you send them"
if [ "$TARGET" = "cowork" ]; then
  if [ "$CF_STATUS" = "Deployed" ]; then
    ok "CloudFront $CF_DOMAIN is Deployed"
  else
    warn "CloudFront status is '${CF_STATUS:-unknown}' — wait for Deployed (5-15 min)"
  fi
  note "onboard-windows.ps1 GETs \$ANTHROPIC_BASE_URL/health, so a CloudFront"
  note "that cannot reach its origin fails the employee's onboarding, not ours."
  note "That path needs: bash 03-create-cloudfront.sh --allow-cloudfront"
fi
cat <<EOF

  ADMIN_API_URL is IP restricted. The employee's public IP must be in the
  allow-list or VK issuance times out after a successful login:

      bash 05-allow-client-ip.sh --add <their-public-IP>/32 --apply

  Ask them for it — on their PC:  (irm https://checkip.amazonaws.com).Trim()

  The model list for Cowork's inferenceModels comes from:
      bash 00-preflight-check.sh        (the ACTIVE alias list)

  Send with the values: the guide, plus a Cognito email + temporary password.
EOF
