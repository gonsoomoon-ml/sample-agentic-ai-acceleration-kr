#!/bin/bash
# ---------------------------------------------------------------------------
# 01a-fix-claude-code-routing.sh
#
# WHAT: reshape the Claude Code row in model.routing_profiles to use
#       in-account Bedrock native (drop the cross-account role ARN and
#       switch region to the local deployment region).
# WHY:  the 0022 migration configures claude-code for a multi-account setup
#       (374 cross-account). Single-account deployments do not have that
#       account, so every call fails STS AssumeRole and falls back to
#       in-account anyway. Setting the row to in-account directly removes
#       the failed-assume overhead and the wrong region.
# UNDO: 99-rollback.sh — applying writes a restore SQL into snapshots/
#
# Usage: bash 01a-fix-claude-code-routing.sh            # dry-run
#        bash 01a-fix-claude-code-routing.sh --apply    # make the change
#        bash 01a-fix-claude-code-routing.sh --status   # show current state only
# ---------------------------------------------------------------------------
set -uo pipefail
source "$(dirname "$(readlink -f "$0")")/_lib.sh"

MODE="${1:-dry-run}"

# Defaults mirror the local deployment. Override in config.env if needed.
: "${CLAUDE_CODE_CLIENT:=claude-code}"
: "${CLAUDE_CODE_BACKEND:=invoke}"
: "${CLAUDE_CODE_REGION:=$AWS_REGION}"

show_current() {
  run_sql "SELECT client, backend, region,
                  COALESCE(default_model,'(null)')    AS default_model,
                  COALESCE(account_role_arn,'(null)') AS account_role_arn,
                  COALESCE(external_id,'(null)')      AS external_id,
                  enabled, web_search_enabled
             FROM model.routing_profiles ORDER BY client;"
}

UPDATE_SQL="UPDATE model.routing_profiles
   SET backend          = '$CLAUDE_CODE_BACKEND',
       account_role_arn = NULL,
       region           = '$CLAUDE_CODE_REGION',
       default_model    = NULL,
       external_id      = NULL,
       updated_at       = now()
 WHERE client = '$CLAUDE_CODE_CLIENT';"

case "$MODE" in
--status)
  hdr "Current routing_profiles"
  show_current
  exit 0
  ;;

--apply)
  hdr "State before change"
  BEFORE=$(show_current) || die "Database query failed"
  echo "$BEFORE"

  hdr "Generating rollback SQL"
  RB_VALUES=$(run_sql "SELECT format(
      'UPDATE model.routing_profiles SET backend=%L, account_role_arn=%L, region=%L, default_model=%L, external_id=%L, updated_at=now() WHERE client=''$CLAUDE_CODE_CLIENT'';',
      backend, account_role_arn, region, default_model, external_id)
    FROM model.routing_profiles WHERE client='$CLAUDE_CODE_CLIENT';" 2>/dev/null \
    | grep -E '^\s*UPDATE model' | sed 's/^ *//')

  RB_FILE="$SNAP_DIR/${TS}-01a-routing-rollback.sql"
  if [ -n "$RB_VALUES" ]; then
    printf '%s\n' "$RB_VALUES" > "$RB_FILE"
    ok "rollback SQL: $RB_FILE"
    note "$RB_VALUES"
  else
    die "Could not read current values, so no rollback SQL can be written. Stopping."
  fi

  confirm "This repoints the $CLAUDE_CODE_CLIENT routing profile to in-account Bedrock ($CLAUDE_CODE_REGION).
The Codex/Cowork paths are unaffected."

  hdr "Applying"
  run_sql "$UPDATE_SQL" || die "UPDATE failed"
  ok "applied"

  hdr "State after change"
  show_current

  hdr "Next steps"
  cat <<EOF
  Expected: $CLAUDE_CODE_CLIENT now uses in-account Bedrock
     backend=$CLAUDE_CODE_BACKEND  region=$CLAUDE_CODE_REGION  account_role_arn=(null)  default_model=(null)

  NOTE: takes up to 5 minutes to take effect (routing_profile cache TTL 300s).
        Restarting pods will not speed this up — the cache is external ElastiCache.
EOF
  ;;

*)
  hdr "Current state"
  show_current

  hdr "Planned change (not applied yet)"
  printf '%s\n' "$UPDATE_SQL"

  cat <<EOF

  Why
    · The 0022 migration points claude-code at a 374 cross-account role
      (345678901234) that does not exist in a single-account deployment.
      Every call fails AssumeRole and falls back to in-account.
    · Setting the row to in-account removes the failed-assume overhead
      and routes to the local region ($CLAUDE_CODE_REGION).

  Apply:  bash $(basename "$0") --apply
EOF
  ;;
esac
