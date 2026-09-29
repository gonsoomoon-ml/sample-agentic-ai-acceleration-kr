#!/bin/bash
# ---------------------------------------------------------------------------
# 20-enable-body-logging.sh
#
# WHAT: open the infra gate for request/response body logging — US-15,
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

# ── Reads ───────────────────────────────────────────────────────────────────
tfvars_enabled()  { grep -Eq '^[[:space:]]*enable_body_logging[[:space:]]*=[[:space:]]*true' "$TFVARS"; }
tfvars_declared() { grep -Eq '^[[:space:]]*enable_body_logging[[:space:]]*=' "$TFVARS"; }

tf_output() { terraform -chdir="$TF_DIR" output -raw "$1" 2>/dev/null || true; }

pod_env() {
  kubectl get deploy "$DEP_PROXY" -n "$NS" \
    -o jsonpath='{range .spec.template.spec.containers[0].env[*]}{.name}{"="}{.value}{"\n"}{end}' \
    2>/dev/null | grep -E '^(FIREHOSE_STREAM_NAME|BODY_LOG_S3_BUCKET)=' || true
}

values_env_has() {
  python3 - "$V" "$1" <<'PY'
import re, sys
path, key = sys.argv[1:3]
lines = open(path).read().splitlines()
gp = next((i for i, l in enumerate(lines) if re.match(r'^gatewayProxy:\s*$', l)), None)
if gp is None:
    sys.exit(1)
end = next((i for i in range(gp + 1, len(lines)) if re.match(r'^[a-zA-Z]', lines[i])), len(lines))
env = next((i for i in range(gp + 1, end) if re.match(r'^\s{2}env:\s*$', lines[i])), None)
if env is None:
    sys.exit(1)
for l in lines[env + 1:end]:
    if re.match(rf'^\s{{3,}}{re.escape(key)}:', l):
        sys.exit(0)
sys.exit(1)
PY
}

# gatewayProxy.env 에 KEY: VALUE 를 삽입/치환 (line 기반, PyYAML 불요)
set_env_kv() {
  python3 - "$V" "$1" "$2" <<'PY'
import re, sys
path, key, val = sys.argv[1:4]
lines = open(path).read().splitlines()
gp = next(i for i, l in enumerate(lines) if re.match(r'^gatewayProxy:\s*$', l))
end = next((i for i in range(gp + 1, len(lines)) if re.match(r'^[a-zA-Z]', lines[i])), len(lines))
env = next((i for i in range(gp + 1, end) if re.match(r'^\s{2}env:\s*$', lines[i])), None)
if env is None:
    print(f"ERROR: gatewayProxy.env block not found in {path}", file=sys.stderr)
    sys.exit(1)
env_end = end
for i in range(env + 1, end):
    if not re.match(r'^\s{3,}|^\s*$', lines[i]):
        env_end = i
        break
kv = f"    {key}: {val}"
pat = re.compile(rf'^\s{{3,}}{re.escape(key)}:')
for i in range(env + 1, env_end):
    if pat.match(lines[i]):
        lines[i] = kv
        break
else:
    lines.insert(env + 1, kv)
open(path, 'w').write("\n".join(lines) + "\n")
PY
}

unset_env_kv() {
  python3 - "$V" "$1" <<'PY'
import re, sys
path, key = sys.argv[1:3]
lines = open(path).read().splitlines()
pat = re.compile(rf'^\s+{re.escape(key)}:')
out = [l for l in lines if not pat.match(l)]
open(path, 'w').write("\n".join(out) + "\n")
PY
}

# ── Status (read-only) ─────────────────────────────────────────────────────
print_status() {
  hdr "Body-logging status (env=$DEPLOY_ENV)"
  if tfvars_enabled; then ok "tfvars: enable_body_logging = true"
  elif tfvars_declared; then warn "tfvars: declared but not true"
  else warn "tfvars: enable_body_logging not declared (default false — no sink)"; fi

  STREAM="$(tf_output body_log_firehose_stream)"
  BUCKET="$(tf_output body_log_bucket)"
  [ -n "${STREAM:-}" ] && ok "terraform output stream: $STREAM" || warn "terraform output body_log_firehose_stream: none"
  [ -n "${BUCKET:-}" ] && ok "terraform output bucket: $BUCKET" || warn "terraform output body_log_bucket: none"

  PENV="$(pod_env)"
  [ -n "$PENV" ] && { ok "pod env:"; sed 's/^/       /' <<<"$PENV"; } \
    || warn "pod env: FIREHOSE_STREAM_NAME / BODY_LOG_S3_BUCKET not set (infra gate closed)"

  values_env_has FIREHOSE_STREAM_NAME && values_env_has BODY_LOG_S3_BUCKET \
    && ok "values file has both keys ($V)" \
    || warn "values file missing one/both keys: $V"
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
  [ $rc -eq 0 ] && echo && ok "READY — turning on the admin toggle starts collection" \
                || echo && bad "NOT READY — fix the XX items above"
  exit $rc
  ;;

tfvars)
  if tfvars_enabled; then
    ok "enable_body_logging = true already in $TFVARS — nothing to write"
    note "next: terraform plan → apply in $TF_DIR, then env step"
    exit 0
  fi
  if tfvars_declared; then
    hdr "Planned change — flip enable_body_logging to true in $(basename "$TFVARS")"
    echo "  ~ enable_body_logging = false → true"
  else
    hdr "Planned change — append to $(basename "$TFVARS")"
    cat <<'EOT'
  + # 본문 로깅 sink — 20-enable-body-logging.sh tfvars (수집 on/off 는 admin 토글)
  + enable_body_logging = true
EOT
  fi
  if [ "$APPLY" = 0 ]; then
    printf '\n  Nothing written yet. Apply:  bash %s tfvars --apply\n\n' "$(basename "$0")"; exit 0
  fi
  confirm "Writing enable_body_logging to $TFVARS (backup kept in $SNAP_DIR)."
  bak="$SNAP_DIR/${TS}-tfvars-$DEPLOY_ENV-20-bodylog.bak"
  cp "$TFVARS" "$bak" || die "backup failed"
  if tfvars_declared; then
    python3 - "$TFVARS" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p).read()
s = re.sub(r'(?m)^(\s*enable_body_logging\s*=\s*).+$', r'\g<1>true', s)
open(p, 'w').write(s)
PY
  else
    [ -z "$(tail -c1 "$TFVARS")" ] || echo >> "$TFVARS"
    cat >> "$TFVARS" <<'EOF'

# 본문 로깅 sink — 20-enable-body-logging.sh tfvars (수집 on/off 는 admin 토글)
enable_body_logging = true
EOF
  fi
  tfvars_enabled || { cp "$bak" "$TFVARS"; die "tfvars write did not take — restored from $bak"; }
  ok "terraform.tfvars updated"; note "backup: $bak"
  hdr "Next — terraform (you run it, in $TF_DIR)"
  cat <<EOT
  terraform plan -target=module.body_logging -target=module.irsa
    expect: S3 bucket + Firehose stream + IAM additions only — anything else: stop (ops/8-V)
  terraform apply
  then: bash $(basename "$0") env
EOT
  ;;

env)
  STREAM="$(tf_output body_log_firehose_stream)"
  BUCKET="$(tf_output body_log_bucket)"
  [ -n "${STREAM:-}" ] && [ -n "${BUCKET:-}" ] \
    || die "terraform outputs body_log_firehose_stream / body_log_bucket are empty —
     module.body_logging not applied yet? run: bash $(basename "$0") tfvars --apply, then terraform plan/apply"

  if values_env_has FIREHOSE_STREAM_NAME && values_env_has BODY_LOG_S3_BUCKET; then
    ok "values already has both keys — nothing to write"
    note "next: ./deployment/scripts/install-eks.sh $DEPLOY_ENV"
    exit 0
  fi
  hdr "Planned change — gatewayProxy.env in $(basename "$V")"
  printf '  + FIREHOSE_STREAM_NAME: "%s"\n  + BODY_LOG_S3_BUCKET: "%s"\n' "$STREAM" "$BUCKET"
  if [ "$APPLY" = 0 ]; then
    printf '\n  Nothing written yet. Apply:  bash %s env --apply\n\n' "$(basename "$0")"; exit 0
  fi
  confirm "Writing body-logging env to $V (backup kept in $SNAP_DIR)."
  bak="$SNAP_DIR/${TS}-values-$DEPLOY_ENV-20-bodylog.bak"
  cp "$V" "$bak" || die "backup failed"
  set_env_kv FIREHOSE_STREAM_NAME "\"$STREAM\""
  set_env_kv BODY_LOG_S3_BUCKET "\"$BUCKET\""

  # helm 렌더 검증 — env 가 실제 매니페스트에 타는지 확인한다
  rendered=$(helm template "$HELM_RELEASE" "$CHART" --values "$V" 2>/dev/null \
    | grep -c "FIREHOSE_STREAM_NAME") || true
  [ "${rendered:-0}" -ge 1 ] || { cp "$bak" "$V"; die "helm render lacks FIREHOSE_STREAM_NAME — values restored from $bak"; }
  ok "values updated; helm render carries the keys"; note "backup: $bak"
  hdr "Next — deploy (you run it)"
  cat <<EOT
  ./deployment/scripts/install-eks.sh $DEPLOY_ENV
    (never a bare helm upgrade — install-eks.sh re-injects the terraform-output
     --set values; a plain -f upgrade would roll them back to placeholders)
  then: bash $(basename "$0") verify  →  admin /monitoring body logging toggle ON
EOT
  ;;

disable)
  if ! values_env_has FIREHOSE_STREAM_NAME && ! values_env_has BODY_LOG_S3_BUCKET; then
    ok "values has no body-logging keys — nothing to write"
    exit 0
  fi
  hdr "Planned change — remove gatewayProxy.env keys from $(basename "$V")"
  echo "  - FIREHOSE_STREAM_NAME / BODY_LOG_S3_BUCKET"
  if [ "$APPLY" = 0 ]; then
    printf '\n  Nothing written yet. Apply:  bash %s disable --apply\n\n' "$(basename "$0")"; exit 0
  fi
  confirm "Removing body-logging env from $V (backup kept in $SNAP_DIR)."
  bak="$SNAP_DIR/${TS}-values-$DEPLOY_ENV-20-disable.bak"
  cp "$V" "$bak" || die "backup failed"
  unset_env_kv FIREHOSE_STREAM_NAME
  unset_env_kv BODY_LOG_S3_BUCKET
  ok "values updated"; note "backup: $bak"
  cat <<EOT

  Next: ./deployment/scripts/install-eks.sh $DEPLOY_ENV   — proxy env 제거로 수집 중지.
  S3 버킷/Firehose 는 남는다 — 버킷엔 프롬프트 본문이 있을 수 있어(force_destroy=false)
  객체 비우기 후 terraform 에서 수동 처리.
EOT
  ;;

esac
