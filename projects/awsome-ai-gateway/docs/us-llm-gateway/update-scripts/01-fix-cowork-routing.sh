#!/bin/bash
# ---------------------------------------------------------------------------
# 01-fix-cowork-routing.sh
#
# WHAT: reshape the Cowork row in model.routing_profiles to match claude-code
#       (backend mantle->invoke, drop the placeholder ARN, set the local
#        region, clear default_model)
# WHY:  the Mantle seed from migration 0009 is still live, so Cowork requests
#       try to AssumeRole into a placeholder account that does not exist.
#       Worse, backend='mantle' together with default_model discards whatever
#       model Cowork asks for and forces the seeded model (messages.py:118).
#       Values come from config.env: COWORK_CLIENT / COWORK_BACKEND /
#       COWORK_REGION.
# UNDO: 99-rollback.sh — applying writes a restore SQL into snapshots/
#
# Usage: bash 01-fix-cowork-routing.sh            # dry-run
#        bash 01-fix-cowork-routing.sh --apply    # make the change
#        bash 01-fix-cowork-routing.sh --status   # show current state only
# ---------------------------------------------------------------------------
set -uo pipefail
source "$(dirname "$(readlink -f "$0")")/_lib.sh"

require_env
MODE="${1:-dry-run}"

show_current() {
  run_sql "SELECT client, backend, region,
                  COALESCE(default_model,'(null)')    AS default_model,
                  COALESCE(account_role_arn,'(null)') AS account_role_arn,
                  COALESCE(external_id,'(null)')      AS external_id,
                  enabled, web_search_enabled
             FROM model.routing_profiles ORDER BY client;"
}

UPDATE_SQL="UPDATE model.routing_profiles
   SET backend          = '$COWORK_BACKEND',
       account_role_arn = NULL,
       region           = '$COWORK_REGION',
       default_model    = NULL,
       external_id      = NULL,
       updated_at       = now()
 WHERE client = '$COWORK_CLIENT';"

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

  # Build the restore statement from the live values, not from memory or docs.
  hdr "Generating rollback SQL"
  RB_VALUES=$(run_sql "SELECT format(
      'UPDATE model.routing_profiles SET backend=%L, account_role_arn=%L, region=%L, default_model=%L, external_id=%L, updated_at=now() WHERE client=''$COWORK_CLIENT'';',
      backend, account_role_arn, region, default_model, external_id)
    FROM model.routing_profiles WHERE client='$COWORK_CLIENT';" 2>/dev/null \
    | grep -E '^\s*UPDATE model' | sed 's/^ *//')

  RB_FILE="$SNAP_DIR/${TS}-01-routing-rollback.sql"
  if [ -n "$RB_VALUES" ]; then
    printf '%s\n' "$RB_VALUES" > "$RB_FILE"
    ok "rollback SQL: $RB_FILE"
    note "$RB_VALUES"
  else
    die "Could not read current values, so no rollback SQL can be written. Stopping."
  fi

  confirm "This repoints the $COWORK_CLIENT routing profile to in-account Bedrock ($COWORK_REGION).
The Claude Code path is unaffected."

  hdr "Applying"
  run_sql "$UPDATE_SQL" || die "UPDATE failed"
  ok "applied"

  hdr "State after change"
  show_current

  hdr "Next steps"
  cat <<EOF
  Expected: $COWORK_CLIENT now mirrors claude-code
     backend=$COWORK_BACKEND  region=$COWORK_REGION  account_role_arn=(null)  default_model=(null)

  NOTE: takes up to 5 minutes to take effect (routing_profile cache TTL 300s).
        Restarting pods will not speed this up — the cache is external ElastiCache.

  Next:  bash 02-add-opus5-model.sh --help
EOF
  ;;

*)
  hdr "Current state"
  show_current

  hdr "Planned change (not applied yet)"
  printf '%s\n' "$UPDATE_SQL"

  cat <<EOF

  Why
    · The seeded account_role_arn points at a documentation placeholder
      account that does not exist. Without Mantle in this deployment the
      AssumeRole can never succeed.
    · backend='mantle' combined with default_model discards the model name
      Cowork sends and forces the profile's default_model — messages.py:118

  Why not just delete the row or set enabled=false
    · Per-client toggles such as web_search_enabled live on this row.
      Removing it would silently drop those features too.

  Apply:  bash $(basename "$0") --apply
EOF
  ;;
esac
