#!/bin/bash
# ---------------------------------------------------------------------------
# 21-set-notification-provider.sh
#
# WHAT: switch the notification-worker's email provider — edits
#       notificationWorker.email.* in values-eks-fargate-<env>.yaml with yq.
#         (no arg)   status: current values block + live pod EMAIL_SENDER_TYPE
#         mock       provider = mock (no real sending; default for dev/test)
#         internal-api   provider = internal_api — needs --url
#         smtp       provider = smtp — needs --host and --from
#         ses        provider = ses — needs 22-setup-notification-ses-irsa.sh
#                    (IRSA role) applied first
# WHY:  the values file is the source of truth because deploys go through
#       install-eks.sh, which re-injects terraform outputs via --set on every
#       run — a bare helm upgrade -f would roll those values back to
#       placeholders. This script edits the file with yq (a structured YAML
#       tool — values are never touched by grep/sed/regex) and leaves the
#       rollout to install-eks.sh, exactly like 20-enable-body-logging.sh.
# NOTE: the chart reads SMTP credentials ONLY from
#       email.smtp.credentialsSecretName — writing the key anywhere else
#       silently drops SMTP auth.
# UNDO: restore the backup --apply prints (snapshots/), or re-run with the
#       previous provider, then install-eks.sh <env>.
#
# Usage:
#   bash 21-set-notification-provider.sh                 # status
#   bash 21-set-notification-provider.sh mock --apply
#   bash 21-set-notification-provider.sh internal-api --apply \
#       --url http://mail-api.internal/send [--from ADDR] [--from-name NAME]
#   bash 21-set-notification-provider.sh smtp --apply \
#       --host smtp.example.com [--port 587] [--starttls true|false] \
#       --from ADDR [--credentials-secret SECRET]
#   bash 21-set-notification-provider.sh ses --apply \
#       [--region us-east-1] [--from ADDR] [--from-name NAME]
#   --values <path>  edit/render another values file (test)
#
# Order: (ses only) 22-setup-notification-ses-irsa.sh --apply
#        → 21-set-notification-provider.sh <provider> --apply
#        → install-eks.sh <env> → status.sh (SES probe row)
# ---------------------------------------------------------------------------
set -uo pipefail
source "$(dirname "$(readlink -f "$0")")/_lib.sh"

STEP=status; APPLY=0; VALUES=""
URL=""; FROM_ADDR=""; FROM_NAME=""
SMTP_HOST=""; SMTP_PORT=587; SMTP_STARTTLS=true; SMTP_SECRET=""
SES_REGION=""
while [ $# -gt 0 ]; do
  case "$1" in
    status|mock|internal-api|internal_api|smtp|ses) STEP="$1"; shift ;;
    --apply)               APPLY=1; shift ;;
    --values)              VALUES="$2"; shift 2 ;;
    --url)                 URL="$2"; shift 2 ;;
    --from)                FROM_ADDR="$2"; shift 2 ;;
    --from-name)           FROM_NAME="$2"; shift 2 ;;
    --host)                SMTP_HOST="$2"; shift 2 ;;
    --port)                SMTP_PORT="$2"; shift 2 ;;
    --starttls)            SMTP_STARTTLS="$2"; shift 2 ;;
    --credentials-secret)  SMTP_SECRET="$2"; shift 2 ;;
    --region)              SES_REGION="$2"; shift 2 ;;
    -h|--help)             sed -n '2,42p' "$0"; exit 0 ;;
    *) die "Unknown argument: $1 (see --help)" ;;
  esac
done
[ "$STEP" = internal-api ] && STEP=internal_api

require_env
command -v yq >/dev/null 2>&1 || die "yq not found — install: sudo wget -qO /usr/local/bin/yq https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64 && sudo chmod +x /usr/local/bin/yq"
command -v helm >/dev/null 2>&1 || die "helm not found"

ROOT="$(cd "$LIB_DIR/../../.." && pwd)"
CHART="$ROOT/deployment/charts/llm-gateway"
V="${VALUES:-$CHART/values-eks-fargate-$DEPLOY_ENV.yaml}"
[ -f "$V" ] || die "values file not found: $V"
V=$(readlink -f "$V")
EMAIL='.notificationWorker.email'

if [ "$STEP" = status ]; then
  hdr "notificationWorker.email — $(basename "$V")"
  yq "$EMAIL" "$V"
  echo
  # live pod env is evidence — the file can be edited but not rolled out
  live=$(kubectl get deploy "${HELM_RELEASE}-notification-worker" -n "$NS" \
    -o jsonpath='{.spec.template.spec.containers[?(@.name=="notification-worker")].env[?(@.name=="EMAIL_SENDER_TYPE")].value}' 2>/dev/null || true)
  file_provider=$(yq -r "$EMAIL.provider // \"-\"" "$V")
  if [ -n "$live" ]; then
    [ "$live" = "$file_provider" ] \
      && ok "live pod EMAIL_SENDER_TYPE=$live (values와 일치)" \
      || warn "live pod EMAIL_SENDER_TYPE=$live ≠ values provider=$file_provider — install-eks.sh $DEPLOY_ENV 미반영"
  else
    note "live pod env 를 읽지 못했습니다 (notification-worker 미배포?)"
  fi
  exit 0
fi

# ── validate required args per provider ─────────────────────────────────────
case "$STEP" in
  internal_api) [ -n "$URL" ]       || die "internal-api requires --url" ;;
  smtp)         [ -n "$SMTP_HOST" ] || die "smtp requires --host"
                [ -n "$FROM_ADDR" ] || die "smtp requires --from" ;;
esac

# provider 별 yq 편집 — 한 파일(임시 또는 실제)에 적용한다.
# strenv 로 값을 넘겨 셸 따옴표 문제를 피한다.
apply_provider() {
  local f="$1"
  PROVIDER="$STEP" yq -i "$EMAIL.provider = strenv(PROVIDER)" "$f"
  case "$STEP" in
    internal_api)
      URL="$URL" yq -i "$EMAIL.internalApi.url = strenv(URL)" "$f"
      [ -n "$FROM_ADDR" ] && FROM="$FROM_ADDR" yq -i "$EMAIL.internalApi.fromAddress = strenv(FROM)" "$f"
      [ -n "$FROM_NAME" ] && FROMN="$FROM_NAME" yq -i "$EMAIL.internalApi.fromName = strenv(FROMN)" "$f" ;;
    smtp)
      HOST="$SMTP_HOST" yq -i "$EMAIL.smtp.host = strenv(HOST)" "$f"
      PORT="$SMTP_PORT" yq -i "$EMAIL.smtp.port = env(PORT) | tonumber" "$f" 2>/dev/null \
        || yq -i "$EMAIL.smtp.port = $SMTP_PORT" "$f"
      [ "$SMTP_STARTTLS" = true ] || [ "$SMTP_STARTTLS" = false ] \
        || die "--starttls must be true or false"
      yq -i "$EMAIL.smtp.startTls = $SMTP_STARTTLS" "$f"
      FROM="$FROM_ADDR" yq -i "$EMAIL.smtp.fromAddress = strenv(FROM)" "$f"
      # 차트가 읽는 경로는 email.smtp.credentialsSecretName — email 아래가 아니다.
      if [ -n "$SMTP_SECRET" ]; then
        SECRET="$SMTP_SECRET" yq -i "$EMAIL.smtp.credentialsSecretName = strenv(SECRET)" "$f"
      else
        yq -i "del($EMAIL.smtp.credentialsSecretName)" "$f"
        warn "credentials secret 미지정 — SMTP 인증 없이 발송을 시도합니다"
      fi ;;
    ses)
      [ -n "$SES_REGION" ] && REGION="$SES_REGION" yq -i "$EMAIL.ses.region = strenv(REGION)" "$f"
      [ -n "$FROM_ADDR" ]  && FROM="$FROM_ADDR"   yq -i "$EMAIL.ses.fromAddress = strenv(FROM)" "$f"
      [ -n "$FROM_NAME" ]  && FROMN="$FROM_NAME"  yq -i "$EMAIL.ses.fromName = strenv(FROMN)" "$f"
      warn "SES 는 IRSA role 이 필요합니다 — 22-setup-notification-ses-irsa.sh --apply 를 먼저 실행했는지 확인하세요" ;;
    mock) ;;
  esac
}

hdr "notification-worker email provider → $STEP"

if [ "$APPLY" = 0 ]; then
  # dry-run: 원본을 건드리지 않고 임시 복사본에서 렌더 결과만 보여준다
  tmp="$(mktemp)"; cp "$V" "$tmp"
  apply_provider "$tmp"
  yq "$EMAIL" "$tmp"; rm -f "$tmp"
  printf '\n  Nothing written yet. Apply:  bash %s %s --apply ...\n\n' "$(basename "$0")" "$STEP"
  exit 0
fi

confirm "Writing provider=$STEP to $V (backup kept in $SNAP_DIR)."
bak="$SNAP_DIR/${TS}-values-$DEPLOY_ENV-21-provider.bak"
cp "$V" "$bak" || die "backup failed"
apply_provider "$V"
yq "$EMAIL" "$V"

# helm 렌더 검증 — env 가 실제 매니페스트에 타는지, smtp 면 credentials 경로까지 확인
rendered=$(helm template "$HELM_RELEASE" "$CHART" --values "$V" 2>/dev/null)
printf '%s' "$rendered" | grep -q "EMAIL_SENDER_TYPE" \
  || { cp "$bak" "$V"; die "helm render lacks EMAIL_SENDER_TYPE — values restored from $bak"; }
printf '%s' "$rendered" | grep -A1 "name: EMAIL_SENDER_TYPE" | grep -q "value: \"$STEP\"" \
  || { cp "$bak" "$V"; die "helm render EMAIL_SENDER_TYPE != $STEP — values restored from $bak"; }
if [ "$STEP" = smtp ] && [ -n "$SMTP_SECRET" ]; then
  printf '%s' "$rendered" | grep -q "SMTP_USERNAME" \
    || { cp "$bak" "$V"; die "helm render lacks SMTP_USERNAME — credentialsSecretName 경로 확인 필요; values restored from $bak"; }
fi
ok "values updated; helm render carries provider=$STEP"; note "backup: $bak"

hdr "Next — deploy (you run it)"
cat <<EOT
  ./deployment/scripts/install-eks.sh $DEPLOY_ENV
    (never a bare helm upgrade — install-eks.sh re-injects the terraform-output
     --set values; a plain -f upgrade would roll them back to placeholders)
  then: bash $(basename "$0") status  →  live pod EMAIL_SENDER_TYPE 확인
EOT
