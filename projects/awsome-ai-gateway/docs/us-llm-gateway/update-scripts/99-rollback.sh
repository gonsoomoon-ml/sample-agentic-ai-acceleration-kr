#!/bin/bash
# ---------------------------------------------------------------------------
# 99-rollback.sh
#
# WHAT: revert the changes made by 01/02/03 using the snapshots they left
# WHY:  each script writes a restore statement into snapshots/ at apply time,
#       so rollback uses the values that were actually in the database rather
#       than what a document claims they were
# UNDO: (this script is the undo)
#
# Usage:
#   bash 99-rollback.sh --list              # what can be rolled back
#   bash 99-rollback.sh --routing           # revert 01
#   bash 99-rollback.sh --model             # revert 02 (INACTIVE, never DELETE)
#   bash 99-rollback.sh --cloudfront        # print the revert procedure for 03
# ---------------------------------------------------------------------------
set -uo pipefail
source "$(dirname "$(readlink -f "$0")")/_lib.sh"

require_env

latest() { ls -1t "$SNAP_DIR"/*"$1" 2>/dev/null | head -1; }

list_all() {
  hdr "Snapshots"
  ls -1t "$SNAP_DIR" 2>/dev/null | sed 's/^/  /' || echo "  (none)"
  echo
  local r m c
  r=$(latest "-01-routing-rollback.sql")
  m=$(latest "-02-opus5-rollback.sql")
  c=$(latest "-03-cloudfront.txt")
  hdr "Available rollbacks"
  [ -n "$r" ] && ok "routing    $(basename "$r")"  || note "routing    (none — 01 was never run with --apply)"
  [ -n "$m" ] && ok "model      $(basename "$m")"  || note "model      (none)"
  [ -n "$c" ] && ok "cloudfront $(basename "$c")"  || note "cloudfront (none)"
}

rollback_routing() {
  local f; f=$(latest "-01-routing-rollback.sql")
  [ -n "$f" ] || die "No rollback SQL found. 01 was never run with --apply."
  hdr "Statement to be executed"
  cat "$f"
  hdr "Current state"
  run_sql "SELECT client, backend, region, COALESCE(default_model,'(null)') AS default_model,
                  COALESCE(account_role_arn,'(null)') AS account_role_arn
             FROM model.routing_profiles WHERE client='$COWORK_CLIENT';"
  confirm "Restore the $COWORK_CLIENT routing profile to the snapshot values (i.e. the Mantle seed state)."
  run_sql_file "$f" || die "Rollback failed"
  ok "restored — takes 5 minutes to take effect"
  run_sql "SELECT client, backend, region, COALESCE(account_role_arn,'(null)') AS account_role_arn
             FROM model.routing_profiles WHERE client='$COWORK_CLIENT';"
}

rollback_model() {
  local f; f=$(latest "-02-opus5-rollback.sql")
  [ -n "$f" ] || die "No rollback SQL found. 02 was never run with --apply."
  hdr "Statement to be executed"
  cat "$f"
  note "This sets INACTIVE rather than deleting: several FKs reference"
  note "model_aliases and none declare ON DELETE, so a delete would fail."
  confirm "Set $MODEL_ALIAS to INACTIVE."
  run_sql_file "$f" || die "Rollback failed"
  ok "restored — takes 5 minutes to take effect"
  run_sql "SELECT alias, status FROM model.model_aliases WHERE alias='$MODEL_ALIAS';"
}

rollback_cloudfront() {
  local f; f=$(latest "-03-cloudfront.txt")
  [ -n "$f" ] || die "No creation record found."
  local id domain; read -r id domain < "$f"
  hdr "CloudFront rollback — manual procedure"
  # Not automated: a distribution must be disabled and fully propagated before
  # it can be deleted, which takes several minutes of polling.
  cat <<EOF
  Distribution $id ($domain)

    1) Revoke the SG exposure first (closes the origin immediately).
       Prefer reverting the Ingress annotation, since the controller owns the SG:
       kubectl annotate ingress $ING_GATEWAY -n $NS \\
         alb.ingress.kubernetes.io/security-group-prefix-lists- --overwrite

    2) Disable the distribution
       ETAG=\$(aws cloudfront get-distribution-config --id $id --query ETag --output text)
       aws cloudfront get-distribution-config --id $id --query DistributionConfig > /tmp/cf.json
       python3 -c "import json;d=json.load(open('/tmp/cf.json'));d['Enabled']=False;json.dump(d,open('/tmp/cf.json','w'))"
       aws cloudfront update-distribution --id $id --distribution-config file:///tmp/cf.json --if-match \$ETAG

    3) Wait until Status is Deployed (a few minutes), then delete
       aws cloudfront get-distribution --id $id --query Distribution.Status --output text
       ETAG=\$(aws cloudfront get-distribution-config --id $id --query ETag --output text)
       aws cloudfront delete-distribution --id $id --if-match \$ETAG
EOF
}

case "${1:-}" in
  --list)       list_all ;;
  --routing)    rollback_routing ;;
  --model)      rollback_model ;;
  --cloudfront) rollback_cloudfront ;;
  *) list_all
     cat <<EOF

Options
  --list         show snapshots and what can be rolled back
  --routing      revert 01 (cowork routing back to the Mantle seed state)
  --model        revert 02 (the configured MODEL_ALIAS -> INACTIVE)
  --cloudfront   print the revert procedure for 03

NOTE: database rollbacks also take 5 minutes to take effect (cache TTL 300s).
EOF
     ;;
esac
