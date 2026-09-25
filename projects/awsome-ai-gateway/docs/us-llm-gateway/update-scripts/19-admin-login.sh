#!/bin/bash
# ---------------------------------------------------------------------------
# 19-admin-login.sh
#
# WHAT: turn on Cognito sign-in for the admin console (admin-ui), then turn
#       dev-login off — US-12, docs/us-llm-gateway/ops/8-L-admin-login.md.
#         (no step)      status: what is done, what is next (reads the DB)
#         callback       add the admin-ui callback URL to terraform.tfvars
#                        (terraform plan/apply stays a manual step)
#         login          put OIDC_* (4 keys) under adminUi.env in values
#         dev-login-off  DEV_LOGIN_ENABLED "false" under adminApi.env AND adminUi.env
#         verify         probe the login redirect from inside the cluster,
#                        including Cognito's answer to it
# WHY:  the admin-ui image already carries the Cognito login (app/api/auth/
#       login + callback). What is missing is configuration, so /api/auth/login
#       falls through to the dev form, which hands an unsigned ADMIN token to
#       anyone who reaches the host. Every value is derived — pool, client ID
#       and Hosted UI domain from terraform output, the host from the live
#       Ingress — so dev and prod run the same commands.
# UNDO: restore the backup --apply prints (snapshots/), then install-eks.sh
#       <env> for values, or terraform apply for tfvars.
#
# Order: callback → terraform apply → login → install-eks.sh → verify + browser
#        → dev-login-off → install-eks.sh → verify.
# dev-login stays on through the first rollout: it is the way back in if the
# Cognito login fails. dev-login-off refuses until the login is live and an
# admin exists in the DB.
#
# Usage:
#   bash 19-admin-login.sh                          # status (~1.5 min: psql pod)
#   bash 19-admin-login.sh callback [--apply]
#   bash 19-admin-login.sh login [--apply]
#   bash 19-admin-login.sh dev-login-off [--apply]  # also reads the DB
#   bash 19-admin-login.sh verify                   # ~1-2 min: probe pod
#   --no-db          status without the DB check
#   --values <path>  edit/render another values file (test)
# ---------------------------------------------------------------------------
set -uo pipefail
source "$(dirname "$(readlink -f "$0")")/_lib.sh"

STEP=status; APPLY=0; VALUES=""; NO_DB=0
while [ $# -gt 0 ]; do
  case "$1" in
    callback|login|dev-login-off|verify) STEP="$1"; shift ;;
    --apply)   APPLY=1; shift ;;
    --no-db)   NO_DB=1; shift ;;
    --values)  VALUES="$2"; shift 2 ;;
    -h|--help) sed -n '2,36p' "$0"; exit 0 ;;
    *) die "Unknown argument: $1 (see --help)" ;;
  esac
done
case "$STEP" in status|verify) [ "$APPLY" = 0 ] || die "--apply goes with callback | login | dev-login-off" ;; esac

require_env
for c in helm python3 terraform; do
  command -v "$c" >/dev/null 2>&1 || die "$c not found. Run this on the deployer EC2."
done

ROOT="$(cd "$LIB_DIR/../../.." && pwd)"
CHART="$ROOT/deployment/charts/llm-gateway"
V="${VALUES:-${HELM_VALUES_FILE:-$CHART/values-eks-fargate-$DEPLOY_ENV.yaml}}"
[ -f "$V" ] || die "values file not found: $V"
V=$(readlink -f "$V")
TF_DIR="$ROOT/deployment/terraform/environments/llm-gateway-$DEPLOY_ENV"
TFVARS="$TF_DIR/terraform.tfvars"
[ -f "$TFVARS" ] || die "terraform.tfvars not found: $TFVARS"
DEP_UI="${HELM_RELEASE}-admin-ui"
DEP_API="${HELM_RELEASE}-admin-api"

# ── Derived values ──────────────────────────────────────────────────────────
TFO=$(terraform -chdir="$TF_DIR" output -json 2>/dev/null) \
  || die "terraform output failed in $TF_DIR (not initialised?) — do not run terraform apply to fix this; see ops/8-U-update.md"
POOL_ID=$(jq -r '.cognito_user_pool_id.value // empty' <<<"$TFO")
CLIENT_ID=$(jq -r '.cognito_client_id.value // empty' <<<"$TFO")
HOSTED=$(jq -r '.cognito_hosted_ui_domain.value // empty' <<<"$TFO")
[ -n "$POOL_ID" ] && [ -n "$CLIENT_ID" ] && [ -n "$HOSTED" ] \
  || die "terraform output has no cognito_user_pool_id / cognito_client_id / cognito_hosted_ui_domain — this install has no Cognito"

UI_HOST=$(kubectl get ingress "$ING_ADMIN_UI" -n "$NS" -o jsonpath='{.spec.rules[0].host}' 2>/dev/null)
UI_CERT=$(kubectl get ingress "$ING_ADMIN_UI" -n "$NS" \
  -o jsonpath='{.metadata.annotations.alb\.ingress\.kubernetes\.io/certificate-arn}' 2>/dev/null)
[ -n "$UI_HOST" ] && [ -n "$UI_CERT" ] || die "admin-ui Ingress has no https host (host='${UI_HOST}', certificate-arn='${UI_CERT}').
     Cognito accepts http callbacks only for localhost — do US-06 (ops/8-H-alb-https.md) first."

CALLBACK="https://$UI_HOST/api/auth/callback"
AUTHORIZE_URL="https://$HOSTED/oauth2/authorize"
TOKEN_URL="https://$HOSTED/oauth2/token"
LOGIN_KEYS=(OIDC_CLIENT_ID OIDC_AUTHORIZE_URL OIDC_TOKEN_URL OIDC_REDIRECT_URI)
declare -A WANT=( [OIDC_CLIENT_ID]="$CLIENT_ID" [OIDC_AUTHORIZE_URL]="$AUTHORIZE_URL"
                  [OIDC_TOKEN_URL]="$TOKEN_URL" [OIDC_REDIRECT_URI]="$CALLBACK" )

# A failed read must stop the script, never look like "no callbacks".
CB_JSON=$(aws cognito-idp describe-user-pool-client --user-pool-id "$POOL_ID" --client-id "$CLIENT_ID" \
          --query 'UserPoolClient.CallbackURLs' --output json 2>&1) \
  || die "cannot read Cognito app client $CLIENT_ID: $CB_JSON"
LIVE_CB=$(jq -r '.[]?' <<<"$CB_JSON") || die "unexpected describe-user-pool-client output: $CB_JSON"
CB_LIVE=0; [[ $'\n'"$LIVE_CB"$'\n' == *$'\n'"$CALLBACK"$'\n'* ]] && CB_LIVE=1

# ── Helpers ─────────────────────────────────────────────────────────────────
# Effective var.cognito_callback_urls, one per line. terraform parses the
# tfvars (and any *.auto.tfvars), so what we compare is what apply would use.
tf_var_callbacks() {
  local out
  out=$(echo 'jsonencode(var.cognito_callback_urls)' | terraform -chdir="$TF_DIR" console 2>/dev/null | tail -1)
  jq -er 'fromjson | .[]' <<<"$out" 2>/dev/null
}

# "deploy<TAB>KEY<TAB>value" for admin-ui / admin-api container env, from the
# chart rendered with <values>. Later duplicates win, as in the kubelet.
render_env() {
  local out
  out=$(helm template "$HELM_RELEASE" "$CHART" -f "$1" 2>&1) \
    || { printf 'helm template failed for %s:\n%s\n' "$1" "$(tail -5 <<<"$out")" >&2; return 1; }
  python3 -c '
import sys, yaml
for d in yaml.safe_load_all(sys.stdin):
    if not d or d.get("kind") != "Deployment":
        continue
    name = d["metadata"]["name"]
    for dep in ("admin-ui", "admin-api"):
        if name.endswith("-" + dep):
            for e in d["spec"]["template"]["spec"]["containers"][0].get("env") or []:
                if "value" in e:
                    print(dep + "\t" + e["name"] + "\t" + str(e["value"]))
' <<<"$out"
}

# Same shape, from the Deployments running now.
live_env() {
  local dep j
  for dep in admin-ui admin-api; do
    j=$(kubectl get deploy "${HELM_RELEASE}-$dep" -n "$NS" -o json 2>&1) \
      || { printf 'cannot read Deployment %s: %s\n' "${HELM_RELEASE}-$dep" "$j" >&2; return 1; }
    jq -r --arg d "$dep" '.spec.template.spec.containers[0].env[]? | select(.value != null) | [$d, .name, .value] | @tsv' <<<"$j"
  done
}

declare -A RV LV   # rendered / live: RV["admin-ui/OIDC_CLIENT_ID"]=...
load_env() {       # <assoc name> <command...> — dies if the command fails
  local -n _m="$1"; shift
  local out d k v
  out=$("$@") || die "could not read env ($1) — see the message above"
  while IFS=$'\t' read -r d k v; do [ -n "$d" ] && _m["$d/$k"]="$v"; done <<<"$out"
}

# Text edit of the values file, never a YAML round-trip: its comments are the
# documentation. Args: <src> <dst> then triples <section> <KEY> <value>. Keys
# live at indent 4 under <section>: -> env: (indent 2). Existing keys are
# rewritten in place; missing ones go after the last key of that env block.
edit_values() {
  python3 - "$@" <<'PY'
import json, re, sys
src, dst, *ops = sys.argv[1:]
L = open(src, encoding="utf-8").read().split("\n")

def ind(l): return len(l) - len(l.lstrip(" "))
def code(l): return l.strip() != "" and not l.lstrip().startswith("#")

def env_block(sec):
    hits = [i for i, l in enumerate(L) if re.match(rf"^{sec}:\s*(#.*)?$", l)]
    if len(hits) != 1:
        sys.exit(f"values: top-level '{sec}:' found {len(hits)} times (need exactly 1)")
    s = hits[0]
    e = next((i for i in range(s + 1, len(L)) if code(L[i]) and ind(L[i]) == 0), len(L))
    envs = [i for i in range(s + 1, e) if code(L[i]) and ind(L[i]) == 2 and re.match(r"^\s*env:", L[i])]
    if len(envs) != 1:
        sys.exit(f"values: '{sec}.env:' found {len(envs)} times (need exactly 1)")
    i = envs[0]
    if not re.match(r"^\s*env:\s*(#.*)?$", L[i]):
        sys.exit(f"values: '{sec}.env:' is inline ({L[i].strip()}) — make it a block map first")
    j = i + 1
    while j < e and (not code(L[j]) or ind(L[j]) > 2):
        j += 1
    return i, j

groups = {}
for n in range(0, len(ops), 3):
    groups.setdefault(ops[n], []).append((ops[n + 1], ops[n + 2]))

for sec, pairs in groups.items():
    inserted_note = False
    for key, val in pairs:
        i, j = env_block(sec)
        line = f"    {key}: {json.dumps(val)}"
        if key == "DEV_LOGIN_ENABLED":
            line += "  # US-12: Cognito 로그인만 (19-admin-login.sh dev-login-off)"
        hits = [k for k in range(i + 1, j) if code(L[k]) and ind(L[k]) == 4 and re.match(rf"^\s*{key}:", L[k])]
        if len(hits) > 1:
            sys.exit(f"values: {sec}.env.{key} appears {len(hits)} times — fix the file first")
        if hits:
            L[hits[0]] = line
            continue
        last = max([k for k in range(i + 1, j) if code(L[k]) and ind(L[k]) >= 4], default=i)
        new = [line]
        if not inserted_note:
            new.insert(0, "    # US-12 admin 콘솔 Cognito 로그인 (19-admin-login.sh) — 값은 terraform output·Ingress 에서")
            inserted_note = True
        L[last + 1:last + 1] = new

open(dst, "w", encoding="utf-8").write("\n".join(L))
PY
}

# Edit → render → show → (apply). $1 = label, rest = triples for edit_values.
apply_values_edit() {
  local label="$1"; shift
  local tmp; tmp=$(mktemp)
  edit_values "$V" "$tmp" "$@" || { rm -f "$tmp"; die "values edit failed — $V untouched"; }
  if cmp -s "$V" "$tmp"; then rm -f "$tmp"; ok "values already carries these — nothing to write"; return 1; fi

  declare -A NV=()
  load_env NV render_env "$tmp"
  local n=0 sec key val bad_n=0
  local -a args=("$@")
  for ((n = 0; n < ${#args[@]}; n += 3)); do
    sec=${args[n]}; key=${args[n+1]}; val=${args[n+2]}
    local dep=admin-ui; [ "$sec" = adminApi ] && dep=admin-api
    if [ "${NV[$dep/$key]:-}" != "$val" ]; then
      bad "$dep $key renders '${NV[$dep/$key]:-(unset)}', expected '$val'"; bad_n=$((bad_n + 1))
    fi
  done
  [ "$bad_n" -eq 0 ] || { rm -f "$tmp"; die "edited values do not render the target — $V untouched"; }

  hdr "Planned change — $(basename "$V") (helm renders it)"
  diff -u "$V" "$tmp" | sed -n '3,$p' | sed 's/^/  /'
  if [ "$APPLY" = 0 ]; then
    rm -f "$tmp"
    printf '\n  Nothing written yet. Apply:  bash %s %s --apply\n\n' "$(basename "$0")" "$STEP"
    exit 0
  fi
  confirm "$label"
  local bak="$SNAP_DIR/${TS}-values-$DEPLOY_ENV-19-$STEP.bak"
  cp "$V" "$bak" || { rm -f "$tmp"; die "backup failed"; }
  cp "$tmp" "$V" || { rm -f "$tmp"; die "could not write $V (backup: $bak)"; }
  rm -f "$tmp"
  ok "values updated: $V"
  note "backup: $bak   (undo: cp $bak $V, then install-eks.sh $DEPLOY_ENV)"
  return 0
}

# Who can sign in as ADMIN once dev-login is off. Two sources, in order:
#   1. members of the Cognito group(s) in ADMIN_GROUPS, checked against auth.users
#   2. active ADMIN rows in auth.users — the path for a deployment federated to a
#      corporate IdP where the admin group arrives as a token claim and the
#      Cognito group itself has no members.
# Sets DB_ADMINS_OK (> 0 means someone can get in). Prints a line per admin.
check_admin_db() {
  DB_ADMINS_OK=0
  local groups="${LV[admin-api/ADMIN_GROUPS]:-}"
  if [ -z "$groups" ]; then
    bad "admin-api ADMIN_GROUPS is empty — nobody gets ADMIN from the group claim"
    return
  fi
  local g users=""
  IFS=',' read -ra GS <<<"$groups"
  for g in "${GS[@]}"; do
    local out
    out=$(aws cognito-idp list-users-in-group --user-pool-id "$POOL_ID" --group-name "$g" \
          --query 'Users[].[Attributes[?Name==`sub`].Value|[0], Attributes[?Name==`email`].Value|[0]]' \
          --output text 2>&1) || { bad "cannot list Cognito group $g: $out"; continue; }
    users+="$out"$'\n'
  done
  users=$(sed '/^$/d' <<<"$users" | sort -u)

  local subs="" sub email sql
  if [ -n "$users" ]; then
    while IFS=$'\t' read -r sub email; do
      [[ "$sub" =~ ^[0-9a-fA-F-]{36}$ ]] || { warn "skipping $email — unexpected sub '$sub'"; continue; }
      subs+="${subs:+,}'$sub'"
    done <<<"$users"
  fi
  sql="SELECT 'ADM|' || count(*)::text FROM auth.users WHERE role::text = 'ADMIN' AND is_active;"
  [ -n "$subs" ] && sql="SELECT 'ROW|' || sso_subject || '|' || is_active::text || '|' || role::text FROM auth.users WHERE sso_subject IN ($subs); $sql"

  note "reading auth.users — psql pod, ~1.5 min"
  local rows
  rows=$(run_sql "$sql") || { bad "DB query failed:"; sed 's/^/       /' <<<"$rows"; return; }

  if [ -n "$users" ]; then
    while IFS=$'\t' read -r sub email; do
      local r; r=$(command grep -m1 -F "ROW|$sub|" <<<"$rows")
      if [ -z "$r" ]; then
        warn "$email — in the Cognito group but not in auth.users: sign-in gives 403 user_not_provisioned"
        note "   fix: that person runs gateway-cli login once (VK exchange provisions the user)"
      elif [[ "$r" == *"|true|"* ]]; then
        ok "$email — in auth.users, active (role ${r##*|})"; DB_ADMINS_OK=$((DB_ADMINS_OK + 1))
      else
        warn "$email — in auth.users but deactivated: sign-in gives 403 user_deactivated"
      fi
    done <<<"$users"
  fi

  local adm; adm=$(command grep -m1 -F 'ADM|' <<<"$rows" | tr -dc '0-9')
  if [ "$DB_ADMINS_OK" -eq 0 ] && [ -n "$adm" ] && [ "$adm" -gt 0 ]; then
    ok "$adm active ADMIN user(s) in auth.users (the Cognito group has no members — normal when a corporate IdP carries the group claim)"
    DB_ADMINS_OK="$adm"
  fi
  [ "$DB_ADMINS_OK" -gt 0 ] || bad "no active ADMIN in auth.users and no provisioned member of $groups — a sign-in would end in 403"
}

show_header() {
  hdr "US-12 admin 콘솔 Cognito 로그인 — env $DEPLOY_ENV · $STEP"
  printf '  admin-ui    https://%s\n' "$UI_HOST"
  printf '  Cognito     pool %s · client %s\n' "$POOL_ID" "$CLIENT_ID"
  printf '  Hosted UI   https://%s\n' "$HOSTED"
  printf '  callback    %s\n' "$CALLBACK"
  printf '  values      %s\n' "$V"
}

load_env LV live_env
LIVE_LOGIN=1
for k in "${LOGIN_KEYS[@]}"; do [ "${LV[admin-ui/$k]:-}" = "${WANT[$k]}" ] || LIVE_LOGIN=0; done

# ── Steps ───────────────────────────────────────────────────────────────────
step_status() {
  load_env RV render_env "$V"
  local k rl=1
  for k in "${LOGIN_KEYS[@]}"; do [ "${RV[admin-ui/$k]:-}" = "${WANT[$k]}" ] || rl=0; done
  local r_api="${RV[admin-api/DEV_LOGIN_ENABLED]:-unset}" r_ui="${RV[admin-ui/DEV_LOGIN_ENABLED]:-unset}"
  local l_api="${LV[admin-api/DEV_LOGIN_ENABLED]:-unset}" l_ui="${LV[admin-ui/DEV_LOGIN_ENABLED]:-unset}"
  local next=""

  hdr "① callback (Cognito app client)"
  if [ "$CB_LIVE" = 1 ]; then ok "registered"
  else
    local tfv; tfv=$(tf_var_callbacks) || { bad "terraform console could not read var.cognito_callback_urls"; tfv=""; }
    if [[ $'\n'"$tfv"$'\n' == *$'\n'"$CALLBACK"$'\n'* ]]; then
      warn "in terraform.tfvars, not applied — terraform plan / apply in $TF_DIR"
      next="terraform plan → apply ($TF_DIR)"
    else
      bad "not registered"; next="bash $(basename "$0") callback"
    fi
  fi

  hdr "② Cognito login (admin-ui OIDC_*)"
  if [ "$rl" = 1 ]; then ok "values: 4 keys render"; else bad "values: not set (or different)"; fi
  if [ "$LIVE_LOGIN" = 1 ]; then ok "live: admin-ui runs with them"
  elif [ "$rl" = 1 ]; then warn "live: not deployed yet — install-eks.sh $DEPLOY_ENV"; : "${next:=./deployment/scripts/install-eks.sh $DEPLOY_ENV, then verify}"
  else bad "live: not set"; : "${next:=bash $(basename "$0") login}"; fi

  hdr "③ dev-login (DEV_LOGIN_ENABLED)"
  printf '  values  admin-api %-6s admin-ui %s\n' "$r_api" "$r_ui"
  printf '  live    admin-api %-6s admin-ui %s\n' "$l_api" "$l_ui"
  if [ "$l_api" != true ] && [ "$l_ui" != true ]; then
    if [ "$LIVE_LOGIN" = 1 ]; then ok "off — Cognito is the only way in"
    else bad "off, but the Cognito login is not live — nobody can sign in to the admin console"; fi
  elif [ "$r_api" != true ] && [ "$r_ui" != true ]; then
    warn "values off, not deployed yet — install-eks.sh $DEPLOY_ENV"; : "${next:=./deployment/scripts/install-eks.sh $DEPLOY_ENV, then verify}"
  else
    note "on (expected until the Cognito login is verified)"
    [ "$LIVE_LOGIN" = 1 ] && : "${next:=verify + browser login, then bash $(basename "$0") dev-login-off}"
  fi

  hdr "Admins in the DB (Cognito login needs them in auth.users)"
  if [ "$NO_DB" = 1 ]; then note "skipped (--no-db)"; else check_admin_db; fi

  hdr "Next"
  printf '  %s\n\n' "${next:-nothing — US-12 is complete}"
}

step_callback() {
  if [ "$CB_LIVE" = 1 ]; then ok "callback already registered on client $CLIENT_ID — nothing to do"; echo; exit 0; fi
  local tfv; tfv=$(tf_var_callbacks) \
    || die "terraform console could not read var.cognito_callback_urls in $TF_DIR"
  if [[ $'\n'"$tfv"$'\n' == *$'\n'"$CALLBACK"$'\n'* ]]; then
    ok "terraform.tfvars already has it — only terraform plan / apply is left"; echo; exit 0
  fi
  # A tfvars attribute is a top-level `name =`; a second assignment is a
  # terraform error, which the check after writing would catch anyway.
  local f
  for f in "$TFVARS" "$TF_DIR"/*.auto.tfvars; do
    [ -f "$f" ] || continue
    if command grep -Eq '^[[:space:]]*cognito_callback_urls[[:space:]]*=' "$f"; then
      die "$(basename "$f") already sets cognito_callback_urls — add this line to that list by hand:
       \"$CALLBACK\",
     then: terraform plan (expect 1 in-place change) → apply"
    fi
  done

  local block u
  block=$'\n'"# US-12 admin 콘솔 Cognito 로그인 — 19-admin-login.sh callback (${TS})"$'\n'
  block+="# 기존 항목(gateway-cli login 의 localhost 콜백)은 지우지 말 것 — 직원 로그인이 깨진다"$'\n'
  block+="cognito_callback_urls = ["$'\n'
  while IFS= read -r u; do [ -n "$u" ] && block+="  \"$u\","$'\n'; done <<<"$tfv"
  block+="  \"$CALLBACK\","$'\n'"]"$'\n'

  hdr "Planned change — append to $(basename "$TFVARS")"
  local shown=${block#$'\n'}; sed 's/^/  + /' <<<"${shown%$'\n'}"
  if [ "$APPLY" = 0 ]; then
    printf '\n  Nothing written yet. Apply:  bash %s callback --apply\n\n' "$(basename "$0")"; exit 0
  fi
  confirm "Appending cognito_callback_urls to $TFVARS (backup kept in $SNAP_DIR)."
  local bak="$SNAP_DIR/${TS}-tfvars-$DEPLOY_ENV-19-callback.bak"
  cp "$TFVARS" "$bak" || die "backup failed"
  [ -z "$(tail -c1 "$TFVARS")" ] || echo >> "$TFVARS"
  printf '%s' "$block" >> "$TFVARS"

  local want got
  want=$(printf '%s\n%s' "$tfv" "$CALLBACK" | sed '/^$/d')
  got=$(tf_var_callbacks) || true
  if [ "$got" != "$want" ]; then
    cp "$bak" "$TFVARS"
    die "terraform does not read the new list back (got: ${got:-nothing}) — tfvars restored from $bak
     (a *.auto.tfvars overriding it, or the state locked by another terraform run?)"
  fi
  ok "terraform.tfvars updated — terraform reads $(wc -l <<<"$want") callback URLs"
  note "backup: $bak"
  hdr "Next — terraform (you run it, in $TF_DIR)"
  cat <<EOT
  terraform plan -no-color 2>/dev/null | grep -E '^\s*# .* (will be|must be)|^Plan:'
    expect exactly:
      # module.cognito.aws_cognito_user_pool_client.cli will be updated in-place
      Plan: 0 to add, 1 to change, 0 to destroy.
    anything else: stop (ops/8-L-admin-login.md)
  terraform apply
  then: bash $(basename "$0") login

EOT
}

step_login() {
  [ "$CB_LIVE" = 1 ] || die "the callback is not registered on Cognito client $CLIENT_ID yet.
     Run: bash $(basename "$0") callback --apply, then terraform plan / apply in $TF_DIR"
  local -a t=()
  local k
  for k in "${LOGIN_KEYS[@]}"; do t+=(adminUi "$k" "${WANT[$k]}"); done
  apply_values_edit "Writing 4 OIDC_* keys under adminUi.env in $V (backup kept in $SNAP_DIR)." "${t[@]}" || { echo; exit 0; }
  hdr "Next"
  cat <<EOT
  cd $ROOT && ./deployment/scripts/install-eks.sh $DEPLOY_ENV   # admin-ui rolls
  then: bash $(basename "$0") verify   → browser: https://$UI_HOST
  dev-login stays on until you run dev-login-off — it is the way back in.

EOT
}

step_devoff() {
  [ "$LIVE_LOGIN" = 1 ] || die "the Cognito login is not live on $DEP_UI yet.
     Turning dev-login off now would lock everyone out of the admin console.
     First: bash $(basename "$0") login --apply → install-eks.sh $DEPLOY_ENV → verify → browser login"
  hdr "Admins in the DB"
  check_admin_db
  [ "$DB_ADMINS_OK" -gt 0 ] || die "no active admin in auth.users — a Cognito login would end in 403. Fix that first."
  apply_values_edit "Did you sign in at https://$UI_HOST through Cognito and reach the dashboard? dev-login off in adminApi.env + adminUi.env of $V." \
    adminApi DEV_LOGIN_ENABLED false adminUi DEV_LOGIN_ENABLED false || { echo; exit 0; }
  hdr "Next"
  cat <<EOT
  cd $ROOT && ./deployment/scripts/install-eks.sh $DEPLOY_ENV   # admin-api + admin-ui roll
  then: bash $(basename "$0") verify   (dev-login must answer 503)

EOT
}

step_verify() {
  local image port base pod phase out
  image=$(kubectl get deploy "$DEP_UI" -n "$NS" -o jsonpath='{.spec.template.spec.containers[0].image}' 2>/dev/null)
  port=$(kubectl get svc "$DEP_UI" -n "$NS" -o jsonpath='{.spec.ports[0].port}' 2>/dev/null)
  [ -n "$image" ] && [ -n "$port" ] || die "cannot read $DEP_UI Deployment image / Service port"
  base="http://$DEP_UI.$NS.svc.cluster.local:$port"

  # Runs in a throwaway pod on the admin-ui image (node is there, and it pulls
  # from ECR like the real pods). Host + X-Forwarded-Proto make admin-ui answer
  # as it does behind the ALB. Then the authorize URL it hands out is sent to
  # Cognito itself: an unregistered callback comes back as error=redirect_mismatch.
  local js
  js=$(base64 -w0 <<'JS'
const http = require('http'), https = require('https');
function get(url, headers) {
  return new Promise((resolve) => {
    const u = new URL(url);
    const req = (u.protocol === 'https:' ? https : http).request(u, { headers, timeout: 15000 }, (res) => {
      res.resume(); resolve({ status: res.statusCode, location: res.headers.location || '' });
    });
    req.on('timeout', () => req.destroy(new Error('timeout')));
    req.on('error', (e) => resolve({ status: 0, location: '', error: String(e.message || e) }));
    req.end();
  });
}
(async () => {
  const h = { Host: process.env.UI_HOST, 'X-Forwarded-Proto': 'https' }, out = {};
  out.root = await get(process.env.BASE + '/', h);
  out.login = await get(process.env.BASE + '/api/auth/login', h);
  out.devLogin = await get(process.env.BASE + '/api/auth/dev-login', h);
  if (/^https:\/\//.test(out.login.location)) out.cognito = await get(out.login.location, {});
  console.log('PROBE ' + JSON.stringify(out));
})();
JS
)
  pod="admin-login-probe-$(date +%s)-$RANDOM"
  hdr "Probe from inside the cluster ($pod, ~1-2 min on Fargate)"
  kubectl run "$pod" -n "$NS" --restart=Never --image="$image" --pod-running-timeout=5m \
    --env="JSB64=$js" --env="BASE=$base" --env="UI_HOST=$UI_HOST" \
    --command -- node -e 'eval(Buffer.from(process.env.JSB64, "base64").toString())' >/dev/null 2>&1 \
    || die "could not start the probe pod"
  for _ in $(seq 1 90); do
    phase=$(kubectl get pod "$pod" -n "$NS" -o jsonpath='{.status.phase}' 2>/dev/null)
    [ "$phase" = Succeeded ] || [ "$phase" = Failed ] && break
    sleep 4
  done
  out=$(kubectl logs "$pod" -n "$NS" 2>&1)
  kubectl delete pod "$pod" -n "$NS" --wait=false >/dev/null 2>&1
  local probe; probe=$(command grep -m1 '^PROBE ' <<<"$out")
  [ -n "$probe" ] || { sed 's/^/  | /' <<<"$out"; die "probe pod gave no result (phase: ${phase:-unknown})"; }

  local lvl msg
  while IFS=$'\t' read -r lvl msg; do
    case "$lvl" in ok) ok "$msg" ;; warn) warn "$msg" ;; bad) bad "$msg" ;; *) note "$msg" ;; esac
  done < <(python3 - "${probe#PROBE }" "$UI_HOST" "$AUTHORIZE_URL" "$CLIENT_ID" "$CALLBACK" \
             "$LIVE_LOGIN" "${LV[admin-ui/DEV_LOGIN_ENABLED]:-unset}" <<'PY'
import json, sys
from urllib.parse import urlsplit, parse_qs
p, host, authz, cid, cb, live_login, ui_dev = sys.argv[1:]
d = json.loads(p)
def say(l, m): print(f"{l}\t{m}")
r = d.get("root", {})
if r.get("status") in (302, 307) and r.get("location", "").endswith("/api/auth/login"):
    say("ok", f"/  → {r['status']} → /api/auth/login (no cookie = sent to sign in)")
else:
    say("bad", f"/  → {r.get('status')} {r.get('location') or r.get('error', '')}")
lg = d.get("login", {})
loc = lg.get("location", "")
if live_login == "1":
    q = parse_qs(urlsplit(loc).query)
    if lg.get("status") == 302 and loc.startswith(authz + "?") and q.get("client_id") == [cid] and q.get("redirect_uri") == [cb]:
        say("ok", "/api/auth/login → 302 Cognito authorize (client_id, redirect_uri match)")
    else:
        say("bad", f"/api/auth/login → {lg.get('status')} {loc or lg.get('error', '')}")
else:
    say("warn", f"/api/auth/login → {lg.get('status')} {loc} — Cognito login not live yet (login step)")
cg = d.get("cognito")
if cg is not None:
    cl = cg.get("location", "")
    if cg.get("status") == 302 and "error" not in parse_qs(urlsplit(cl).query) and not urlsplit(cl).path.startswith("/error"):
        say("ok", "Cognito accepted the authorize request → its sign-in page (callback registered)")
    else:
        err = parse_qs(urlsplit(cl).query).get("error", [cg.get("error", "")])[0]
        say("bad", f"Cognito refused: {cg.get('status')} {err or cl} — callback not registered? (callback step + terraform apply)")
dv = d.get("devLogin", {})
if ui_dev == "true":
    if dv.get("status") == 200:
        say("note", "/api/auth/dev-login → 200: dev-login still open (off: dev-login-off)")
    else:
        say("warn", f"/api/auth/dev-login → {dv.get('status')} although DEV_LOGIN_ENABLED=true")
else:
    if dv.get("status") == 503:
        say("ok", "/api/auth/dev-login → 503: dev-login closed")
    else:
        say("bad", f"/api/auth/dev-login → {dv.get('status')} — expected 503 with dev-login off")
PY
)
  echo
  note "The last check is a person: open https://$UI_HOST in a private window → Cognito → dashboard."
  note "An internal admin ALB (US-07/US-08) is reachable only from a VPN-connected PC."
  echo
}

show_header
case "$STEP" in
  status)        step_status ;;
  callback)      step_callback ;;
  login)         step_login ;;
  dev-login-off) step_devoff ;;
  verify)        step_verify ;;
esac
