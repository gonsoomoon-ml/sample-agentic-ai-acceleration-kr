#!/bin/bash
# ---------------------------------------------------------------------------
# 00-preflight-check.sh
#
# WHAT: show what config resolved to, read current state, and decide whether
#       anything blocks the update
# WHY:  some conditions must be known up front — e.g. any row in
#       team_allowed_models flips it to whitelist mode and a newly added model
#       then returns 400. This script also records the pre-change snapshot.
# UNDO: read-only — changes nothing
# ---------------------------------------------------------------------------
set -uo pipefail
source "$(dirname "$(readlink -f "$0")")/_lib.sh"

require_env

echo
printf '%s LLM Gateway update — preflight check%s\n' "$c_bold" "$c_reset"

# Confirm config + discovery landed on the right environment before anything
# else is read or reported.
show_resolved

# ── Database state ──────────────────────────────────────────────────────────
hdr "1. Database state"

# Quoted heredoc: without the quotes the shell would expand $0 etc. and
# corrupt the SQL. COALESCE(...) renders NULLs visibly so the checks below
# can grep for them.
SQL=$(cat <<'Q'
\echo ''
\echo '--- routing_profiles ---'
SELECT client, backend, region,
       COALESCE(default_model,'(null)')    AS default_model,
       COALESCE(account_role_arn,'(null)') AS account_role_arn,
       enabled, web_search_enabled
  FROM model.routing_profiles ORDER BY client;

\echo ''
\echo '--- model_aliases: ACTIVE ---'
SELECT alias, provider, provider_model_id FROM model.model_aliases
 WHERE status='ACTIVE' ORDER BY alias;

\echo ''
\echo '--- model_pricings: effective price per ACTIVE model (missing => cost logged as $0) ---'
SELECT a.alias,
       COALESCE(p.input_price_per_1k_tokens::text,  'MISSING') AS input_1k,
       COALESCE(p.output_price_per_1k_tokens::text, 'MISSING') AS output_1k
  FROM model.model_aliases a
  LEFT JOIN LATERAL (
       SELECT * FROM model.model_pricings mp
        WHERE mp.model_alias = a.alias AND mp.effective_from <= now()
        ORDER BY mp.effective_from DESC LIMIT 1) p ON true
 WHERE a.status='ACTIVE' ORDER BY a.alias;

\echo ''
\echo '--- team_allowed_models  (0 rows = allow all) ---'
SELECT team_id, model_alias FROM model.team_allowed_models ORDER BY team_id, model_alias;

\echo ''
\echo '--- user_allowed_models  (0 rows means "defer to team", NOT "allow all") ---'
SELECT user_id, model_alias FROM model.user_allowed_models ORDER BY user_id, model_alias;

\echo ''
\echo '--- budget_configs ---'
SELECT scope, scope_id, COALESCE(client,'(total)') AS client,
       max_budget_usd, period_type, is_active
  FROM budget.budget_configs ORDER BY scope, client NULLS FIRST;
Q
)

# Query once, reuse for display + checks + snapshot. Spinning up a psql pod
# costs 20-30s, so there is no reason to do it three times.
DB_OUT=$(run_sql "$SQL" 2>&1) || { echo "$DB_OUT"; die "Database query failed"; }
echo "$DB_OUT"

# ── Findings ────────────────────────────────────────────────────────────────
hdr "2. Findings"

if grep -qE "^ $COWORK_CLIENT .*mantle" <<<"$DB_OUT"; then
  bad "$COWORK_CLIENT routing is still on mantle -> run 01-fix-cowork-routing.sh"
  note "In this state the model name Cowork sends is discarded and forced to the profile's default_model"
  note "(messages.py:118 — mantle + default_model acts as a hard override)"
else
  ok "$COWORK_CLIENT routing already corrected"
fi

if grep -q "$MODEL_ALIAS" <<<"$DB_OUT"; then
  ok "$MODEL_ALIAS alias already present"
else
  warn "$MODEL_ALIAS alias missing -> run 02-add-opus5-model.sh"
fi

if grep -q 'MISSING' <<<"$DB_OUT"; then
  bad "an ACTIVE model has no price row — its cost will be recorded as \$0"
  note "Requests still succeed, so this is easy to miss; budget is bypassed meanwhile"
else
  ok "every ACTIVE model has an effective price"
fi

# Count directly instead of parsing the table output above: a wrong verdict
# here means chasing a mysterious 400 much later.
TEAM_ROWS=$(run_sql "\\pset tuples_only on
SELECT count(*) FROM model.team_allowed_models;" 2>/dev/null | tr -dc '0-9')
USER_ROWS=$(run_sql "\\pset tuples_only on
SELECT count(*) FROM model.user_allowed_models;" 2>/dev/null | tr -dc '0-9')

if [ "${TEAM_ROWS:-0}" -gt 0 ]; then
  bad "team_allowed_models has ${TEAM_ROWS} row(s) = whitelist mode"
  note "A new model returns 400 until a matching row is added for that team"
  note "Pass --team-id <uuid> to 02-add-opus5-model.sh"
else
  ok "team_allowed_models has 0 rows = allow all. Nothing extra to do"
fi

# The two tables differ in what "0 rows" means, so this one is only a warning
# when rows DO exist — such users ignore team settings entirely.
if [ "${USER_ROWS:-0}" -gt 0 ]; then
  warn "user_allowed_models has ${USER_ROWS} row(s) — those users ignore team settings"
  note "To grant them the new model, insert into user_allowed_models as well"
fi

# ── Network ─────────────────────────────────────────────────────────────────
hdr "3. HTTPS endpoint"

if [ -n "$GW_ALB_NAME" ]; then
  LISTENERS=$(aws elbv2 describe-listeners \
    --load-balancer-arn "$(aws elbv2 describe-load-balancers --names "$GW_ALB_NAME" \
        --query 'LoadBalancers[0].LoadBalancerArn' --output text)" \
    --query "Listeners[].[Protocol,Port]" --output text 2>/dev/null)
  echo "  gateway ALB listeners:"
  sed 's/^/    /' <<<"$LISTENERS"
  grep -q HTTPS <<<"$LISTENERS" \
    && ok "ALB has an HTTPS listener" \
    || warn "HTTP only — Cowork needs an https base URL -> 03-create-cloudfront.sh"
else
  warn "gateway ALB not resolved — is the ingress provisioned yet?"
fi

# Filter by origin domain: an account may hold unrelated distributions, so a
# bare "any CloudFront?" check would misjudge.
CF=$(aws cloudfront list-distributions \
  --query "DistributionList.Items[?Origins.Items[0].DomainName=='$GW_ALB_DNS'].[Id,DomainName,Status]" \
  --output text 2>/dev/null)
if [ -n "$CF" ]; then
  ok "a CloudFront distribution already fronts this ALB"
  sed 's/^/    /' <<<"$CF"
else
  warn "no CloudFront distribution -> run 03-create-cloudfront.sh"
fi

if [ -n "$GW_SG" ]; then
  echo
  echo "  gateway ALB SG inbound ($GW_SG):"
  aws ec2 describe-security-group-rules --filters "Name=group-id,Values=$GW_SG" \
    --query "SecurityGroupRules[?!IsEgress].[FromPort,CidrIpv4,PrefixListId,Description]" \
    --output text 2>/dev/null | sed 's/^/    /'
fi

# ── Snapshot ────────────────────────────────────────────────────────────────
# 01/02 write their own rollback SQL at apply time; this is the "what did the
# whole thing look like before we touched it" record.
SNAP="$SNAP_DIR/${TS}-00-preflight.txt"
{ echo "# preflight $TS  account=$AWS_ACCOUNT_ID release=$HELM_RELEASE"; echo; echo "$DB_OUT"; } > "$SNAP"

hdr "Next steps"
cat <<EOF
  snapshot saved: $SNAP

  1) bash 01-fix-cowork-routing.sh            # dry-run first
     bash 01-fix-cowork-routing.sh --apply
  2) bash 02-add-opus5-model.sh --help        # see required price arguments
  3) wait 5 minutes, then run 04-verify.sh
EOF
