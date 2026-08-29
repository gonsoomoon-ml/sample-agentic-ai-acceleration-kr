#!/bin/bash
# ---------------------------------------------------------------------------
# status.sh — which updates this gateway has applied
#
# WHAT: probe the live system and report US-02 … US-07 as
#       applied, partially applied, or not applied. Prints the next command
#       for each.
# WHY:  what an update produces lives OUTSIDE git — a routing_profiles row, a
#       CloudFront distribution, VPC endpoints. Pulling the latest code does
#       not apply them, so "not applied" is a normal state for a checkout that
#       is fully up to date. Commit hashes cannot answer this either: this
#       branch is rebased onto upstream, so the hashes change (which is why
#       update-scripts/README.md tells you to `git reset --hard`, not `git pull`).
# UNDO: changes nothing. Note it is not strictly read-only, though: reading the
#       database means starting a throwaway psql pod in the cluster and
#       deleting it (_lib.sh run_sql). Fargate scheduling dominates: measured
#       1m20s-1m30s on the us-west-2 deployment.
#
# The name carries no number on purpose. 00-09 are the execution ORDER inside
# one batch; US-NN are update GENERATIONS. Two different axes — numbering this
# script would conflate them.
#
# 업데이트 목록: docs/us-llm-gateway/README.md  ("최신 소식")
#         Spelled from the repo root, not relative: this file is mirrored into
#         another repository where a relative path would point elsewhere.
# ---------------------------------------------------------------------------
set -uo pipefail
source "$(dirname "$(readlink -f "$0")")/_lib.sh"

VERBOSE=0
while [ $# -gt 0 ]; do
  case "$1" in
    -v|--verbose) VERBOSE=1 ;;
    -h|--help)    sed -n '2,24p' "$0"; exit 0 ;;
    *) die "unknown argument: $1
     usage: $(basename "$0") [--verbose]" ;;
  esac
  shift
done

require_env

# When i18n became present ON THIS BRANCH — merge 152ce95, not the authored
# commit fe10cab: what matters is when a checkout of us/deploy-fixes started
# carrying it. An admin-ui image pushed before this cannot contain i18n whatever
# its tag says, because a built image cannot be introspected from outside.
# Compared at full timestamp, not by day: the observed deployment built its
# image under two hours after the merge, which day granularity cannot separate.
US03_MERGED_AT="2026-08-07T06:23:50+00:00"

# ── Row printing ────────────────────────────────────────────────────────────
# Titles are Korean, and Korean glyphs occupy two terminal columns while printf
# counts characters. Padding a column that holds them therefore misaligns, so
# only the ASCII part (state + ID) is padded and the title runs free.
row() {
  local state="$1" id="$2" title="$3"
  case "$state" in
    ok)   printf '  %s OK %s  %-6s %s\n' "$c_green"  "$c_reset" "$id" "$title" ;;
    warn) printf '  %s !! %s  %-6s %s\n' "$c_yellow" "$c_reset" "$id" "$title" ;;
    bad)  printf '  %s XX %s  %-6s %s\n' "$c_red"    "$c_reset" "$id" "$title" ;;
    *)    printf '  %s -- %s  %-6s %s\n' "$c_dim"    "$c_reset" "$id" "$title" ;;
  esac
}
detail() { printf '            %s\n' "$1"; }
raw()    { [ "$VERBOSE" = "1" ] && { printf '%s' "$c_dim"; sed 's/^/            | /' <<<"$1"; printf '%s' "$c_reset"; }; return 0; }

TODO=()

# ── US-02 — Cowork routing + Opus 5 + CloudFront ────────────────────────────
# Same three checks 00-preflight-check.sh makes, reduced to a verdict. Reported
# as three sub-items rather than one boolean: CloudFront only matters to a
# deployment that actually serves Cowork.
probe_us02() {
  local out routing alias_n cf n_bad=0

  # Markers rather than column parsing: the pod log carries other lines, and
  # unaligned output with an explicit separator survives any psql formatting.
  out=$(run_sql "\\pset tuples_only on
\\pset format unaligned
SELECT 'ROUTING=' || backend FROM model.routing_profiles WHERE client='$COWORK_CLIENT';
SELECT 'ALIAS='   || count(*) FROM model.model_aliases
 WHERE alias='$MODEL_ALIAS' AND status='ACTIVE';" 2>&1)

  routing=$(grep -o 'ROUTING=[a-z]*' <<<"$out" | head -1 | cut -d= -f2)
  alias_n=$(grep -o 'ALIAS=[0-9]*'   <<<"$out" | head -1 | cut -d= -f2)

  # Neither marker means the query never ran (pod scheduling, credentials,
  # network). Reporting that as "three things missing" would send the operator
  # off to repair rows that are probably fine, so say what actually happened.
  if [ -z "$routing" ] && [ -z "$alias_n" ]; then
    row warn "US-02" "Cowork 연결 + Opus 5 등록 — 판정 불가"
    detail "DB 조회 실패 — 원인은 00-preflight-check.sh 가 자세히 보여줍니다"
    raw "$out"
    return
  fi

  # Filter by origin: an account may hold unrelated distributions, so "any
  # CloudFront?" would misjudge.
  cf=$(aws cloudfront list-distributions \
        --query "DistributionList.Items[?Origins.Items[0].DomainName=='$GW_ALB_DNS'].Id" \
        --output text 2>/dev/null)

  local l_routing l_alias l_cf
  if [ -z "$routing" ]; then
    # The query ran but returned no cowork row — Cowork cannot work either way,
    # and 01 is what creates/corrects it.
    l_routing="routing 행 없음"; n_bad=$((n_bad+1))
    TODO+=("bash 01-fix-cowork-routing.sh --apply")
  elif [ "$routing" = "mantle" ]; then
    l_routing="routing=mantle (미적용)"; n_bad=$((n_bad+1))
    TODO+=("bash 01-fix-cowork-routing.sh --apply")
  else
    l_routing="routing=$routing"
  fi

  if [ "${alias_n:-0}" -ge 1 ]; then
    l_alias="$MODEL_ALIAS ACTIVE"
  else
    l_alias="$MODEL_ALIAS 없음"; n_bad=$((n_bad+1))
    TODO+=("bash 02-add-opus5-model.sh --help   # 단가 인자 확인 후 --apply")
  fi

  if [ "$GW_HTTPS" = 1 ]; then
    # US-06 puts TLS on the ALB itself; CloudFront is then not part of the design.
    l_cf="CloudFront 불필요 (US-06 HTTPS)"
  elif [ -n "$cf" ]; then
    l_cf="CloudFront $cf"
  else
    l_cf="CloudFront 없음"; n_bad=$((n_bad+1))
    TODO+=("bash 03-create-cloudfront.sh        # Cowork 를 사용하는 경우에만 필요")
  fi

  if   [ "$n_bad" -eq 0 ]; then row ok   "US-02" "Cowork 연결 + Opus 5 등록"
  elif [ "$n_bad" -eq 3 ]; then row bad  "US-02" "Cowork 연결 + Opus 5 등록 — 미적용"
  else                          row warn "US-02" "Cowork 연결 + Opus 5 등록 — 일부 적용"
  fi
  detail "$l_routing · $l_alias · $l_cf"
  [ "$n_bad" -gt 0 ] && detail "Cowork 를 안 쓰는 배포라면 건너뛰어도 됩니다"
  raw "$out"
}

# ── US-03 — admin-ui ko/en i18n ─────────────────────────────────────────────
# There is no runtime flag to read, so the deployed image is the only evidence.
# Two signals, strongest first: the tag suffix 09-update-admin-ui.sh writes, and
# failing that, whether the image predates the i18n merge.
probe_us03() {
  local deploy live tag pushed
  deploy="${HELM_RELEASE}-admin-ui"
  live=$(kubectl get deploy "$deploy" -n "$NS" \
         -o jsonpath='{.spec.template.spec.containers[?(@.name=="admin-ui")].image}' 2>/dev/null)

  if [ -z "$live" ]; then
    row bad "US-03" "Admin UI 한·영 토글 — 판정 불가"
    detail "deployment '$deploy' 을 못 찾았습니다 (ns $NS)"
    return
  fi
  tag="${live##*:}"

  if [[ "$tag" == *i18n* ]]; then
    row ok "US-03" "Admin UI 한·영 토글"
    detail "이미지 tag $tag"
    return
  fi

  pushed=$(aws ecr describe-images --repository-name "llm-gateway/admin-ui" \
            --image-ids imageTag="$tag" \
            --query 'imageDetails[0].imagePushedAt' --output text 2>/dev/null)

  local p_epoch="" m_epoch
  [ -n "$pushed" ] && [ "$pushed" != "None" ] && p_epoch=$(date -d "$pushed" +%s 2>/dev/null)
  m_epoch=$(date -d "$US03_MERGED_AT" +%s 2>/dev/null)

  if [ -z "$p_epoch" ]; then
    row warn "US-03" "Admin UI 한·영 토글 — 판정 불가"
    detail "이미지 tag $tag — ECR 에서 푸시 시각을 못 읽었습니다"
    detail "화면 우상단 KO/EN 토글이 실제로 번역되는지 눈으로 확인하십시오"
    return
  fi

  if [ "$p_epoch" -lt "$m_epoch" ]; then
    row bad "US-03" "Admin UI 한·영 토글 — 미적용"
    detail "이미지 tag $tag (푸시 $(date -d "$pushed" +'%F %H:%M %Z'))"
    detail "i18n 이 브랜치에 들어온 $(date -d "$US03_MERGED_AT" +'%F %H:%M %Z') 보다 이전 빌드 → 포함될 수 없음"
    TODO+=("bash 06-persist-annotations.sh                       # 선행 확인 (읽기 전용)")
    TODO+=("cd ~/awsome-ai-gateway && ./deployment/scripts/rebuild-image.sh admin-ui $DEPLOY_ENV")
    TODO+=("cd ~/awsome-ai-gateway && ./deployment/scripts/install-eks.sh $DEPLOY_ENV")
  else
    # Built after the merge, so an up-to-date checkout carried i18n into it.
    # That last assumption is the only gap, hence "추정" and the eyeball check.
    row ok "US-03" "Admin UI 한·영 토글 (추정)"
    detail "이미지 tag $tag (푸시 $(date -d "$pushed" +'%F %H:%M %Z')) — i18n 반입 이후 빌드"
    detail "최신 체크아웃으로 빌드했다는 전제입니다. 확실히 하려면 화면 우상단 KO/EN 토글을 눌러 보십시오"
  fi
}

# ── US-04 — Bedrock/STS over VPC endpoints ──────────────────────────────────
# Deliberately NOT enable-bedrock-vpce.sh: its no-flag mode runs `terraform
# plan`, which needs an initialised state directory and takes tens of seconds.
# Existence is one describe call, and the script stays the place for detail.
probe_us04() {
  local vpc found n
  vpc=$(aws elbv2 describe-load-balancers --names "$GW_ALB_NAME" \
        --query 'LoadBalancers[0].VpcId' --output text 2>/dev/null)
  if [ -z "$vpc" ] || [ "$vpc" = "None" ]; then
    row warn "US-04" "Bedrock VPC Endpoint — 판정 불가"
    detail "gateway ALB 로부터 VPC 를 못 찾았습니다"
    return
  fi

  found=$(aws ec2 describe-vpc-endpoints --filters "Name=vpc-id,Values=$vpc" \
          --query "VpcEndpoints[?ServiceName=='com.amazonaws.$AWS_REGION.bedrock-runtime'
                             || ServiceName=='com.amazonaws.$AWS_REGION.bedrock'
                             || ServiceName=='com.amazonaws.$AWS_REGION.sts'].ServiceName" \
          --output text 2>/dev/null)
  n=$(wc -w <<<"$found")

  if [ "$n" -ge 3 ]; then
    row ok "US-04" "Bedrock·STS VPC Endpoint"
    detail "$vpc 에 엔드포인트 3종 존재"
  elif [ "$n" -gt 0 ]; then
    row warn "US-04" "Bedrock·STS VPC Endpoint — 일부 ($n/3)"
    detail "있는 것: $(tr '\t' ' ' <<<"$found")"
    TODO+=("bash ../../../deployment/scripts/enable-bedrock-vpce.sh $DEPLOY_ENV")
  else
    row bad "US-04" "Bedrock·STS VPC Endpoint — 미적용 (필수)"
    detail "엔드포인트 없음 → Bedrock·STS 호출이 NAT 경유"
    detail "컴플라이언스 요건이므로 적용해야 합니다 — README.md 「최신 업데이트」 US-04"
    TODO+=("bash ../../../deployment/scripts/enable-bedrock-vpce.sh $DEPLOY_ENV")
  fi
  raw "$found"
}

# ── US-05 — EKS 1.34 ────────────────────────────────────────────────────────
# The version comes from the live API server (kubectl version), not from
# terraform files: tfvars can already say 1.34 while the cluster still runs
# 1.31 — only the server's own answer proves the upgrade happened.
probe_us05() {
  local minor oldest
  minor=$(kubectl version -o json 2>/dev/null \
          | sed -n 's/.*"minor": *"\([0-9]\{1,\}\).*/\1/p' | tail -1)
  if [ -z "$minor" ]; then
    row warn "US-05" "EKS 1.34 업그레이드 — 판정 불가"
    detail "kubectl version 으로 API 서버 버전을 읽지 못했습니다"
    return
  fi

  # Data plane: on Fargate a pod IS a node, so a pod not restarted since the
  # upgrade still runs the old kubelet. The oldest node minor tells whether
  # the restart step of ops/8-E-eks-upgrade.md was completed.
  oldest=$(kubectl get nodes -o jsonpath='{range .items[*]}{.status.nodeInfo.kubeletVersion}{"\n"}{end}' 2>/dev/null \
           | sed -n 's/^v1\.\([0-9]\{1,\}\)\..*/\1/p' | sort -n | head -1)

  if [ "$minor" -lt 34 ]; then
    row bad "US-05" "EKS 1.34 업그레이드 — 미적용 (필수)"
    detail "컨트롤 플레인 1.$minor (목표 1.34) — 표준 지원 만료 시 연장 요금이 붙습니다"
    TODO+=("(수동) docs/us-llm-gateway/ops/8-E-eks-upgrade.md — EKS 1.$minor → 1.34 를 1단계씩")
  elif [ -n "$oldest" ] && [ "$oldest" -lt "$minor" ]; then
    row warn "US-05" "EKS 1.34 업그레이드 — 일부 적용"
    detail "컨트롤 플레인 1.$minor 이지만 가장 오래된 노드가 1.$oldest — §8-E 의 파드 재시작 누락"
    TODO+=("kubectl rollout restart deployment -n $NS   # coredns(-n kube-system)도 함께 — §8-E")
  else
    row ok "US-05" "EKS 1.34 업그레이드"
    detail "컨트롤 플레인 1.$minor · 노드 최저 1.${oldest:-$minor}"
  fi
  raw "server minor=$minor / oldest node minor=${oldest:-?}"
}

# ── US-06 — ALB HTTPS on a custom domain (optional) ─────────────────────────
# The evidence is the gateway Ingress itself: a host rule plus a certificate-arn
# annotation is what makes the ALB terminate TLS (values 방식 B). Optional, so
# "not applied" is informational (--), never a failure — a deployment without a
# domain is a valid deployment.
probe_us06() {
  if [ "$GW_HTTPS" = 1 ]; then
    local arn ports
    arn=$(aws elbv2 describe-load-balancers --names "$GW_ALB_NAME" \
          --query 'LoadBalancers[0].LoadBalancerArn' --output text 2>/dev/null)
    ports=$(aws elbv2 describe-listeners --load-balancer-arn "$arn" \
          --query 'Listeners[].Port' --output text 2>/dev/null | tr '\t' ',')
    if [[ ",$ports," == *",443,"* ]]; then
      row ok "US-06" "ALB HTTPS (커스텀 도메인)"
      detail "https://$GW_HOST · 리스너 $ports · cert …${GW_CERT_ARN: -12}"
    else
      row warn "US-06" "ALB HTTPS (커스텀 도메인) — 일부 적용"
      detail "Ingress 엔 host·cert 가 있는데 ALB 리스너가 $ports — helm 반영 지연 또는 SG 규칙 초과 (kubectl get events)"
      TODO+=("kubectl get events -n $NS --sort-by=.lastTimestamp | tail   # ops/8-H-alb-https.md 2단계")
    fi
    raw "host=$GW_HOST cert=$GW_CERT_ARN listeners=$ports"
  else
    row skip "US-06" "ALB HTTPS (커스텀 도메인) — 미적용 (선택 · POC 는 도메인 있을 때)"
    detail "도메인이 있으면 ops/8-H-alb-https.md · 없으면 Cowork https 는 CloudFront(US-02 03)"
  fi
}

# ── US-07 — admin ALBs internal (optional, customer final posture) ──────────
# The evidence is the live ALB behind each admin Ingress, not the values file:
# `scheme: internal` in values (ops/8-I-admin-internal.md) only takes effect
# once the ALB controller has rebuilt the load balancer, so an edited-but-not-
# reconciled annotation would still leave an internet-facing ALB. Optional —
# it needs a VPN path into the VPC — so "not applied" is informational (--),
# never a failure. Production built with US-08 includes it from the start.
probe_us07() {
  local ing dns scheme n_int=0 n_unk=0 ev=""
  for ing in "$ING_ADMIN_UI" "$ING_ADMIN_API"; do
    dns=$(kubectl get ingress "$ing" -n "$NS" \
          -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null)
    scheme=""
    [ -n "$dns" ] && scheme=$(aws elbv2 describe-load-balancers \
          --query "LoadBalancers[?DNSName=='$dns'].Scheme | [0]" --output text 2>/dev/null)
    case "$scheme" in
      internal)        n_int=$((n_int+1)) ;;
      internet-facing) ;;
      *)               n_unk=$((n_unk+1)); scheme="?" ;;
    esac
    ev+="$ing -> ${dns:-<no ALB>} ($scheme)"$'\n'
  done
  if [ "$n_unk" -gt 0 ]; then
    row warn "US-07" "admin ALB internal — 판정 불가"
    detail "admin Ingress 의 ALB 를 읽지 못했습니다 (kubectl get ingress -n $NS · aws elbv2 describe-load-balancers)"
  elif [ "$n_int" -eq 2 ]; then
    row ok "US-07" "admin ALB 2개 internal (고객사 최종형)"
    detail "admin-ui·admin-api ALB scheme=internal — VPN/VPC 안에서만 접근"
  elif [ "$n_int" -eq 1 ]; then
    row warn "US-07" "admin ALB internal — 일부 적용 (1/2)"
    detail "한쪽만 internal — values 의 adminUi·adminApi annotations 를 같이 (ops/8-I-admin-internal.md)"
    TODO+=("(수동) docs/us-llm-gateway/ops/8-I-admin-internal.md — 나머지 admin Ingress 도 internal 로")
  else
    row skip "US-07" "admin ALB internal — 미적용 (선택 · S2S VPN 전제 · 운영(US-08)은 포함)"
    detail "VPN 개통 후 ops/8-I-admin-internal.md · VPN 없이 internal 로 두면 VK 발급이 막힙니다"
  fi
  raw "${ev%$'\n'}"
}

# ── Report ──────────────────────────────────────────────────────────────────
echo
printf '%s AWSome AI Gateway 해외 배포판 — 업데이트 적용 상태%s\n' "$c_bold" "$c_reset"
printf '%s\n' "$(printf '─%.0s' $(seq 1 68))"
printf '  계정 %s / %s · env %s · release %s · ns %s\n\n' \
  "$AWS_ACCOUNT_ID" "$AWS_REGION" "$DEPLOY_ENV" "$HELM_RELEASE" "$NS"

row ok "US-01" "최초 설치 (기준선)"
detail "이 스크립트가 도는 것 자체가 설치가 끝났다는 뜻입니다"
probe_us02
probe_us03
probe_us04
probe_us05
probe_us06
probe_us07

echo
if [ "${#TODO[@]}" -eq 0 ]; then
  ok "모든 업데이트가 적용돼 있습니다"
else
  hdr "다음 작업 (update-scripts 디렉터리에서 실행)"
  for t in "${TODO[@]}"; do printf '  %s\n' "$t"; done
  note "각 업데이트의 등급·대상·롤백은 docs/us-llm-gateway/README.md 「최신 소식」에 있습니다"
fi
echo
