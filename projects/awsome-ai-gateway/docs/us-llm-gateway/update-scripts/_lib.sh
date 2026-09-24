#!/bin/bash
# ---------------------------------------------------------------------------
# _lib.sh — shared helpers. Sourced by the other scripts; never run directly.
#
# WHAT:   config loading + account guard + resource discovery + DB plumbing
# WHY:    these scripts run against different installs in different accounts,
#         so nothing environment-specific may be hardcoded. Values come from
#         config.env; anything derivable is discovered from the cluster.
# UNDO:   this file changes nothing
# ---------------------------------------------------------------------------

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SNAP_DIR="$LIB_DIR/snapshots"
mkdir -p "$SNAP_DIR"
TS="$(date +%Y%m%d-%H%M%S)"

# ── Output helpers ──────────────────────────────────────────────────────────
c_reset=$'\033[0m'; c_bold=$'\033[1m'; c_red=$'\033[31m'
c_green=$'\033[32m'; c_yellow=$'\033[33m'; c_dim=$'\033[2m'

hdr()  { printf '\n%s%s%s\n%s\n' "$c_bold" "$1" "$c_reset" "$(printf '─%.0s' $(seq 1 68))"; }
ok()   { printf '%s  OK %s %s\n'   "$c_green"  "$c_reset" "$1"; }
warn() { printf '%s  !! %s %s\n'   "$c_yellow" "$c_reset" "$1"; }
bad()  { printf '%s  XX %s %s\n'   "$c_red"    "$c_reset" "$1"; }
note() { printf '%s     %s%s\n'    "$c_dim" "$1" "$c_reset"; }
die()  { printf '\n%sABORTED: %s%s\n\n' "$c_red" "$1" "$c_reset" >&2; exit 1; }

# ── Config ──────────────────────────────────────────────────────────────────
load_config() {
  local cfg="${CONFIG_FILE:-$LIB_DIR/config.env}"
  [ -f "$cfg" ] || die "config.env not found.
     cp $LIB_DIR/config.env.example $LIB_DIR/config.env
     then edit it for your environment."
  # shellcheck disable=SC1090
  source "$cfg"
  CONFIG_PATH="$cfg"

  # Only the account is genuinely required. Everything else has a default that
  # matches what deployment/scripts/install-eks.sh produces, so an install that
  # followed the guide needs no other edits.
  [ -n "${AWS_ACCOUNT_ID:-}" ] || die "AWS_ACCOUNT_ID is not set in $cfg
     This is the guard that stops these scripts running against the wrong
     environment, so it has no default. Find it with:
       aws sts get-caller-identity --query Account --output text"

  # install-eks.sh hardcodes RELEASE_NAME/NAMESPACE to llm-gateway (:47-48),
  # and terraform defaults database_name to "gateway"
  # (modules/aurora-postgresql/variables.tf:19).
  : "${AWS_REGION:=us-west-2}"
  : "${K8S_NAMESPACE:=llm-gateway}"
  : "${HELM_RELEASE:=llm-gateway}"
  : "${DB_NAME:=gateway}"

  # Secrets are named /llm-gateway/<env>/... where <env> is the argument passed
  # to install-eks.sh (modules/aurora-postgresql/secrets.tf:10).
  : "${DEPLOY_ENV:=dev}"
  : "${DB_SECRET_ID:=/llm-gateway/${DEPLOY_ENV}/db/gateway-user}"

  # Defaults for anything the operator left out
  : "${PSQL_IMAGE:=postgres:16-alpine}"
  : "${SEED_ADMIN_UUID:=00000000-0000-4000-a000-000000000010}"
  : "${COWORK_CLIENT:=cowork}"
  : "${COWORK_BACKEND:=invoke}"
  : "${COWORK_REGION:=$AWS_REGION}"
  : "${CLAUDE_CODE_CLIENT:=claude-code}"
  : "${CLAUDE_CODE_BACKEND:=invoke}"
  : "${CLAUDE_CODE_REGION:=$AWS_REGION}"
  : "${MODEL_ALIAS:=claude-opus-5}"
  : "${MODEL_PROVIDER_ID:=us.anthropic.claude-opus-5}"
  : "${MODEL_DISPLAY_NAME:=Claude Opus 5}"
  : "${MODEL_DESCRIPTION:=Claude Opus 5 (US Geo)}"
  # Prices intentionally have no default — see 02-add-opus5-model.sh
  : "${MODEL_PRICE_INPUT:=}"
  : "${MODEL_PRICE_OUTPUT:=}"
  : "${MODEL_PRICE_CACHE_5M:=}"
  : "${MODEL_PRICE_CACHE_1H:=}"
  : "${MODEL_PRICE_CACHE_READ:=}"
  : "${MODEL_PRICE_ASOF:=}"
  : "${DB_HOST:=}"
  : "${CF_PREFIX_LIST:=}"
  : "${REFERENCE_CF_DIST_ID:=}"
  : "${REFERENCE_CF_PROFILE:=}"

  export AWS_DEFAULT_REGION="$AWS_REGION"
  NS="$K8S_NAMESPACE"

  # Ingress objects are named after the helm release
  ING_GATEWAY="${HELM_RELEASE}-gateway"
  ING_ADMIN_API="${HELM_RELEASE}-admin-api"
  ING_ADMIN_UI="${HELM_RELEASE}-admin-ui"

  export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"
}

# ── Preconditions ───────────────────────────────────────────────────────────
# Refuse to run against the wrong account. With several environments in play,
# pointing an update at the wrong one is a realistic mistake, so fail loudly
# before anything is touched.
require_env() {
  load_config

  for c in aws kubectl jq base64; do
    command -v "$c" >/dev/null 2>&1 || die "$c not found. Run this on a host that has it (usually the deployer EC2)."
  done

  local acct
  acct=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) \
    || die "No usable AWS credentials."
  if [ "$acct" != "$AWS_ACCOUNT_ID" ]; then
    die "Account mismatch — credentials are for $acct, config.env expects $AWS_ACCOUNT_ID
     Either switch AWS_PROFILE, or fix AWS_ACCOUNT_ID in $CONFIG_PATH."
  fi

  kubectl get ns "$NS" >/dev/null 2>&1 \
    || die "Cannot reach namespace '$NS' in the EKS cluster.
     Check kubeconfig and K8S_NAMESPACE in $CONFIG_PATH."

  aws secretsmanager describe-secret --secret-id "$DB_SECRET_ID" >/dev/null 2>&1 \
    || die "DB secret '$DB_SECRET_ID' not found.
     DEPLOY_ENV in $CONFIG_PATH is probably wrong (it must match the argument
     you passed to install-eks.sh). Secrets in this account:
$(aws secretsmanager list-secrets --query "SecretList[?starts_with(Name,'/llm-gateway')].Name" --output text 2>/dev/null | tr '\t' '\n' | sed 's/^/       /')"

  # FRESH_INSTALL=1: values 만 편집하는 첫 설치 전 단계(예: 10-switch-https.sh --fresh) —
  # 아직 Ingress 가 없으므로 클러스터 discovery 를 건너뛴다.
  [ "${FRESH_INSTALL:-0}" = 1 ] || discover
}

# ── Discovery ───────────────────────────────────────────────────────────────
# Everything derivable is looked up rather than hardcoded, so the same scripts
# work against any install. Discovery failures are fatal only for the values a
# given script actually needs, so soft-fail here and let callers check.
discover() {
  kubectl get ingress "$ING_GATEWAY" -n "$NS" >/dev/null 2>&1 \
    || die "Ingress '$ING_GATEWAY' not found in namespace '$NS'.
     HELM_RELEASE in $CONFIG_PATH is probably wrong. Existing ingresses:
$(kubectl get ingress -n "$NS" --no-headers -o custom-columns=NAME:.metadata.name 2>/dev/null | sed 's/^/       /')"

  GW_ALB_DNS=$(kubectl get ingress "$ING_GATEWAY" -n "$NS" \
    -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null)

  # US-06 (custom domain + ACM on the ALB, ops/8-H-alb-https.md): the gateway
  # Ingress then carries a host rule and a certificate-arn annotation. When both
  # are present the public entry point is https://<host>, not the raw ALB DNS,
  # and no CloudFront is needed — every script that hands out or checks a URL
  # must branch on GW_HTTPS rather than assume the http/CloudFront layout.
  GW_HOST=$(kubectl get ingress "$ING_GATEWAY" -n "$NS" \
    -o jsonpath='{.spec.rules[0].host}' 2>/dev/null)
  GW_CERT_ARN=$(kubectl get ingress "$ING_GATEWAY" -n "$NS" \
    -o jsonpath='{.metadata.annotations.alb\.ingress\.kubernetes\.io/certificate-arn}' 2>/dev/null)
  ADMIN_API_HOST=$(kubectl get ingress "$ING_ADMIN_API" -n "$NS" \
    -o jsonpath='{.spec.rules[0].host}' 2>/dev/null)
  GW_HTTPS=0
  [ -n "$GW_HOST" ] && [ -n "$GW_CERT_ARN" ] && GW_HTTPS=1

  GW_ALB_NAME=""; GW_SG=""
  if [ -n "$GW_ALB_DNS" ]; then
    local lb
    lb=$(aws elbv2 describe-load-balancers \
         --query "LoadBalancers[?DNSName=='$GW_ALB_DNS'].[LoadBalancerName,SecurityGroups[0]]" \
         --output text 2>/dev/null)
    GW_ALB_NAME=$(awk '{print $1}' <<<"$lb")
    GW_SG=$(awk '{print $2}' <<<"$lb")
  fi

  # The CloudFront origin-facing prefix list has a different ID per region,
  # so it must be resolved by name, never copied between environments.
  if [ -z "$CF_PREFIX_LIST" ]; then
    CF_PREFIX_LIST=$(aws ec2 describe-managed-prefix-lists \
      --filters "Name=prefix-list-name,Values=com.amazonaws.global.cloudfront.origin-facing" \
      --query "PrefixLists[0].PrefixListId" --output text 2>/dev/null)
    [ "$CF_PREFIX_LIST" = "None" ] && CF_PREFIX_LIST=""
  fi

  # DB endpoint: prefer an RDS proxy belonging to this deployment, then any
  # proxy, then the cluster writer endpoint. Matching on the name matters —
  # an account may host unrelated databases, and picking "the first proxy"
  # would silently point these scripts at the wrong one.
  if [ -z "$DB_HOST" ]; then
    DB_HOST=$(aws rds describe-db-proxies \
      --query "DBProxies[?Status=='available' && contains(DBProxyName,'llm-gateway')].Endpoint | [0]" \
      --output text 2>/dev/null)
    [ "$DB_HOST" = "None" ] && DB_HOST=""
  fi
  if [ -z "$DB_HOST" ]; then
    DB_HOST=$(aws rds describe-db-clusters \
      --query "DBClusters[?Status=='available' && contains(DBClusterIdentifier,'llm-gateway')].Endpoint | [0]" \
      --output text 2>/dev/null)
    [ "$DB_HOST" = "None" ] && DB_HOST=""
  fi
  [ -z "$DB_HOST" ] && die "Could not discover a database endpoint.
     Set DB_HOST explicitly in $CONFIG_PATH."
}

# Print what config + discovery resolved to. 00 calls this; the others do not,
# to keep their output focused.
show_resolved() {
  hdr "Resolved configuration"
  printf '  config file      %s\n' "$CONFIG_PATH"
  printf '  account / region %s / %s\n' "$AWS_ACCOUNT_ID" "$AWS_REGION"
  printf '  deploy env       %s\n' "$DEPLOY_ENV"
  printf '  namespace        %s\n' "$NS"
  printf '  helm release     %s\n' "$HELM_RELEASE"
  printf '  ingresses        %s, %s, %s\n' "$ING_GATEWAY" "$ING_ADMIN_API" "$ING_ADMIN_UI"
  printf '  gateway ALB      %s\n' "${GW_ALB_NAME:-<not found>}"
  printf '  gateway ALB DNS  %s\n' "${GW_ALB_DNS:-<not found>}"
  printf '  gateway host     %s\n' "${GW_HOST:-<none — 방식 A, http/CloudFront>}"
  [ "$GW_HTTPS" = 1 ] && printf '  gateway https    yes (cert %s)\n' "${GW_CERT_ARN##*/}"
  printf '  gateway ALB SG   %s\n' "${GW_SG:-<not found>}"
  printf '  CF prefix list   %s\n' "${CF_PREFIX_LIST:-<not found>}"
  printf '  db host          %s\n' "$DB_HOST"
  printf '  db name          %s\n' "$DB_NAME"
  printf '  db secret        %s\n' "$DB_SECRET_ID"
  echo
  note "Discovered values come from the cluster/AWS, not from config.env."
  note "If any line above looks wrong, fix config.env before going further."
}

# ── DB access ───────────────────────────────────────────────────────────────
# The host running these scripts is usually in a different VPC than the
# cluster, so it cannot reach the database directly (observed: connection
# timeout). The reliable path is to run psql from a pod inside the cluster.
#
# SQL is always passed as a file (-f): psql -c refuses to execute a string that
# mixes meta-commands like \echo with statements.
# Credentials enter only through pod env — never through argv or logs.
run_sql_file() {
  local sqlfile="$1"
  [ -f "$sqlfile" ] || die "SQL file not found: $sqlfile"

  local sec u p pod b64 phase
  sec=$(aws secretsmanager get-secret-value --secret-id "$DB_SECRET_ID" \
        --query SecretString --output text 2>/dev/null) \
    || die "Cannot read DB secret: $DB_SECRET_ID"
  u=$(jq -r '.username // empty' <<<"$sec")
  p=$(jq -r '.password // empty' <<<"$sec")
  [ -n "$u" ] && [ -n "$p" ] \
    || die "Secret $DB_SECRET_ID has no username/password keys."
  b64=$(base64 -w0 "$sqlfile")
  pod="psql-$(date +%s)-$RANDOM"

  kubectl run "$pod" -n "$NS" --restart=Never --image="$PSQL_IMAGE" \
    --pod-running-timeout=5m \
    --env="PGPASSWORD=$p" --env="SQLB64=$b64" \
    --env="PGH=$DB_HOST" --env="PGU=$u" --env="PGD=$DB_NAME" \
    --command -- sh -c \
    'echo "$SQLB64" | base64 -d > /tmp/q.sql
     psql -h "$PGH" -U "$PGU" -d "$PGD" -v ON_ERROR_STOP=1 -P pager=off -f /tmp/q.sql' \
    >/dev/null 2>&1

  # Fargate scheduling is slow; wait generously before giving up
  for _ in $(seq 1 90); do
    phase=$(kubectl get pod "$pod" -n "$NS" -o jsonpath='{.status.phase}' 2>/dev/null)
    [ "$phase" = "Succeeded" ] || [ "$phase" = "Failed" ] && break
    sleep 4
  done

  kubectl logs "$pod" -n "$NS" 2>&1
  kubectl delete pod "$pod" -n "$NS" --wait=false >/dev/null 2>&1

  [ "$phase" = "Succeeded" ]
}

# Convenience wrapper: run an inline SQL string by writing it to a temp file
run_sql() {
  local tmp; tmp=$(mktemp)
  printf '%s\n' "$1" > "$tmp"
  run_sql_file "$tmp"
  local rc=$?          # capture before rm, or $? becomes rm's status
  rm -f "$tmp"
  return $rc
}

# ── Interactive confirmation ────────────────────────────────────────────────
confirm() {
  local msg="$1"
  printf '\n%s%s%s\n' "$c_yellow" "$msg" "$c_reset"
  printf 'Type %syes%s to continue (make sure your IME is in English): ' "$c_bold" "$c_reset"
  local ans; read -r ans
  [ "$ans" = "yes" ] || die "Cancelled by user."
}

# ── Cache wait ──────────────────────────────────────────────────────────────
# VK / routing / model / model-list caches all use TTL 300s.
# This cannot be shortened by restarting pods: the cache lives in an external
# ElastiCache, so `rollout restart` has no effect whatsoever.
wait_cache() {
  local secs="${1:-300}"
  hdr "Waiting for caches to expire (${secs}s)"
  note "VK / routing_profile / model / model:list all use TTL 300s"
  note "Restarting pods does not help — the cache is external ElastiCache"
  local i
  for i in $(seq "$secs" -10 1); do
    printf '\r  remaining: %3ds ' "$i"
    sleep 10
  done
  printf '\r  %-30s\n' "done."
}
