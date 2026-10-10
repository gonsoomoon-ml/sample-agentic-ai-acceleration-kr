#!/bin/bash
# ---------------------------------------------------------------------------
# 21-set-notification-provider.sh
#
# WHAT: switch the notification-worker's email provider — edits
#       notificationWorker.email.* in values-eks-fargate-<env>.yaml.
#         (no arg)   status: rendered values + live pod EMAIL_SENDER_TYPE
#         mock       provider = mock (no real sending; default for dev/test)
#         internal-api   provider = internal_api — needs --url
#         smtp       provider = smtp — needs --host and --from
#         ses        provider = ses — needs 22-setup-notification-ses-irsa.sh
#                    (IRSA role) applied first
# WHY:  the values file is the source of truth because deploys go through
#       install-eks.sh, which re-injects terraform outputs via --set on every
#       run — a bare helm upgrade -f would roll those values back to
#       placeholders. Reads come from the chart RENDER (helm template), so
#       values inherited from the base values.yaml are visible; writes are
#       scoped line edits — never a YAML round-trip, which would drop the
#       hand-maintained comments and blank lines of the deploy-EC2 originals
#       (same rule as 15·17·19·20).
# NOTE: the chart reads SMTP credentials ONLY from
#       email.smtp.credentialsSecretName — writing the key anywhere else
#       silently drops SMTP auth. Re-running without --credentials-secret
#       leaves an existing secret untouched.
# UNDO: restore the backup --apply prints (snapshots/), or re-run with the
#       previous provider, then install-eks.sh <env>.
#
# Usage:
#   bash 21-set-notification-provider.sh                 # status
#   bash 21-set-notification-provider.sh mock --apply
#   bash 21-set-notification-provider.sh internal-api --apply \
#       --url http://mail-api.internal/send [--from ADDR] [--from-name NAME]
#   bash 21-set-notification-provider.sh smtp --apply \
#       --host smtp.example.com [--port 587] [--usetls true|false|auto] \
#       [--starttls true|false|auto] --from ADDR [--credentials-secret SECRET]
#       (auto = 키 삭제 → worker 의 포트 기반 자동 판정으로 되돌림)
#   bash 21-set-notification-provider.sh ses --apply \
#       [--region REGION] [--from ADDR] [--from-name NAME]
#   --values <path>  edit/render another values file (test)
#
# Order: (ses only) 22-setup-notification-ses-irsa.sh --apply
#        → 21-set-notification-provider.sh <provider> --apply
#        → install-eks.sh <env> → status.sh (SES probe row)
# ---------------------------------------------------------------------------
set -euo pipefail
source "$(dirname "$(readlink -f "$0")")/_lib.sh"

STEP=status; APPLY=0; VALUES=""
URL=""; FROM_ADDR=""; FROM_NAME=""
SMTP_HOST=""; SMTP_PORT=""; SMTP_STARTTLS=""; SMTP_USETLS=""
SMTP_SECRET=""; SMTP_SECRET_SET=0
SES_REGION=""
while [ $# -gt 0 ]; do
  case "$1" in
    status|mock|internal-api|internal_api|smtp|ses) STEP="$1"; shift ;;
    --apply)               APPLY=1; shift ;;
    --values)              VALUES="${2:?--values requires a value}"; shift 2 ;;
    --url)                 URL="${2:?--url requires a value}"; shift 2 ;;
    --from)                FROM_ADDR="${2:?--from requires a value}"; shift 2 ;;
    --from-name)           FROM_NAME="${2:?--from-name requires a value}"; shift 2 ;;
    --host)                SMTP_HOST="${2:?--host requires a value}"; shift 2 ;;
    --port)                SMTP_PORT="${2:?--port requires a value}"; shift 2 ;;
    --usetls)              SMTP_USETLS="${2:?--usetls requires a value}"; shift 2 ;;
    --starttls)            SMTP_STARTTLS="${2:?--starttls requires a value}"; shift 2 ;;
    --credentials-secret)  SMTP_SECRET="${2:?--credentials-secret requires a value}"; SMTP_SECRET_SET=1; shift 2 ;;
    --region)              SES_REGION="${2:?--region requires a value}"; shift 2 ;;
    -h|--help)             sed -n '2,46p' "$0"; exit 0 ;;
    *) die "Unknown argument: $1 (see --help)" ;;
  esac
done
if [ "$STEP" = internal-api ]; then STEP=internal_api; fi
case "$STEP" in status|verify) [ "$APPLY" = 0 ] || die "--apply goes with a provider step" ;; esac
for a in "$SMTP_USETLS" "$SMTP_STARTTLS"; do
  [ -z "$a" ] || [ "$a" = true ] || [ "$a" = false ] || [ "$a" = auto ] || die "--usetls/--starttls must be true, false or auto (got '$a')"
done

require_env
command -v helm >/dev/null 2>&1 || die "helm not found. Run this on the deployer EC2."
command -v python3 >/dev/null 2>&1 || die "python3 not found — values 편집기와 렌더 검증에 필요하다"
python3 -c 'import yaml' 2>/dev/null || die "python3-yaml(PyYAML) not found — values 편집기와 렌더 검증에 필요하다"

ROOT="$(cd "$LIB_DIR/../../.." && pwd)"
CHART="$ROOT/deployment/charts/llm-gateway"
V="${VALUES:-${HELM_VALUES_FILE:-$CHART/values-eks-fargate-$DEPLOY_ENV.yaml}}"
[ -f "$V" ] || die "values file not found: $V"
V=$(readlink -f "$V")
DEP_WORKER="${HELM_RELEASE}-notification-worker"

# ── Reads (chart render — base values 상속분까지 보인다) ─────────────────────
render_chart() {
  local out
  out=$(helm template "$HELM_RELEASE" "$CHART" -f "$1" 2>&1) \
    || { printf 'helm template failed for %s:\n%s\n' "$1" "$(tail -5 <<<"$out")" >&2; return 1; }
  printf '%s' "$out"
}

# "KEY<TAB>value" lines of the notification-worker container env, from the
# chart rendered with <values-file>. secretKeyRef entries print as <secretRef>.
worker_env() {
  local rendered
  rendered=$(render_chart "$1") || return 1
  python3 -c '
import sys, yaml
for d in yaml.safe_load_all(sys.stdin):
    if d and d.get("kind") == "Deployment" and d["metadata"]["name"].endswith("-notification-worker"):
        # containers[0] 고정 인덱스는 사이드카가 붙는 순간 조용히 다른 컨테이너를
        # 읽는다 — 이름으로 고른다(status.sh 의 kubectl jsonpath 와 같은 규칙).
        for c in d["spec"]["template"]["spec"]["containers"]:
            if c.get("name") == "notification-worker":
                for e in c.get("env") or []:
                    print(e["name"] + "\t" + str(e.get("value", "<secretRef>")))
        break
' <<<"$rendered"
}

declare -A RV   # rendered: RV[EMAIL_SENDER_TYPE]=...
load_rendered() {
  local out k v
  out=$(worker_env "$1") || die "could not render env — see the message above"
  while IFS=$'\t' read -r k v; do [ -n "$k" ] && RV["$k"]="$v"; done <<<"$out"
}

# ── Values edit (scoped line edit — never a YAML round-trip) ────────────────
# <src> <dst> then op args separated by US(\x1f):
#   set \x1f seg1 \x1f ... \x1f segN \x1f <str|int|bool|raw> \x1f value
#   del \x1f seg1 \x1f ... \x1f segN
# Segments walk nested maps by indentation; a missing leaf is inserted at the
# end of its parent block, an inline "key: {}" is opened. Trailing comments on
# a replaced leaf survive.
edit_values() {
  python3 - "$@" <<'PY'
import json, re, sys
src, dst, *ops_args = sys.argv[1:]
L = open(src, encoding="utf-8").read().split("\n")

def ind(l): return len(l) - len(l.lstrip(" "))
def code(l): return l.strip() != "" and not l.lstrip().startswith("#")

def split_comment(s):
    # 인라인 주석 위치 — 따옴표 안의 '#' 는 주석이 아니다 (예: key: "a # b").
    # YAML 주석은 공백 뒤 '#' 라서 앞 문자가 공백/탭일 때만 인정한다.
    # 따옴표 스칼라는 값의 첫 비공백 문자가 따옴표일 때만 성립 — 평문 중간의
    # 아포스트로피(Gateway's)는 리터럴이라 "따옴표가 열렸다"로 오인하면 안 된다.
    q = None
    seen = False  # 값 부분에서 비공백 문자를 봤는가
    i = 0
    while i < len(s):
        c = s[i]
        if q:
            if q == '"' and c == "\\":
                i += 2  # \" 는 닫는 따옴표가 아니다(단, '' 안의 \ 는 리터럴)
                continue
            if c == q:
                if q == "'" and i + 1 < len(s) and s[i + 1] == "'":
                    i += 2  # '' 는 단일 따옴표 안의 이스케이프
                    continue
                q = None
        elif c in " \t":
            pass
        elif not seen and c in "\"'":
            q = c
        elif c == "#" and (i == 0 or s[i - 1] in " \t"):
            return s[:i], s[i:]
        if c not in " \t":
            seen = True
        i += 1
    return s, ""

def child_range(lo, parent_ind):
    # (child_indent, insert_pos, end) — insert_pos 는 마지막 자식 코드 라인
    # 직후라, 다음 섹션을 앞두고 쌓인 주석·빈 줄 사이로 새 키가 새지 않는다.
    child = None; last = lo; i = lo
    while i < len(L):
        if code(L[i]):
            d = ind(L[i])
            if d <= parent_ind:
                break
            if child is None:
                child = d
            last = i + 1
        i += 1
    return child, last, i

def find_leaf(lo, hi, ind_lvl, key):
    hits = [i for i in range(lo, hi)
            if code(L[i]) and ind(L[i]) == ind_lvl and re.match(rf"^\s*{re.escape(key)}\s*:", L[i])]
    if len(hits) > 1:
        sys.exit(f"values: '{key}' appears {len(hits)} times in the same scope — fix the file first")
    return hits[0] if hits else None

def ensure_parent(path, create):
    # returns (lo, ins, hi, child_indent) of the block the last segment lives in.
    # create=True inserts a missing intermediate key as an empty map at the end
    # of its parent block (deploy-EC2 files may lack e.g. notificationWorker.env).
    lo, parent_ind = 0, -1
    for depth, seg in enumerate(path[:-1]):
        ci, ins, hi = child_range(lo, parent_ind)
        i = find_leaf(lo, hi, ci if ci is not None else parent_ind + 2, seg)
        if i is None:
            if not create:
                sys.exit(f"values: '{seg}' not found ({'.'.join(path[:depth + 1])}) — is this a {path[0]} values file?")
            new_ind = ci if ci is not None else parent_ind + 2
            L[ins:ins] = [" " * new_ind + f"{seg}:"]
            parent_ind, lo = new_ind, ins + 1
            continue
        m = re.match(rf"^(\s*){re.escape(seg)}\s*:\s*(.*)$", L[i])
        rest = split_comment(m.group(2) or "")[0].strip()
        if rest not in ("{}", ""):
            sys.exit(f"values: '{seg}' is a scalar ({rest!r}) — cannot nest under it")
        if rest == "{}":
            L[i] = f"{m.group(1)}{seg}:"   # open an inline empty map
        parent_ind, lo = ind(L[i]), i + 1
    ci, ins, hi = child_range(lo, parent_ind)
    return lo, ins, hi, ci if ci is not None else parent_ind + 2

def render_value(typ, raw):
    if typ == "str":  return json.dumps(raw)
    if typ == "int":
        try: return str(int(raw))
        except ValueError: sys.exit(f"edit_values: int expected, got '{raw}'")
    if typ == "bool":
        if raw in ("true", "false"): return raw
        sys.exit(f"edit_values: bool expected, got '{raw}'")
    if typ == "raw":  return raw
    sys.exit(f"edit_values: unknown type '{typ}'")

def leaf_is_multiline(i):
    # 다음 코드 라인이 이 키보다 깊게 들어가 있으면 블록 스칼라(|, >)나
    # 다중행 값 — 첫 줄만 고치면 나머지가 고아가 되므로 수동 편집을 요구한다.
    j = i + 1
    while j < len(L) and not code(L[j]):
        j += 1
    return j < len(L) and ind(L[j]) > ind(L[i])

for arg in ops_args:
    parts = arg.split("\x1f")
    op = parts[0]
    if op == "del":
        path = parts[1:]
        lo, ins, hi, ci = ensure_parent(path, create=False)
        i = find_leaf(lo, hi, ci, path[-1])
        if i is not None:
            if leaf_is_multiline(i):
                sys.exit(f"values: '{path[-1]}' is a multi-line value — edit {path[0]} by hand")
            del L[i]
        continue
    if op == "set":
        path, typ, val = parts[1:-2], parts[-2], parts[-1]
        if len(path) < 1:
            sys.exit(f"edit_values: malformed op '{arg}'")
        lo, ins, hi, ci = ensure_parent(path, create=True)
        i = find_leaf(lo, hi, ci, path[-1])
        if i is not None and leaf_is_multiline(i):
            sys.exit(f"values: '{path[-1]}' is a multi-line value — edit {path[0]} by hand")
        new = " " * ci + f"{path[-1]}: {render_value(typ, val)}"
        if i is not None:
            # split_comment 는 값 부분만 본다 — 키 문자 자체가 'seen' 을 세우면
            # 따옴표 값을 인식 못해 quoted '#' 를 주석으로 오인한다.
            colon = L[i].find(":")
            body, comment = split_comment(L[i][colon + 1:] if colon >= 0 else L[i])
            gap = body[len(body.rstrip()):]
            L[i] = new + (gap + comment if comment else "")
        else:
            L[ins:ins] = [new]
        continue
    sys.exit(f"edit_values: unknown op '{op}'")

open(dst, "w", encoding="utf-8").write("\n".join(L))
PY
}

US=$'\x1f'
# Edit → render → compare with wanted env → (apply). $1 = label, $2.. = "KEY=value"
# checks against the rendered notification-worker env, then op args for
# edit_values after a literal "--".
apply_values_edit() {
  local label="$1"; shift
  local -a checks=() ops=()
  while [ $# -gt 0 ]; do
    case "$1" in --) shift; break ;; *) checks+=("$1"); shift ;; esac
  done
  ops=("$@")
  local tmp; tmp=$(mktemp)
  edit_values "$V" "$tmp" "${ops[@]}" || { rm -f "$tmp"; die "values edit failed — $V untouched"; }
  if cmp -s "$V" "$tmp"; then rm -f "$tmp"; ok "values already carries these — nothing to write"; return 1; fi

  declare -A NV=()
  local out k v
  out=$(worker_env "$tmp") || { rm -f "$tmp"; die "edited values fail helm render — $V untouched"; }
  while IFS=$'\t' read -r k v; do [ -n "$k" ] && NV["$k"]="$v"; done <<<"$out"

  local n=0 ck key want
  for ck in "${checks[@]}"; do
    key="${ck%%=*}"; want="${ck#*=}"
    if [ "${NV[$key]:-}" != "$want" ]; then
      bad "$key renders '${NV[$key]:-(unset)}', expected '$want'"; n=$((n + 1))
    fi
  done
  [ "$n" -eq 0 ] || { rm -f "$tmp"; die "edited values do not render the target — $V untouched"; }

  hdr "Planned change — $(basename "$V") (helm renders it)"
  diff -u "$V" "$tmp" | sed -n '3,$p' | sed 's/^/  /'
  if [ "$APPLY" = 0 ]; then
    rm -f "$tmp"
    printf '\n  Nothing written yet. Apply:  bash %s %s --apply ...\n\n' "$(basename "$0")" "$STEP"
    exit 0
  fi
  confirm "$label"
  local bak="$SNAP_DIR/${TS}-values-$DEPLOY_ENV-21-$STEP.bak"
  cp "$V" "$bak" || { rm -f "$tmp"; die "backup failed"; }
  cp "$tmp" "$V" || { rm -f "$tmp"; die "could not write $V (backup: $bak)"; }
  rm -f "$tmp"
  ok "values updated: $V"
  note "backup: $bak   (undo: cp $bak $V, then install-eks.sh $DEPLOY_ENV)"
  return 0
}

# ── status (read-only) ──────────────────────────────────────────────────────
if [ "$STEP" = status ]; then
  hdr "notification-worker email — env $DEPLOY_ENV"
  load_rendered "$V"
  provider="${RV[EMAIL_SENDER_TYPE]:-unset}"
  printf '  provider (rendered): %s\n' "$provider"
  for k in AWS_SES_REGION EMAIL_SENDER_ADDRESS EMAIL_SENDER_NAME SMTP_HOST SMTP_PORT SMTP_USE_TLS SMTP_STARTTLS; do
    if [ -n "${RV[$k]:-}" ]; then printf '    %-22s %s\n' "$k" "${RV[$k]}"; fi
  done
  if [ -n "${RV[SMTP_USERNAME]:-}" ]; then printf '    %-22s %s\n' "SMTP_USERNAME" "<secretRef>"; fi
  live=$(kubectl get deploy "$DEP_WORKER" -n "$NS" \
    -o jsonpath='{.spec.template.spec.containers[?(@.name=="notification-worker")].env[?(@.name=="EMAIL_SENDER_TYPE")].value}' 2>/dev/null || true)
  if [ -n "$live" ]; then
    [ "$live" = "$provider" ] \
      && ok "live pod EMAIL_SENDER_TYPE=$live (values와 일치)" \
      || warn "live pod EMAIL_SENDER_TYPE=$live ≠ values provider=$provider — install-eks.sh $DEPLOY_ENV 미반영"
  else
    note "live pod env 를 읽지 못했습니다 (notification-worker 미배포?)"
  fi
  if [ "$provider" = mock ]; then
    note "provider=mock — 실제 메일은 나가지 않습니다 (선택 기능)"
  fi
  exit 0
fi

# ── validate required args per provider ─────────────────────────────────────
case "$STEP" in
  internal_api) [ -n "$URL" ]       || die "internal-api requires --url" ;;
  smtp)         [ -n "$SMTP_HOST" ] || die "smtp requires --host"
                [ -n "$FROM_ADDR" ] || die "smtp requires --from" ;;
esac

# ── build the ops + rendered-env checks for the chosen provider ─────────────
declare -a CHECKS=("EMAIL_SENDER_TYPE=$STEP") OPS=(
  "set${US}notificationWorker${US}email${US}provider${US}str${US}$STEP"
)
case "$STEP" in
  internal_api)
    OPS+=("set${US}notificationWorker${US}email${US}internalApi${US}url${US}str${US}$URL")
    if [ -n "$FROM_ADDR" ]; then OPS+=("set${US}notificationWorker${US}email${US}internalApi${US}fromAddress${US}str${US}$FROM_ADDR"); fi
    if [ -n "$FROM_NAME" ]; then OPS+=("set${US}notificationWorker${US}email${US}internalApi${US}fromName${US}str${US}$FROM_NAME"); fi
    ;;
  smtp)
    OPS+=("set${US}notificationWorker${US}email${US}smtp${US}host${US}str${US}$SMTP_HOST"
          "set${US}notificationWorker${US}email${US}smtp${US}fromAddress${US}str${US}$FROM_ADDR")
    if [ -n "$SMTP_PORT" ];     then OPS+=("set${US}notificationWorker${US}email${US}smtp${US}port${US}int${US}$SMTP_PORT"); fi
    # true/false = 명시 값, auto = 키를 지워 차트가 env 를 내리지 않게(worker 의
    # 포트 기반 자동 판정으로 되돌림). 한번 설정한 뒤 해제할 수 있어야 한다.
    if [ "$SMTP_USETLS" = auto ];   then OPS+=("del${US}notificationWorker${US}email${US}smtp${US}useTls");
    elif [ -n "$SMTP_USETLS" ];     then OPS+=("set${US}notificationWorker${US}email${US}smtp${US}useTls${US}bool${US}$SMTP_USETLS"); fi
    if [ "$SMTP_STARTTLS" = auto ]; then OPS+=("del${US}notificationWorker${US}email${US}smtp${US}startTls");
    elif [ -n "$SMTP_STARTTLS" ];   then OPS+=("set${US}notificationWorker${US}email${US}smtp${US}startTls${US}bool${US}$SMTP_STARTTLS"); fi
    if [ -n "$FROM_NAME" ]; then warn "--from-name 은 smtp provider 에서 지원되지 않습니다(internal_api·ses 만) — 무시합니다"; fi
    # 465 는 implicit TLS 포트 — useTls:false + STARTTLS on 465 는 대부분 서버가
    # 거절하는 조합이라 명시했을 때 경고한다(useTls 미설정이면 worker 가 포트로 자동).
    if [ "${SMTP_PORT:-587}" = "465" ] && [ "$SMTP_USETLS" = "false" ]; then
      warn "--port 465 는 implicit TLS 포트입니다 — --usetls false(STARTTLS on 465)는 대부분 거절됩니다. --usetls true 를 쓰거나 --usetls 를 빼서 자동 판정에 맡기세요"
    fi
    CHECKS+=(SMTP_HOST="$SMTP_HOST")
    if [ "$SMTP_SECRET_SET" = 1 ]; then
      if [ -n "$SMTP_SECRET" ]; then
        OPS+=("set${US}notificationWorker${US}email${US}smtp${US}credentialsSecretName${US}str${US}$SMTP_SECRET")
        CHECKS+=("SMTP_USERNAME=<secretRef>")
      else
        OPS+=("del${US}notificationWorker${US}email${US}smtp${US}credentialsSecretName")
        warn "--credentials-secret '' — 기존 SMTP 인증 설정을 지웁니다"
      fi
    else
      note "--credentials-secret 미지정 — 기존 SMTP 인증 설정을 그대로 둡니다"
    fi
    ;;
  ses)
    if [ -n "$SES_REGION" ]; then OPS+=("set${US}notificationWorker${US}email${US}ses${US}region${US}str${US}$SES_REGION"); fi
    if [ -n "$FROM_ADDR" ];  then OPS+=("set${US}notificationWorker${US}email${US}ses${US}fromAddress${US}str${US}$FROM_ADDR"); fi
    if [ -n "$FROM_NAME" ];  then OPS+=("set${US}notificationWorker${US}email${US}ses${US}fromName${US}str${US}$FROM_NAME"); fi
    warn "SES 는 IRSA role 이 필요합니다 — 22-setup-notification-ses-irsa.sh --apply 를 먼저 실행했는지 확인하세요"
    # --region 이 22 가 만든 IAM policy 리전과 어긋나면 pod region ≠ policy
    # resource region 이라 AccessDenied 로 발송이 실패한다 — 어긋나면 거절.
    _role="llm-gateway-${DEPLOY_ENV}-notification-worker-ses"
    _pol_res=$(aws iam get-role-policy --role-name "$_role" \
        --policy-name SESSendEmail --query 'PolicyDocument.Statement[0].Resource' \
        --output text 2>/dev/null || true)
    _pol_region=$(printf '%s' "$_pol_res" | grep -oE 'arn:[a-z0-9-]+:ses:[a-z0-9-]+' | cut -d: -f4 | head -1 || true)
    if [ -n "$_pol_region" ] && [ -n "$SES_REGION" ] && [ "$_pol_region" != "$SES_REGION" ]; then
      die "--region $SES_REGION ≠ IAM policy region $_pol_region (role $_role)
     — 한쪽을 맞추세요: 22-setup-notification-ses-irsa.sh 재실행(IAM) 또는 --region 수정"
    elif [ -z "$_pol_region" ]; then
      note "SES role $_role 의 inline policy 를 읽지 못했습니다 — 22 가 아직 안 돌았거나 권한이 없습니다"
    fi
    note "provider=ses 는 ses extra 가 들어간 worker 이미지 필요(uv sync --extra ses)
    — notificationWorker.image.tag 가 구 빌드(phase 태그)면 배포 전에 13-bump-image-tags.sh 로 올리세요"
    ;;
  mock) ;;
esac

hdr "notification-worker email provider → $STEP"
apply_values_edit "Writing provider=$STEP to $V (backup kept in $SNAP_DIR)." \
  "${CHECKS[@]}" -- "${OPS[@]}" || { echo; exit 0; }

hdr "Next — deploy (you run it)"
cat <<EOT
  ./deployment/scripts/install-eks.sh $DEPLOY_ENV
    (never a bare helm upgrade — install-eks.sh re-injects the terraform-output
     --set values; a plain -f upgrade would roll them back to placeholders)
  then: bash $(basename "$0") status  →  live pod EMAIL_SENDER_TYPE 확인
EOT
