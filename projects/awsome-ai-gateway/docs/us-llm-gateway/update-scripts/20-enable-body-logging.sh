#!/bin/bash
# ---------------------------------------------------------------------------
# 20-enable-body-logging.sh
#
# WHAT: open the infra gate for request/response body logging —
#       docs/us-llm-gateway/ops/8-V-body-logging.md.
#         (no step)  status: tfvars flag + terraform outputs + pod env + values
#         tfvars     append enable_body_logging = true to terraform.tfvars
#                    (terraform plan/apply stays a manual step)
#         env        put FIREHOSE_STREAM_NAME + BODY_LOG_S3_BUCKET under
#                    gatewayProxy.env in the helm values file
#         verify     read-only: bucket exists / stream ACTIVE / pod env set
#         disable    remove the two env keys (collection stops; infra stays)
# WHY:  body logging is gated twice — the admin /monitoring toggle only works
#       when the proxy pod actually has the sink env vars. The values file is
#       the source of truth because deploys go through install-eks.sh, which
#       re-injects terraform outputs via --set on every run: a bare
#       helm upgrade -f would drop those injections and roll release values
#       back to placeholders. So this script writes the two keys into
#       values-eks-fargate-<env>.yaml and leaves the rollout to
#       install-eks.sh — exactly like 19-admin-login.sh leaves helm to it.
# UNDO: restore the backup --apply prints (snapshots/), then install-eks.sh
#       <env> for values, or edit terraform.tfvars for the flag.
#
# ⚠️  When collection is ON, full request JSON and streaming SSE text are
#     stored to S3 unmasked — user prompts included. Review privacy/security
#     before enabling the admin toggle. Objects expire via lifecycle (90d).
#
# Usage:
#   bash 20-enable-body-logging.sh                 # status
#   bash 20-enable-body-logging.sh tfvars [--apply]
#   bash 20-enable-body-logging.sh env [--apply]
#   bash 20-enable-body-logging.sh verify
#   bash 20-enable-body-logging.sh disable [--apply]
#   --values <path>  edit/render another values file (test)
#
# Order: tfvars → terraform plan/apply → env → install-eks.sh <env> → verify
#        → admin /monitoring toggle ON. disable → install-eks.sh <env>.
# ---------------------------------------------------------------------------
set -uo pipefail
source "$(dirname "$(readlink -f "$0")")/_lib.sh"

STEP=status; APPLY=0; VALUES=""
while [ $# -gt 0 ]; do
  case "$1" in
    status|tfvars|env|verify|disable) STEP="$1"; shift ;;
    --apply)   APPLY=1; shift ;;
    --values)  VALUES="$2"; shift 2 ;;
    -h|--help) sed -n '2,38p' "$0"; exit 0 ;;
    *) die "Unknown argument: $1 (see --help)" ;;
  esac
done
case "$STEP" in status|verify) [ "$APPLY" = 0 ] || die "--apply goes with tfvars | env | disable" ;; esac

require_env
for c in helm python3 terraform; do
  command -v "$c" >/dev/null 2>&1 || die "$c not found. Run this on the deployer EC2."
done

ROOT="$(cd "$LIB_DIR/../../.." && pwd)"
CHART="$ROOT/deployment/charts/llm-gateway"
V="${VALUES:-${HELM_VALUES_FILE:-$CHART/values-eks-fargate-$DEPLOY_ENV.yaml}}"
[ -f "$V" ] || die "values file not found: $V"
V=$(readlink -f "$V")
TF_DIR="${TF_DIR:-$ROOT/deployment/terraform/environments/llm-gateway-$DEPLOY_ENV}"
TFVARS="$TF_DIR/terraform.tfvars"
[ -d "$TF_DIR" ] || die "terraform env dir not found: $TF_DIR"
[ -f "$TFVARS" ] || die "terraform.tfvars not found: $TFVARS"
DEP_PROXY="${HELM_RELEASE}-gateway-proxy"

BODY_KEYS=(FIREHOSE_STREAM_NAME BODY_LOG_S3_BUCKET)

# ── Reads ───────────────────────────────────────────────────────────────────
# Effective var.enable_body_logging, evaluated the way apply would see it —
# terraform parses tfvars and every *.auto.tfvars, so a grep of terraform.tfvars
# alone can read a different truth than apply would.
tf_var_body_logging() {
  local out
  out=$(echo 'var.enable_body_logging' | terraform -chdir="$TF_DIR" console 2>/dev/null | tail -1)
  [ "$out" = true ] || [ "$out" = false ] || return 1
  printf '%s\n' "$out"
}

# Where the attribute is assigned (terraform.tfvars or a *.auto.tfvars).
tf_attr_files() {
  command grep -lE '^[[:space:]]*enable_body_logging[[:space:]]*=' \
    "$TFVARS" "$TF_DIR"/*.auto.tfvars 2>/dev/null || true
}

tf_output() { terraform -chdir="$TF_DIR" output -raw "$1" 2>/dev/null || true; }

# "KEY<TAB>value" for the gateway-proxy container env, from the chart rendered
# with <values>. Reading the render (not the file) is what makes inherited
# values from a parent values file visible — same rule as 15·17·19.
render_proxy_env() {
  local out
  out=$(helm template "$HELM_RELEASE" "$CHART" -f "$1" 2>&1) \
    || { printf 'helm template failed for %s:\n%s\n' "$1" "$(tail -5 <<<"$out")" >&2; return 1; }
  python3 -c '
import sys, yaml
for d in yaml.safe_load_all(sys.stdin):
    if d and d.get("kind") == "Deployment" and d["metadata"]["name"].endswith("-gateway-proxy"):
        for e in d["spec"]["template"]["spec"]["containers"][0].get("env") or []:
            if "value" in e:
                print(e["name"] + "\t" + str(e["value"]))
        break
' <<<"$out"
}

declare -A RV   # rendered: RV[FIREHOSE_STREAM_NAME]=...
# load_rendered <map> <values-file> — nameref 로 채운다. 전역 RV 대신 지역
# 맵을 받을 수 있어야 apply_values_edit 의 NV 체크가 실제로 동작한다
# (NV 없이 RV 만 채우면 비교가 항상 빈 값과 한다 — env --apply 가 매번 die).
load_rendered() {
  local -n _m="$1"; shift
  local out k v
  out=$(render_proxy_env "$1") || die "could not render env — see the message above"
  while IFS=$'\t' read -r k v; do [ -n "$k" ] && _m["$k"]="$v"; done <<<"$out"
}

# KEY=value lines of the two body-logging keys as the running pod has them —
# empty values included so a key that is present-but-empty stays visible.
pod_env() {
  kubectl get deploy "$DEP_PROXY" -n "$NS" \
    -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}{"="}{.value}{"\n"}{end}' \
    2>/dev/null | grep -E '^(FIREHOSE_STREAM_NAME|BODY_LOG_S3_BUCKET)=' || true
}

# Both keys set to non-empty values in the rendered chart → the infra gate
# shows as open in the file/deploy inputs.
rendered_gate_open() {
  [ -n "${RV[FIREHOSE_STREAM_NAME]:-}" ] && [ -n "${RV[BODY_LOG_S3_BUCKET]:-}" ]
}

# ── Values edit (text edit, never a YAML round-trip — comments are docs) ─────
# <src> <dst> then triples <op> <KEY> <value>; op is set|del, value is "" on del.
# Keys live at indent 4 under gatewayProxy: -> env: (indent 2). 'del' touches
# only lines inside that env block — a same-named key anywhere else survives.
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

for n in range(0, len(ops), 3):
    op, key, val = ops[n:n + 3]
    if op not in ("set", "del"):
        sys.exit(f"edit_values: unknown op '{op}'")
    i, j = env_block("gatewayProxy")
    hits = [k for k in range(i + 1, j) if code(L[k]) and ind(L[k]) == 4 and re.match(rf"^\s*{key}:", L[k])]
    if len(hits) > 1:
        sys.exit(f"values: gatewayProxy.env.{key} appears {len(hits)} times — fix the file first")
    if hits:
        # 다음 코드 라인이 이 키보다 깊게 들어가 있으면 다중행 값 — 첫 줄만
        # 바꾸면 나머지가 고아가 되므로 수동 편집을 요구한다.
        nxt = hits[0] + 1
        while nxt < j and not code(L[nxt]):
            nxt += 1
        if nxt < j and ind(L[nxt]) > 4:
            sys.exit(f"values: gatewayProxy.env.{key} is a multi-line value — edit by hand")
    if op == "del":
        if hits:
            del L[hits[0]]
        continue
    line = f"    {key}: {json.dumps(val)}"
    if hits:
        L[hits[0]] = line
        continue
    last = max([k for k in range(i + 1, j) if code(L[k]) and ind(L[k]) >= 4], default=i)
    L[last + 1:last + 1] = [line]

open(dst, "w", encoding="utf-8").write("\n".join(L))
PY
}

# Edit → render → compare with the wanted values → (apply). $1 = label,
# rest = triples for edit_values. The render check compares what the chart
# would produce against the wanted strings — a key name alone proves nothing
# (the chart ships FIREHOSE_STREAM_NAME: "" by default).
apply_values_edit() {
  local label="$1"; shift
  local tmp; tmp=$(mktemp)
  edit_values "$V" "$tmp" "$@" || { rm -f "$tmp"; die "values edit failed — $V untouched"; }
  if cmp -s "$V" "$tmp"; then rm -f "$tmp"; ok "values already carries these — nothing to write"; return 1; fi

  declare -A NV=()
  load_rendered NV "$tmp" # dies with the helm error if the edited file cannot render
  local n=0 op key val bad_n=0
  local -a args=("$@")
  for ((n = 0; n < ${#args[@]}; n += 3)); do
    op=${args[n]}; key=${args[n+1]}; val=${args[n+2]}
    if [ "$op" = del ]; then
      if [ -n "${NV[$key]:-}" ]; then
        bad "$key still renders '${NV[$key]}' — expected removed/empty"; bad_n=$((bad_n + 1))
      fi
    elif [ "${NV[$key]:-}" != "$val" ]; then
      bad "$key renders '${NV[$key]:-(unset)}', expected '$val'"; bad_n=$((bad_n + 1))
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
  local bak="$SNAP_DIR/${TS}-values-$DEPLOY_ENV-20-$STEP.bak"
  cp "$V" "$bak" || { rm -f "$tmp"; die "backup failed"; }
  cp "$tmp" "$V" || { rm -f "$tmp"; die "could not write $V (backup: $bak)"; }
  rm -f "$tmp"
  ok "values updated: $V"
  note "backup: $bak   (undo: cp $bak $V, then install-eks.sh $DEPLOY_ENV)"
  return 0
}

# ── Status (read-only) ─────────────────────────────────────────────────────
print_status() {
  hdr "Body-logging status (env=$DEPLOY_ENV)"
  local flag
  flag=$(tf_var_body_logging) || die "terraform console could not read var.enable_body_logging in $TF_DIR"
  if [ "$flag" = true ]; then ok "tfvars: enable_body_logging = true (effective value)"
  else warn "tfvars: enable_body_logging = false (default — no sink)"; fi

  STREAM="$(tf_output body_log_firehose_stream)"
  BUCKET="$(tf_output body_log_bucket)"
  [ -n "${STREAM:-}" ] && ok "terraform output stream: $STREAM" || warn "terraform output body_log_firehose_stream: none"
  [ -n "${BUCKET:-}" ] && ok "terraform output bucket: $BUCKET" || warn "terraform output body_log_bucket: none"

  PENV="$(pod_env)"
  if [ -n "$PENV" ]; then
    sed 's/^/       /' <<<"$PENV"
    if grep -q '^FIREHOSE_STREAM_NAME=.' <<<"$PENV" && grep -q '^BODY_LOG_S3_BUCKET=.' <<<"$PENV"; then
      ok "pod env: both keys set — infra gate open"
    else
      warn "pod env: key(s) present but empty — infra gate closed (a present-but-empty key is OFF)"
    fi
  else
    warn "pod env: FIREHOSE_STREAM_NAME / BODY_LOG_S3_BUCKET not set (infra gate closed)"
  fi

  load_rendered RV "$V"
  if rendered_gate_open; then
    ok "values render: FIREHOSE_STREAM_NAME=${RV[FIREHOSE_STREAM_NAME]} BODY_LOG_S3_BUCKET=${RV[BODY_LOG_S3_BUCKET]}"
  elif [ -n "${RV[FIREHOSE_STREAM_NAME]+x}" ] || [ -n "${RV[BODY_LOG_S3_BUCKET]+x}" ]; then
    warn "values render: key(s) render but empty — infra gate closed: $V"
  else
    warn "values render: no body-logging keys: $V"
  fi
  note "collection on/off itself is the admin /monitoring body logging toggle"
}

case "$STEP" in

status)
  print_status
  hdr "Next"
  cat <<EOT
  tfvars 미적용 → bash $(basename "$0") tfvars --apply   then terraform plan/apply ($TF_DIR)
  infra 있고 env 없음 → bash $(basename "$0") env --apply   then ./deployment/scripts/install-eks.sh $DEPLOY_ENV
  확인 → bash $(basename "$0") verify
EOT
  ;;

verify)
  print_status
  hdr "Verify"
  rc=0
  BUCKET="$(tf_output body_log_bucket)"
  if [ -n "${BUCKET:-}" ] && aws s3api head-bucket --bucket "$BUCKET" 2>/dev/null; then
    ok "bucket $BUCKET reachable"
  else
    bad "bucket check failed: ${BUCKET:-<none>}"; rc=1
  fi
  STREAM="$(tf_output body_log_firehose_stream)"
  if [ -n "${STREAM:-}" ]; then
    st="$(aws firehose describe-delivery-stream --delivery-stream-name "$STREAM" \
        --query 'DeliveryStreamDescription.DeliveryStreamStatus' --output text 2>/dev/null || echo '?')"
    [ "$st" = "ACTIVE" ] && ok "stream $STREAM: ACTIVE" || { bad "stream $STREAM: $st"; rc=1; }
  else
    bad "stream: none"; rc=1
  fi
  PENV="$(pod_env)"
  if grep -q '^FIREHOSE_STREAM_NAME=.' <<<"$PENV" && grep -q '^BODY_LOG_S3_BUCKET=.' <<<"$PENV"; then
    ok "pod env set — infra gate open"
  else
    bad "pod env missing — infra gate closed"; rc=1
  fi
  echo
  if [ $rc -eq 0 ]; then
    ok "READY — turning on the admin toggle starts collection"
  else
    bad "NOT READY — fix the XX items above"
  fi
  exit $rc
  ;;

tfvars)
  flag=$(tf_var_body_logging) \
    || die "terraform console could not read var.enable_body_logging in $TF_DIR"
  if [ "$flag" = true ]; then
    ok "enable_body_logging = true is already the effective value — nothing to write"
    note "next: terraform plan → apply in $TF_DIR, then env step"
    exit 0
  fi
  declared=$(tf_attr_files)
  if [ -n "$declared" ]; then
    die "enable_body_logging is already assigned in:
$declared
     a second assignment is a terraform error. Flip that line to true by hand,
     then: terraform plan → apply, then: bash $(basename "$0") env"
  fi
  hdr "Planned change — append to $(basename "$TFVARS")"
  cat <<'EOT'
  + # 본문 로깅 sink — 20-enable-body-logging.sh tfvars (수집 on/off 는 admin 토글)
  + enable_body_logging = true
EOT
  if [ "$APPLY" = 0 ]; then
    printf '\n  Nothing written yet. Apply:  bash %s tfvars --apply\n\n' "$(basename "$0")"; exit 0
  fi
  confirm "Writing enable_body_logging to $TFVARS (backup kept in $SNAP_DIR)."
  bak="$SNAP_DIR/${TS}-tfvars-$DEPLOY_ENV-20-bodylog.bak"
  cp "$TFVARS" "$bak" || die "backup failed"
  [ -z "$(tail -c1 "$TFVARS")" ] || echo >> "$TFVARS"
  cat >> "$TFVARS" <<'EOF'

# 본문 로깅 sink — 20-enable-body-logging.sh tfvars (수집 on/off 는 admin 토글)
enable_body_logging = true
EOF
  got=$(tf_var_body_logging) || got=?
  if [ "$got" != true ]; then
    cp "$bak" "$TFVARS"
    die "terraform does not read the new flag back (console says: ${got:-nothing}) — tfvars restored from $bak
     (a *.auto.tfvars overriding it, or the state locked by another terraform run?)"
  fi
  ok "terraform.tfvars updated — terraform reads enable_body_logging = true"
  note "backup: $bak"
  hdr "Next — terraform (you run it, in $TF_DIR)"
  cat <<EOT
  terraform plan -target=module.body_logging -target=module.irsa
    expect: S3 bucket + Firehose stream + IAM additions only — anything else: stop (ops/8-V)
  terraform apply -target=module.body_logging -target=module.irsa
  then: bash $(basename "$0") env
EOT
  ;;

env)
  STREAM="$(tf_output body_log_firehose_stream)"
  BUCKET="$(tf_output body_log_bucket)"
  [ -n "${STREAM:-}" ] && [ -n "${BUCKET:-}" ] \
    || die "terraform outputs body_log_firehose_stream / body_log_bucket are empty —
     module.body_logging not applied yet? run: bash $(basename "$0") tfvars --apply, then terraform plan/apply"

  load_rendered RV "$V"
  if [ "${RV[FIREHOSE_STREAM_NAME]:-}" = "$STREAM" ] && [ "${RV[BODY_LOG_S3_BUCKET]:-}" = "$BUCKET" ]; then
    ok "values already renders the terraform outputs — nothing to write"
    note "next: ./deployment/scripts/install-eks.sh $DEPLOY_ENV"
    exit 0
  fi
  apply_values_edit "Writing body-logging env under gatewayProxy.env in $V (backup kept in $SNAP_DIR)." \
    set FIREHOSE_STREAM_NAME "$STREAM" set BODY_LOG_S3_BUCKET "$BUCKET" || { echo; exit 0; }
  hdr "Next — deploy (you run it)"
  cat <<EOT
  ./deployment/scripts/install-eks.sh $DEPLOY_ENV
    (never a bare helm upgrade — install-eks.sh re-injects the terraform-output
     --set values; a plain -f upgrade would roll them back to placeholders)
  then: bash $(basename "$0") verify  →  admin /monitoring body logging toggle ON
EOT
  ;;

disable)
  load_rendered RV "$V"
  # 렌더가 비어도 파일에 키 라인이 남아 있을 수 있다(KEY: "" 잔류) — 그 경우에도
  # 실제로 지워야 하므로 파일 자체에서 키 존재를 확인한다.
  if [ -z "${RV[FIREHOSE_STREAM_NAME]:-}" ] && [ -z "${RV[BODY_LOG_S3_BUCKET]:-}" ] \
     && ! grep -qE '^[[:space:]]+(FIREHOSE_STREAM_NAME|BODY_LOG_S3_BUCKET):' "$V"; then
    ok "values has no body-logging keys (or empty) — nothing to write"
    exit 0
  fi
  apply_values_edit "Removing body-logging env from gatewayProxy.env in $V (backup kept in $SNAP_DIR)." \
    del FIREHOSE_STREAM_NAME "" del BODY_LOG_S3_BUCKET "" || { echo; exit 0; }
  cat <<EOT

  Next: ./deployment/scripts/install-eks.sh $DEPLOY_ENV   — proxy env 제거로 수집 중지.
  S3 버킷/Firehose 는 남는다 — 버킷엔 프롬프트 본문이 있을 수 있어(force_destroy=false)
  객체 비우기 후 terraform 에서 수동 처리.
EOT
  ;;

esac
