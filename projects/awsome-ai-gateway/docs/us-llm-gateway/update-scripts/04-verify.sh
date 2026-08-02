#!/bin/bash
# ---------------------------------------------------------------------------
# 04-verify.sh
#
# WHAT: confirm the three changes actually took effect, across DB state, an
#       end-to-end call, and cost recording
# WHY:  failures on this gateway are mostly the quiet kind — 404 from an
#       unexpired cache, $0 cost from a missing price row, 400 from a
#       whitelist. Each symptom is printed with its likely cause.
# UNDO: read-only — changes nothing
#
# Usage:
#   bash 04-verify.sh                                    # DB layer only
#   bash 04-verify.sh --base-url https://xxx.cloudfront.net --vk vk-xxxx
# ---------------------------------------------------------------------------
set -uo pipefail
source "$(dirname "$(readlink -f "$0")")/_lib.sh"

BASE_URL=""; VK=""; MODEL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --base-url) BASE_URL="$2"; shift 2 ;;
    --vk)       VK="$2";       shift 2 ;;
    --model)    MODEL="$2";    shift 2 ;;
    -h|--help)  sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "Unknown argument: $1"; exit 1 ;;
  esac
done

require_env
MODEL="${MODEL:-$MODEL_ALIAS}"   # --model overrides config.env

# ── (A) Database layer ──────────────────────────────────────────────────────
hdr "A. Database state"

DB_OUT=$(run_sql "$(cat <<'Q'
\echo '--- routing_profiles: does the Cowork row mirror claude-code? ---'
SELECT client, backend, region,
       COALESCE(default_model,'(null)')    AS default_model,
       COALESCE(account_role_arn,'(null)') AS account_role_arn,
       enabled
  FROM model.routing_profiles ORDER BY client;

\echo ''
\echo '--- ACTIVE aliases with their effective price ---'
SELECT a.alias, a.provider_model_id,
       COALESCE(p.input_price_per_1k_tokens::text,  'MISSING') AS input_1k,
       COALESCE(p.output_price_per_1k_tokens::text, 'MISSING') AS output_1k
  FROM model.model_aliases a
  LEFT JOIN LATERAL (
       SELECT * FROM model.model_pricings mp
        WHERE mp.model_alias = a.alias AND mp.effective_from <= now()
        ORDER BY mp.effective_from DESC LIMIT 1) p ON true
 WHERE a.status='ACTIVE' ORDER BY a.alias;
Q
)" 2>&1) || { echo "$DB_OUT"; die "Database query failed"; }
echo "$DB_OUT"

hdr "A. Findings"
grep -qE "^ $COWORK_CLIENT .*$COWORK_BACKEND" <<<"$DB_OUT" \
  && ok "$COWORK_CLIENT routing corrected (backend=$COWORK_BACKEND)" \
  || bad "$COWORK_CLIENT routing still on mantle — run 01-fix-cowork-routing.sh --apply"

grep -q "$MODEL" <<<"$DB_OUT" \
  && ok "$MODEL alias is ACTIVE" \
  || bad "$MODEL alias missing — run 02-add-opus5-model.sh"

grep -q 'MISSING' <<<"$DB_OUT" \
  && bad "a model has no price — its cost will be recorded as \$0" \
  || ok "every ACTIVE model has an effective price"

# ── (B) End to end ──────────────────────────────────────────────────────────
if [ -z "$BASE_URL" ] || [ -z "$VK" ]; then
  hdr "B. End-to-end call — skipped"
  note "To run it: bash $(basename "$0") --base-url https://<cf-domain> --vk <VK>"
  note "The VK comes from gateway-cli's api-key-helper"
  exit 0
fi

hdr "B. End-to-end call (headers that classify as client=cowork)"
note "anthropic-client-platform: desktop_app is what client_identifier.py:38-45 keys on"
note "max_tokens is deliberately large: Opus 5 emits a thinking block first."
note "Too small a budget returns empty text and reads like a broken response"

RESP=$(mktemp); CODE=$(mktemp)
curl -sS -o "$RESP" -w '%{http_code}' -X POST "$BASE_URL/v1/messages" \
  -H "Authorization: Bearer $VK" \
  -H "Content-Type: application/json" \
  -H "anthropic-version: 2023-06-01" \
  -H "anthropic-client-platform: desktop_app" \
  -d "{\"model\":\"$MODEL\",\"max_tokens\":64,
       \"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: OPUS5 OK\"}]}" \
  > "$CODE" 2>/dev/null
HTTP=$(cat "$CODE")

echo
echo "  HTTP $HTTP"
python3 - "$RESP" <<'PY' 2>/dev/null || sed 's/^/    /' "$RESP"
import json,sys
d=json.load(open(sys.argv[1]))
if "content" in d:
    print("    model      :", d.get("model"))
    print("    stop_reason:", d.get("stop_reason"))
    for b in d.get("content", []):
        print(f"    content    : {b.get('type')} | {(b.get('text') or '')[:60]}")
    u=d.get("usage",{})
    print("    usage      : in", u.get("input_tokens"), "/ out", u.get("output_tokens"))
else:
    print("    ", json.dumps(d, ensure_ascii=False)[:400])
PY

if [ "$HTTP" = "200" ]; then
  ok "end-to-end call succeeded"
else
  bad "end-to-end call failed (HTTP $HTTP)"
  cat <<'EOF'

  Symptom -> cause
  ──────────────────────────────────────────────────────────────────
   404 not_found_error         alias missing / INACTIVE / cache not yet expired (wait 5 min)
   400 "does not have access"  team_allowed_models whitelist — team row required
   502 / AssumeRole error      01 not applied, or routing cache not yet expired
   403                         VK expired — re-run api-key-helper
   CloudFront 502/504          03 --allow-cloudfront not run (origin unreachable)
  ──────────────────────────────────────────────────────────────────
EOF
fi
rm -f "$RESP" "$CODE"

# ── (C) Cost recording ──────────────────────────────────────────────────────
hdr "C. Is cost actually being recorded?"
note "\$0 means the price row is wrong — requests succeed, so this is the only place it shows"
# Column names per admin-api/src/app/models/usage.py:46-80 — the cost column is
# cost_usd (not total_cost_usd) and rows are timestamped completed_at, not
# created_at. status is included because a failed call still writes a row: a
# $0 next to status=ERROR is expected, next to SUCCESS it means a bad price row.
run_sql "SELECT COALESCE(client,'(legacy)') AS client, model_alias, status,
                cost_usd, input_tokens, output_tokens, completed_at
           FROM usage.usage_logs ORDER BY completed_at DESC LIMIT 5;"
