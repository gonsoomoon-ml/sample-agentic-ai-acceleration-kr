#!/bin/bash
# ---------------------------------------------------------------------------
# 03-create-cloudfront.sh
#
# WHAT: put CloudFront in front of the gateway-proxy ALB to obtain an https
#       base URL
# WHY:  Cowork requires inferenceGatewayBaseUrl to be https://. When the ALB
#       listens on HTTP only and the account owns no ACM certificate or public
#       hosted zone, CloudFront is the only way to get https without first
#       acquiring a domain (*.cloudfront.net certificate comes free).
#       NOT needed once US-06 (ops/8-H-alb-https.md: custom domain + ACM on the
#       ALB) is applied — the ALB then serves https itself.
# UNDO: 99-rollback.sh — disable then delete the distribution, revert the
#       ingress annotation
#
# The DistributionConfig below is a field-by-field copy of a deployment already
# proven to carry LLM traffic. Set REFERENCE_CF_DIST_ID in config.env to diff
# against your own reference deployment.
#
# Usage:
#   bash 03-create-cloudfront.sh                    # dry-run, print config
#   bash 03-create-cloudfront.sh --create           # create the distribution
#   bash 03-create-cloudfront.sh --allow-cloudfront # let CloudFront reach the origin (required)
#   bash 03-create-cloudfront.sh --status           # current state
#   bash 03-create-cloudfront.sh --diff             # compare against REFERENCE_CF_DIST_ID
# ---------------------------------------------------------------------------
set -uo pipefail
source "$(dirname "$(readlink -f "$0")")/_lib.sh"

ORIGIN_ID="gateway-proxy-alb"
COMMENT="LLM Gateway - Cowork HTTPS entrypoint"
CACHE_POLICY="4135ea2d-6df8-44a3-9df3-4b5a84be39ad"      # CachingDisabled (managed)
ORIGIN_REQ_POLICY="b689b0a8-53d0-40ab-baf2-68738e2966ac" # AllViewerExceptHostHeader (managed)

CFG_FILE="$LIB_DIR/.cf-config.json"

# ── Full DistributionConfig — every field explicit, no reliance on defaults ──
write_config() {
  cat > "$CFG_FILE" <<JSON
{
  "CallerReference": "cowork-gateway-$(date +%s)",
  "Aliases": { "Quantity": 0 },
  "DefaultRootObject": "",
  "Origins": {
    "Quantity": 1,
    "Items": [
      {
        "Id": "$ORIGIN_ID",
        "DomainName": "$GW_ALB_DNS",
        "OriginPath": "",
        "CustomHeaders": { "Quantity": 0 },
        "CustomOriginConfig": {
          "HTTPPort": 80,
          "HTTPSPort": 443,
          "OriginProtocolPolicy": "http-only",
          "OriginSslProtocols": { "Quantity": 1, "Items": ["TLSv1.2"] },
          "OriginReadTimeout": 60,
          "OriginKeepaliveTimeout": 60
        },
        "ConnectionAttempts": 3,
        "ConnectionTimeout": 10,
        "OriginShield": { "Enabled": false },
        "OriginAccessControlId": ""
      }
    ]
  },
  "OriginGroups": { "Quantity": 0 },
  "DefaultCacheBehavior": {
    "TargetOriginId": "$ORIGIN_ID",
    "TrustedSigners": { "Enabled": false, "Quantity": 0 },
    "TrustedKeyGroups": { "Enabled": false, "Quantity": 0 },
    "ViewerProtocolPolicy": "redirect-to-https",
    "AllowedMethods": {
      "Quantity": 7,
      "Items": ["HEAD", "DELETE", "POST", "GET", "OPTIONS", "PUT", "PATCH"],
      "CachedMethods": { "Quantity": 2, "Items": ["HEAD", "GET"] }
    },
    "SmoothStreaming": false,
    "Compress": false,
    "LambdaFunctionAssociations": { "Quantity": 0 },
    "FunctionAssociations": { "Quantity": 0 },
    "FieldLevelEncryptionId": "",
    "CachePolicyId": "$CACHE_POLICY",
    "OriginRequestPolicyId": "$ORIGIN_REQ_POLICY",
    "GrpcConfig": { "Enabled": false }
  },
  "CacheBehaviors": { "Quantity": 0 },
  "CustomErrorResponses": { "Quantity": 0 },
  "Comment": "$COMMENT",
  "Logging": { "Enabled": false, "IncludeCookies": false, "Bucket": "", "Prefix": "" },
  "PriceClass": "PriceClass_200",
  "Enabled": true,
  "ViewerCertificate": {
    "CloudFrontDefaultCertificate": true,
    "SSLSupportMethod": "vip",
    "MinimumProtocolVersion": "TLSv1",
    "CertificateSource": "cloudfront"
  },
  "Restrictions": { "GeoRestriction": { "RestrictionType": "none", "Quantity": 0 } },
  "WebACLId": "",
  "HttpVersion": "http2and3",
  "IsIPV6Enabled": true,
  "ContinuousDeploymentPolicyId": "",
  "Staging": false
}
JSON
}

show_plan() {
  cat <<EOF
CloudFront distribution config
════════════════════════════════════════════════════════════════════
[Origin]
  DomainName              $GW_ALB_DNS
  OriginProtocolPolicy    http-only          ALB only listens on 80
  OriginSslProtocols      TLSv1.2
  OriginReadTimeout       60s                default 30s cuts off slow first tokens
  OriginKeepaliveTimeout  60s
  ConnectionAttempts/Timeout  3 / 10s
  OriginShield            disabled

[Default cache behavior]
  ViewerProtocolPolicy    redirect-to-https
  AllowedMethods          HEAD DELETE POST GET OPTIONS PUT PATCH  (7)
  Compress                false              preserves SSE streaming (important)
  CachePolicyId           CachingDisabled
  OriginRequestPolicyId   AllViewerExceptHostHeader
                          -> forwards Authorization / anthropic-* headers,
                             replaces only Host with the origin's
  GrpcConfig / SmoothStreaming   disabled
  Lambda@Edge / CF Functions     none

[Distribution]
  Enabled  true    PriceClass_200    HttpVersion http2and3    IPv6 on
  Aliases  none (no custom domain)          Staging false

[Certificate]
  CloudFrontDefaultCertificate  true -> *.cloudfront.net (free)
  SSLSupportMethod vip          MinimumProtocolVersion TLSv1

[Deliberately empty]
  WebACLId  none   <- attach a WAF IP set here when hardening
  Logging   disabled       GeoRestriction none
════════════════════════════════════════════════════════════════════
EOF
}

show_status() {
  hdr "Distributions fronting this ALB"
  aws cloudfront list-distributions \
    --query "DistributionList.Items[?Origins.Items[0].DomainName=='$GW_ALB_DNS'].[Id,DomainName,Status,Enabled]" \
    --output text
  hdr "gateway ALB SG inbound ($GW_SG)"
  aws ec2 describe-security-group-rules --filters "Name=group-id,Values=$GW_SG" \
    --query "SecurityGroupRules[?!IsEgress].[FromPort,CidrIpv4,PrefixListId,Description]" --output text
}

show_diff() {
  # Optional: only works if you have another deployment to compare against.
  if [ -z "$REFERENCE_CF_DIST_ID" ]; then
    warn "REFERENCE_CF_DIST_ID is not set in config.env — nothing to compare against."
    return 0
  fi
  write_config
  local ref="$LIB_DIR/.cf-reference.json"
  AWS_PROFILE="$REFERENCE_CF_PROFILE" aws cloudfront get-distribution-config \
    --id "$REFERENCE_CF_DIST_ID" --query "DistributionConfig" --output json > "$ref" 2>/dev/null || {
      warn "Cannot read reference distribution $REFERENCE_CF_DIST_ID (profile=${REFERENCE_CF_PROFILE:-default}). Credentials for that account may be absent, which is fine."
      rm -f "$CFG_FILE"; return 0; }
  hdr "Reference distribution vs this script"
  note "Only these should differ: CallerReference / Origins DomainName / Comment"
  diff <(python3 -m json.tool --sort-keys "$ref") \
       <(python3 -m json.tool --sort-keys "$CFG_FILE") && echo "(no differences)"
  rm -f "$ref" "$CFG_FILE"
}

# CloudFront reaches the origin from public IPs, so the ALB has to accept that
# range.
#
# Do NOT edit the security group directly. The ALB is owned by the AWS Load
# Balancer Controller, which continuously reconciles SG rules against
# the Ingress annotations. A manually added rule survives briefly, then quietly
# disappears and every request starts returning 502 (observed in practice).
# The Ingress annotation is the source of truth.
allow_cloudfront() {
  local ING="$ING_GATEWAY"
  local ANN="alb.ingress.kubernetes.io/security-group-prefix-lists"

  hdr "Allow the CloudFront prefix list on the gateway Ingress"
  cat <<EOF
  How
    · Edits the Ingress annotation rather than the SG (the controller treats
      the annotation as the source of truth)
        $ANN = $CF_PREFIX_LIST

  What this means (approved decision)
    · The gateway ALB becomes reachable by anyone who goes through CloudFront
    · Effective data-plane access control shifts from IP+VK to VK alone,
      matching the reference deployment's model
    · admin-api / admin-ui keep their IP restrictions — out of scope here

  Persistence
    · This Ingress belongs to helm release '$HELM_RELEASE'. An annotation set
      via kubectl is lost on the next helm upgrade. To make it permanent, add
      the following to values:

        ingress:
          annotations:
            $ANN: "$CF_PREFIX_LIST"
EOF
  confirm "Allow the CloudFront prefix list ($CF_PREFIX_LIST) on Ingress $ING."

  local before
  before=$(kubectl get ingress "$ING" -n "$NS" \
           -o jsonpath="{.metadata.annotations.alb\\.ingress\\.kubernetes\\.io/security-group-prefix-lists}" 2>/dev/null)
  printf '%s\n' "${before:-<none>}" > "$SNAP_DIR/${TS}-03-ingress-prefixlists-rollback.txt"

  kubectl annotate ingress "$ING" -n "$NS" "$ANN=$CF_PREFIX_LIST" --overwrite \
    || die "Failed to set the annotation"
  ok "annotation applied"

  # Confirm the controller actually propagates it to the SG. If the annotation
  # name were wrong, or unsupported by this controller version, it shows here.
  hdr "Waiting for the controller to reconcile (up to 90s)"
  local i found=""
  for i in $(seq 1 18); do
    found=$(aws ec2 describe-security-group-rules --filters "Name=group-id,Values=$GW_SG" \
            --query "SecurityGroupRules[?!IsEgress && PrefixListId=='$CF_PREFIX_LIST'].PrefixListId" \
            --output text 2>/dev/null)
    [ -n "$found" ] && break
    printf '\r  waited %2d0s...' "$i"; sleep 5
  done
  echo
  if [ -n "$found" ]; then
    ok "prefix-list rule now present in the SG — the controller accepted the annotation"
  else
    bad "not reflected in the SG within 90s"
    note "This controller version may not support that annotation name"
    note "Check: kubectl logs -n kube-system deploy/aws-load-balancer-controller --tail=50"
  fi
  show_status
}

create_dist() {
  local existing
  existing=$(aws cloudfront list-distributions \
    --query "DistributionList.Items[?Origins.Items[0].DomainName=='$GW_ALB_DNS'].Id" --output text)
  if [ -n "$existing" ]; then
    warn "a distribution already fronts this ALB: $existing"
    confirm "Create another one anyway?"
  fi

  write_config
  confirm "Create the CloudFront distribution."
  hdr "Creating"
  local out id domain
  out=$(aws cloudfront create-distribution --distribution-config "file://$CFG_FILE" \
        --query "Distribution.[Id,DomainName,Status]" --output text) \
    || { die "Creation failed — config kept at: $CFG_FILE"; }
  id=$(awk '{print $1}' <<<"$out"); domain=$(awk '{print $2}' <<<"$out")
  rm -f "$CFG_FILE"
  echo "$id $domain" > "$SNAP_DIR/${TS}-03-cloudfront.txt"

  cat <<EOF

════════════════════════════════════════════════════════════════════
  Distribution ID : $id
  base URL        : https://$domain
                    ^ this is Cowork's inferenceGatewayBaseUrl
════════════════════════════════════════════════════════════════════

  Next
    1) bash $(basename "$0") --allow-cloudfront   # without this: 502
    2) wait 5-15 minutes for global propagation
       aws cloudfront get-distribution --id $id --query Distribution.Status --output text
    3) bash 04-verify.sh --base-url https://$domain --vk <VK>
EOF
}

require_env

case "${1:-}" in
  --create)           show_plan; create_dist ;;
  --allow-cloudfront) allow_cloudfront ;;
  --status)           show_status ;;
  --diff)             show_diff ;;
  *) show_plan
     cat <<EOF

Options
  --create             create the distribution
  --allow-cloudfront   allow the CloudFront range via the Ingress annotation
                       (required after creation; without it every request 502s)
  --status             current state
  --diff               compare field by field against REFERENCE_CF_DIST_ID
EOF
     ;;
esac
