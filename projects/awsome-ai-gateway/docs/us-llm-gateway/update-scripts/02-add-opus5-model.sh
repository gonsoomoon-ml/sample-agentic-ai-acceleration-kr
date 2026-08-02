#!/bin/bash
# ---------------------------------------------------------------------------
# 02-add-opus5-model.sh
#
# WHAT: register the model alias from config.env plus its price row
#       (existing models are left untouched)
# WHY:  the Bedrock model is already callable in the region, but the gateway
#       has no alias for it, so no client can request it
#       Values come from config.env: MODEL_ALIAS / MODEL_PROVIDER_ID /
#       MODEL_DISPLAY_NAME / MODEL_DESCRIPTION
# UNDO: 99-rollback.sh — flips status to INACTIVE (never DELETE: several FKs
#       reference model_aliases and none declare ON DELETE)
#
# Usage:
#   Fill MODEL_PRICE_* in config.env, then:
#       bash 02-add-opus5-model.sh              # dry-run
#       bash 02-add-opus5-model.sh --apply
#
#   Or pass prices on the command line, which overrides config.env:
#       bash 02-add-opus5-model.sh --input 0.005 --output 0.025 \
#            --cache-5m 0.00625 --cache-1h 0.01 --cache-read 0.0005 --apply
#
#   If team_allowed_models is in whitelist mode (00 reports this):
#   ... --team-id <uuid>   also inserts the matching team allow row
# ---------------------------------------------------------------------------
set -uo pipefail
source "$(dirname "$(readlink -f "$0")")/_lib.sh"

P_IN=""; P_OUT=""; P_C5M=""; P_C1H=""; P_CREAD=""
TEAM_ID=""; APPLY=0

usage() {
  cat <<EOF
$(basename "$0") — register the model alias defined in config.env

Prices (USD per 1K tokens) — required, from config.env or these flags
  --input      <n>   input tokens          (config: MODEL_PRICE_INPUT)
  --output     <n>   output tokens         (config: MODEL_PRICE_OUTPUT)
  --cache-5m   <n>   cache write, 5m TTL   (config: MODEL_PRICE_CACHE_5M)
  --cache-1h   <n>   cache write, 1h TTL   (config: MODEL_PRICE_CACHE_1H)
  --cache-read <n>   cache read            (config: MODEL_PRICE_CACHE_READ)

  Flags win over config.env. Filling config.env is preferred: the values stay
  recorded, and MODEL_PRICE_ASOF documents when they were last checked.

Optional
  --team-id <uuid>   only when team_allowed_models is in whitelist mode
  --apply            actually apply (otherwise dry-run)

Why prices are mandatory
  With no price row, router_service.py:51-52 substitutes zero without raising
  and cost_recorder.py:24-39 multiplies straight through, so every call is
  logged at \$0. Requests keep succeeding, which makes this very easy to miss
  while the budget is quietly bypassed. Hence this script refuses to proceed
  without explicit prices.

  Look the prices up on the AWS Bedrock pricing page — the Pricing API does
  not expose newer models, so they cannot be fetched automatically.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --input)      P_IN="$2";    shift 2 ;;
    --output)     P_OUT="$2";   shift 2 ;;
    --cache-5m)   P_C5M="$2";   shift 2 ;;
    --cache-1h)   P_C1H="$2";   shift 2 ;;
    --cache-read) P_CREAD="$2"; shift 2 ;;
    --team-id)    TEAM_ID="$2"; shift 2 ;;
    --apply)      APPLY=1;      shift ;;
    -h|--help)    usage; exit 0 ;;
    *) echo "Unknown argument: $1"; usage; exit 1 ;;
  esac
done

require_env

# config.env supplies the prices; command-line flags override them.
P_IN="${P_IN:-$MODEL_PRICE_INPUT}"
P_OUT="${P_OUT:-$MODEL_PRICE_OUTPUT}"
P_C5M="${P_C5M:-$MODEL_PRICE_CACHE_5M}"
P_C1H="${P_C1H:-$MODEL_PRICE_CACHE_1H}"
P_CREAD="${P_CREAD:-$MODEL_PRICE_CACHE_READ}"

# Config supplies the model identity; these locals keep the SQL readable.
ALIAS="$MODEL_ALIAS"
MODEL_ID="$MODEL_PROVIDER_ID"
DISPLAY="$MODEL_DISPLAY_NAME"
DESCRIPTION="$MODEL_DESCRIPTION"

# ── Validate prices ─────────────────────────────────────────────────────────
missing=()
[ -z "$P_IN" ]    && missing+=(--input)
[ -z "$P_OUT" ]   && missing+=(--output)
[ -z "$P_C5M" ]   && missing+=(--cache-5m)
[ -z "$P_C1H" ]   && missing+=(--cache-1h)
[ -z "$P_CREAD" ] && missing+=(--cache-read)
if [ ${#missing[@]} -gt 0 ]; then
  bad "prices not set: ${missing[*]}"
  note "Fill MODEL_PRICE_* in config.env, or pass the flags below."
  echo
  usage
  exit 1
fi
for v in "$P_IN" "$P_OUT" "$P_C5M" "$P_C1H" "$P_CREAD"; do
  [[ "$v" =~ ^[0-9]+(\.[0-9]+)?$ ]] || die "prices must be numeric: $v"
done
# A zero price row behaves exactly like a missing one: cost aggregates to 0.
for pair in "input:$P_IN" "output:$P_OUT"; do
  name="${pair%%:*}"; val="${pair#*:}"
  awk "BEGIN{exit !($val > 0)}" || die "$name price is 0 — cost tracking would be meaningless."
done

# ── SQL ─────────────────────────────────────────────────────────────────────
# Shape follows the existing seed (db/init/03_seed_data.sql:97-111) and
# migration 0004_add_opus_4_6.py:34-46.
SQL_ALIAS="INSERT INTO model.model_aliases
    (alias, provider, provider_model_id, endpoint_url, api_format, status,
     description, display_name, created_by)
VALUES ('$ALIAS', 'BEDROCK', '$MODEL_ID', NULL, 'BEDROCK_NATIVE', 'ACTIVE',
        '$DESCRIPTION', '$DISPLAY', '$SEED_ADMIN_UUID')
ON CONFLICT (alias) DO NOTHING;"

# effective_from must be <= now(): a future-dated row behaves like no row.
SQL_PRICE="INSERT INTO model.model_pricings
    (id, model_alias,
     input_price_per_1k_tokens, output_price_per_1k_tokens,
     cache_creation_5m_price_per_1k_tokens, cache_creation_1h_price_per_1k_tokens,
     cache_read_price_per_1k_tokens,
     effective_from, created_by)
SELECT gen_random_uuid(), '$ALIAS',
       $P_IN, $P_OUT, $P_C5M, $P_C1H, $P_CREAD,
       now(), '$SEED_ADMIN_UUID'
WHERE NOT EXISTS (
    SELECT 1 FROM model.model_pricings WHERE model_alias = '$ALIAS');"

SQL_TEAM=""
if [ -n "$TEAM_ID" ]; then
  SQL_TEAM="INSERT INTO model.team_allowed_models (team_id, model_alias, created_by)
VALUES ('$TEAM_ID', '$ALIAS', '$SEED_ADMIN_UUID')
ON CONFLICT DO NOTHING;"
fi

hdr "What will be registered"
cat <<EOF
  alias              $ALIAS
  provider           BEDROCK
  provider_model_id  $MODEL_ID
                     ^ if this is an INFERENCE_PROFILE-only model it needs a geo prefix
  api_format         BEDROCK_NATIVE
  status             ACTIVE

  Prices (USD per 1K tokens)
    input        $P_IN
    output       $P_OUT
    cache 5m     $P_C5M
    cache 1h     $P_C1H
    cache read   $P_CREAD
    as of        ${MODEL_PRICE_ASOF:-(not recorded)}
EOF
[ -n "$TEAM_ID" ] && echo "  team_allowed_models  allow row for team $TEAM_ID"

hdr "Currently ACTIVE models"
run_sql "SELECT alias, provider_model_id FROM model.model_aliases
          WHERE status='ACTIVE' ORDER BY alias;"

if [ "$APPLY" -eq 0 ]; then
  cat <<EOF

  Nothing applied yet.
  Apply:  bash $(basename "$0") ${TEAM_ID:+--team-id $TEAM_ID }--apply
EOF
  exit 0
fi

confirm "Registering $ALIAS as ACTIVE. Existing models are not modified."

hdr "Applying"
run_sql "$SQL_ALIAS" || die "alias INSERT failed"
ok "alias registered"
run_sql "$SQL_PRICE" || die "pricing INSERT failed"
ok "pricing registered"
if [ -n "$SQL_TEAM" ]; then
  run_sql "$SQL_TEAM" || die "team_allowed_models INSERT failed"
  ok "team allow row registered"
fi

printf 'UPDATE model.model_aliases SET status='"'"'INACTIVE'"'"' WHERE alias='"'"'%s'"'"';\n' \
  "$ALIAS" > "$SNAP_DIR/${TS}-02-opus5-rollback.sql"

hdr "Verification"
run_sql "SELECT a.alias, a.provider_model_id, a.status,
                p.input_price_per_1k_tokens, p.output_price_per_1k_tokens
           FROM model.model_aliases a
           LEFT JOIN model.model_pricings p ON p.model_alias = a.alias
          WHERE a.alias = '$ALIAS';"

hdr "Next steps"
cat <<EOF
  NOTE: takes up to 5 minutes to take effect (model / model:list cache TTL 300s).
        Until then Claude Code gets a 404 from GET /v1/models/{id} and will not
        even attempt a call — an easy point to misread as "it does not work".

  Next:  bash 03-create-cloudfront.sh
         (after 5 minutes) bash 04-verify.sh
EOF
