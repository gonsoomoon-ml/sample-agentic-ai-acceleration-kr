#!/usr/bin/env bash
# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

# ==============================================================================
# enable-bedrock-vpce.sh — Bedrock/STS 트래픽을 NAT 대신 VPC Endpoint 로
# ------------------------------------------------------------------------------
# terraform/modules/vpc/main.tf 는 bedrock-runtime · bedrock · sts 인터페이스
# 엔드포인트를 선언한다. 그러나 그 선언이 추가되기 **전에 apply 한 VPC** 에는
# 엔드포인트가 없고, 게이트웨이의 모든 Bedrock 호출이 NAT 를 거쳐 퍼블릭
# 인터넷을 지난다. 코드는 이미 있고 apply 만 안 된 상태이며, 호출은 계속
# 성공하므로 아무도 알려주지 않는다.
#
# 신규 설치에는 필요 없다 — terraform apply 에 이미 포함돼 있다.
# 기존 설치를 뒤늦게 전환할 때만 쓴다.
#
# 사용법:
#   ./enable-bedrock-vpce.sh <env>              # 상태 + plan. 아무것도 안 바꿈
#   ./enable-bedrock-vpce.sh <env> --apply      # 엔드포인트 생성
#   ./enable-bedrock-vpce.sh <env> --verify     # 읽기 전용. 경로가 사설인지 확인
#   ./enable-bedrock-vpce.sh <env> --rollback   # 엔드포인트 삭제 → NAT 로 복귀
#     env: dev | prod
#
# 전제:
#   - terraform/environments/llm-gateway-<env> 가 init 되어 있고 state 접근 가능
#   - aws / kubectl / terraform 설치, install-eks.sh 를 돌리는 그 호스트에서 실행
# ==============================================================================

# -e 는 쓰지 않는다. 이 스크립트는 grep/aws 의 "못 찾음"(exit 1)을 정상 분기로
# 다루므로, -e 가 걸리면 판정 로직이 통째로 중단된다.
set -uo pipefail

# 도움말은 리전·인자 검사보다 앞이다. 뭘 하는 스크립트인지 읽어보려는 사람에게
# "리전이 설정되지 않았습니다" 를 돌려주는 건 답이 아니다.
case "${1:-}" in -h|--help) sed -n '5,26p' "$0"; exit 0 ;; esac

# ---- 리전 확정 (install-eks.sh 와 동일 규약) ----
: "${AWS_REGION:=${AWS_DEFAULT_REGION:-}}"
if [ -z "$AWS_REGION" ]; then
    echo "ERROR: region is not set. export AWS_DEFAULT_REGION=<region> and retry." >&2
    exit 1
fi
export AWS_REGION AWS_DEFAULT_REGION="$AWS_REGION"

# ---- 인자 ----
ENV="${1:-}"
if [ -z "$ENV" ] || { [ "$ENV" != "dev" ] && [ "$ENV" != "prod" ]; }; then
    echo "Usage: $0 <dev|prod> [--apply|--verify|--rollback]"
    exit 1
fi
shift

MODE="plan"
while [ $# -gt 0 ]; do
    case "$1" in
        --apply)    MODE="apply";    shift ;;
        --verify)   MODE="verify";   shift ;;
        --rollback) MODE="rollback"; shift ;;
        --status)   MODE="plan";     shift ;;
        -h|--help)  sed -n '5,26p' "$0"; exit 0 ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

# ---- 경로 ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TF_DIR="$DEPLOY_DIR/terraform/environments/llm-gateway-$ENV"

# 검증 파드용. install-eks.sh 가 네임스페이스를 llm-gateway 로 고정한다.
NS="${K8S_NAMESPACE:-llm-gateway}"
# Docker Hub rate limit 을 피해 ECR Public 사용
PROBE_IMAGE="${PROBE_IMAGE:-public.ecr.aws/docker/library/alpine:3.20}"

# ---- 출력 ----
if [ -t 1 ]; then
    c_bold=$'\033[1m'; c_dim=$'\033[2m'; c_red=$'\033[31m'
    c_green=$'\033[32m'; c_yellow=$'\033[33m'; c_reset=$'\033[0m'
else
    c_bold=""; c_dim=""; c_red=""; c_green=""; c_yellow=""; c_reset=""
fi
hdr()  { printf '\n%s%s%s\n%s\n' "$c_bold" "$1" "$c_reset" "$(printf '─%.0s' $(seq 1 68))"; }
ok()   { printf '%s  OK %s %s\n' "$c_green"  "$c_reset" "$1"; }
warn() { printf '%s  !! %s %s\n' "$c_yellow" "$c_reset" "$1"; }
bad()  { printf '%s  XX %s %s\n' "$c_red"    "$c_reset" "$1"; }
note() { printf '%s     %s%s\n'  "$c_dim" "$1" "$c_reset"; }
die()  { printf '\n%sABORTED: %s%s\n\n' "$c_red" "$1" "$c_reset" >&2; exit 1; }

confirm() {
    printf '\n%s%s%s\n' "$c_yellow" "$1" "$c_reset"
    printf 'Type %syes%s to continue (영문 입력기로): ' "$c_bold" "$c_reset"
    local ans; read -r ans
    [ "$ans" = "yes" ] || die "Cancelled by user."
}

# ---- 전제 확인 ----
for c in aws kubectl terraform; do
    command -v "$c" >/dev/null 2>&1 \
        || die "$c not found. install-eks.sh 를 돌리는 호스트에서 실행하십시오."
done
[ -d "$TF_DIR" ] || die "terraform 디렉터리가 없습니다: $TF_DIR"

ACCT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) \
    || die "사용 가능한 AWS 자격증명이 없습니다."

tf() { terraform -chdir="$TF_DIR" "$@"; }

VPC=$(tf output -raw vpc_id 2>/dev/null)
if [ -z "$VPC" ]; then
    die "terraform output 에서 vpc_id 를 못 읽었습니다:
       $TF_DIR

     에러가 state 없음이 아니라 provider 체크섬 얘기였다면 작업 디렉터리만
     다시 초기화하면 됩니다. 인프라는 멀쩡합니다:
       cd $TF_DIR && terraform init

     ⚠️ 이 메시지를 없애려고 'terraform apply' 를 돌리지 마십시오.
     ⚠️ 'terraform init -upgrade' 도 쓰지 마십시오 — provider 버전이 올라가
        다음 apply 에 무관한 인프라 변경이 딸려옵니다."
fi

VPC_CIDR=$(aws ec2 describe-vpcs --vpc-ids "$VPC" --query 'Vpcs[0].CidrBlock' --output text 2>/dev/null)
VPC_PREFIX=$(cut -d. -f1,2 <<<"$VPC_CIDR")   # "10.30" — 사설 응답인지 판정할 때 씀

# modules/vpc/main.tf 가 선언하는 것들과 그 terraform 주소
SERVICES=("bedrock-runtime" "bedrock" "sts")
TARGETS=(
    -target=module.vpc.aws_security_group.vpce_bedrock
    -target=module.vpc.aws_vpc_endpoint.bedrock_runtime
    -target=module.vpc.aws_vpc_endpoint.bedrock
    -target=module.vpc.aws_vpc_endpoint.sts
)

# ---- 상태 판정 ----
# PRESENT = 세 엔드포인트 중 이미 있는 개수
PRESENT=0
status() {
    hdr "현재 egress 경로"
    note "account $ACCT   region $AWS_REGION   env $ENV"
    note "vpc $VPC ($VPC_CIDR)"

    PRESENT=0
    local s state
    for s in "${SERVICES[@]}"; do
        state=$(aws ec2 describe-vpc-endpoints \
                  --filters "Name=vpc-id,Values=$VPC" \
                            "Name=service-name,Values=com.amazonaws.$AWS_REGION.$s" \
                  --query 'VpcEndpoints[0].State' --output text 2>/dev/null)
        if [ "$state" = "available" ]; then
            ok   "com.amazonaws.$AWS_REGION.$s   $state"
            PRESENT=$((PRESENT + 1))
        elif [ -n "$state" ] && [ "$state" != "None" ]; then
            warn "com.amazonaws.$AWS_REGION.$s   $state  (아직 사용 불가)"
        else
            bad  "com.amazonaws.$AWS_REGION.$s   없음"
        fi
    done

    local nat
    nat=$(aws ec2 describe-route-tables \
            --filters "Name=vpc-id,Values=$VPC" "Name=tag:Name,Values=*-private" \
            --query 'RouteTables[0].Routes[?DestinationCidrBlock==`0.0.0.0/0`].NatGatewayId|[0]' \
            --output text 2>/dev/null)
    [ "$nat" = "None" ] && nat=""
    if [ -n "$nat" ]; then
        note ""
        note "private 서브넷의 0.0.0.0/0 은 여전히 $nat 로 갑니다."
        note "이건 정상이고 그대로 둡니다 — ECR pull·Cognito·타 리전 AgentCore"
        note "web search 는 엔드포인트가 없어 계속 NAT 를 씁니다. NAT 는 안 없어집니다."
    fi

    hdr "판정"
    if [ "$PRESENT" -eq 3 ]; then
        ok "Bedrock·STS 가 VPC 안에서 해석됩니다 — PrivateLink 적용 상태"
    elif [ "$PRESENT" -eq 0 ]; then
        warn "Bedrock 호출이 NAT 를 거쳐 퍼블릭 인터넷을 지납니다"
    else
        warn "부분 적용 ($PRESENT/3) — --apply 로 마저 적용하십시오"
    fi
}

# ---- plan ----
# 0 = 적용해도 안전, 1 = 할 일 없음, 2 = 안전하지 않음
PLANFILE="/tmp/bedrock-vpce-$$-$(date +%s).tfplan"
plan() {
    hdr "terraform plan (타깃 지정)"
    note "VPC 엔드포인트 4개만 타깃으로 잡습니다. 오래 운영한 배포에서 타깃 없이"
    note "plan 을 뜨면 이 변경과 무관한 드리프트까지 같이 잡힙니다 — 로테이트된"
    note "시크릿, 손으로 고친 리소스 같은 것들. 그걸 모르고 apply 하는 순간"
    note "평범한 변경이 장애가 됩니다."

    local out summary
    out=$(tf plan -no-color -lock-timeout=5m "${TARGETS[@]}" -out="$PLANFILE" 2>&1)
    summary=$(grep -E '^Plan: |^No changes' <<<"$out" | head -1)

    if [ -z "$summary" ]; then
        echo "$out" | tail -30
        die "terraform plan 이 요약을 못 냈습니다 — 위 출력을 확인하십시오."
    fi

    grep -E '^  # ' <<<"$out"
    echo "  $summary"

    if grep -q '^No changes' <<<"$summary"; then
        ok "할 일 없음 — 이미 설정과 일치합니다"
        return 1
    fi

    local add chg des
    add=$(sed -E 's/^Plan: ([0-9]+) to add.*/\1/' <<<"$summary")
    chg=$(sed -E 's/.* ([0-9]+) to change.*/\1/'  <<<"$summary")
    des=$(sed -E 's/.* ([0-9]+) to destroy.*/\1/' <<<"$summary")

    if [ "$chg" != "0" ] || [ "$des" != "0" ]; then
        bad "이 plan 이 무언가를 바꾸거나 지웁니다 — 진행하지 않습니다"
        note "기대값은 '<n> to add, 0 to change, 0 to destroy' 입니다."
        note "그 밖이면 타깃 리소스 자체가 교체되는 상황입니다. 확인:"
        note "  cd $TF_DIR && terraform show $PLANFILE"
        return 2
    fi

    ok "$add to add, 0 to change, 0 to destroy"
    return 0
}

# ---- 검증 ----
verify() {
    hdr "엔드포인트"
    aws ec2 describe-vpc-endpoints \
      --filters "Name=vpc-id,Values=$VPC" "Name=vpc-endpoint-type,Values=Interface" \
      --query "VpcEndpoints[?contains(ServiceName,'bedrock') || contains(ServiceName,'sts')].[ServiceName,State,PrivateDnsEnabled]" \
      --output table

    hdr "파드가 실제로 해석하는 주소"
    note "게이트웨이와 같은 DNS 경로를 타도록 네임스페이스 '$NS' 안에서 조회합니다."
    note "엔드포인트는 State=available 이 된 뒤에도 잠깐 연결을 안 받으므로,"
    note "443 확인은 한 번 실패로 단정하지 않고 최대 60초 재시도합니다."
    local pod out phase=""
    pod="vpce-check-$(date +%s)-$RANDOM"
    kubectl run "$pod" -n "$NS" --restart=Never --image="$PROBE_IMAGE" \
      --pod-running-timeout=5m --command -- sh -c "
        for h in bedrock-runtime.$AWS_REGION.amazonaws.com sts.$AWS_REGION.amazonaws.com; do
          echo \"== \$h\"
          nslookup \"\$h\" 2>/dev/null | grep -i '^Address' | tail -n +2
          if command -v nc >/dev/null 2>&1; then
            r=1
            for i in 1 2 3 4 5 6; do
              nc -z -w 5 \"\$h\" 443 2>/dev/null && { r=0; break; }
              sleep 10
            done
            [ \$r -eq 0 ] && echo '   tcp/443 도달' || echo '   tcp/443 FAILED (60초 재시도 후)'
          else
            echo '   tcp/443 확인 생략 (이미지에 nc 없음)'
          fi
        done" >/dev/null 2>&1

    local i
    for i in $(seq 1 90); do
        phase=$(kubectl get pod "$pod" -n "$NS" -o jsonpath='{.status.phase}' 2>/dev/null)
        { [ "$phase" = "Succeeded" ] || [ "$phase" = "Failed" ]; } && break
        sleep 4
    done
    out=$(kubectl logs "$pod" -n "$NS" 2>&1)
    kubectl delete pod "$pod" -n "$NS" --wait=false >/dev/null 2>&1
    echo "$out"

    hdr "결과"
    if grep -qE "Address.*: ${VPC_PREFIX}\." <<<"$out"; then
        ok "$VPC_PREFIX.x 로 해석됩니다 — VPC 내부 주소이므로 PrivateLink 적용됨"
    else
        bad "아직 퍼블릭 주소로 해석됩니다 — PrivateLink 미적용"
        note "private DNS 전파에 1분쯤 걸릴 수 있습니다. --verify 를 다시 돌려보십시오."
    fi
    if grep -q 'tcp/443 FAILED' <<<"$out"; then
        bad "엔드포인트로의 TCP 443 이 실패했습니다"
        note "엔드포인트 보안그룹이 private 서브넷 CIDR 에서 443 을 허용하는지 확인하십시오."
    fi

    hdr "다음"
    note "🔴 검증 전에 gateway-proxy 를 한 번 재시작하십시오:"
    note "     kubectl rollout restart deploy/<release>-gateway-proxy -n $NS"
    note ""
    note "경로 이전 자체는 재시작 없이도 됩니다 — 새 커넥션마다 DNS 를 다시"
    note "해석하니까요. 문제는 botocore 풀에 남아 있는 **죽은 커넥션**입니다."
    note "idle 350초에 조용히 끊긴 소켓을 재사용하면 502 ConnectionClosedError"
    note "가 나는데, 하필 이 변경 직후에 터지므로 '엔드포인트가 깨뜨렸다' 로"
    note "읽힙니다. 실제로 이 배포에서 2회 연속 502 를 겪었고, 파드를 새로"
    note "띄우자마자 200 이었습니다(풀에 죽은 소켓이 여러 개였음)."
    note ""
    note "추론이 멀쩡한지는 실제 /v1/messages 호출로 확인하십시오."
    note "⚠️ smoke-test.sh --with-bedrock 으로는 확인되지 않습니다 — 그 함수는"
    note "   (test_bedrock_e2e) 수동 절차를 화면에 출력할 뿐 호출하지 않습니다."
    note "   VK 는 api-key-helper, 주소는 gateway Ingress 의 ALB DNS 를 씁니다."
    note "문제가 생긴다면 볼 곳은 Bedrock 이 아니라 STS 입니다 — 같이 옮겨갔습니다:"
    note "  kubectl logs -n $NS deploy/llm-gateway-gateway-proxy | grep -i 'credential\\|assumerole'"
}

# ---- 실행 ----
case "$MODE" in
  plan)
    status
    [ "$PRESENT" -eq 3 ] && { note ""; note "이미 적용돼 있습니다. 할 일 없음."; exit 0; }
    plan; rc=$?
    [ $rc -eq 2 ] && exit 1
    [ $rc -eq 1 ] && exit 0
    hdr "적용하려면"
    note "  $0 $ENV --apply"
    ;;

  apply)
    status
    [ "$PRESENT" -eq 3 ] && { note ""; ok "이미 적용돼 있습니다. 할 일 없음."; exit 0; }
    plan; rc=$?
    [ $rc -eq 2 ] && exit 1
    [ $rc -eq 1 ] && exit 0

    hdr "적용 즉시 무엇이 바뀌나"
    note "엔드포인트가 생기는 순간 VPC 전체에서 private DNS 가 뒤집힙니다:"
    note "  bedrock-runtime.$AWS_REGION.amazonaws.com -> 내 서브넷의 엔드포인트 ENI"
    note "  sts.$AWS_REGION.amazonaws.com             -> 내 서브넷의 엔드포인트 ENI"
    note ""
    note "중요한 건 Bedrock 이 아니라 STS 입니다 — 모든 파드가 IRSA 자격증명을"
    note "이 경로로 갱신합니다. 엔드포인트 보안그룹은 private 서브넷 CIDR 에서"
    note "443 을 허용하고 Fargate 파드가 전부 그 서브넷에 있으므로 무중단이 기대값입니다."
    note ""
    note "타 리전 AgentCore web search 는 서비스도 리전도 달라 영향이 없고,"
    note "계속 NAT 를 씁니다."

    confirm "VPC $VPC ($ACCT / $AWS_REGION) 에 Bedrock·STS 엔드포인트를 만들까요?"

    hdr "적용 중"
    tf apply -no-color -lock-timeout=5m "$PLANFILE" || die "terraform apply 실패."

    verify
    ;;

  verify)
    status
    verify
    ;;

  rollback)
    status
    [ "$PRESENT" -eq 0 ] && { note ""; ok "되돌릴 것이 없습니다."; exit 0; }
    hdr "롤백"
    note "엔드포인트 3개와 보안그룹을 지웁니다. Bedrock·STS 는 다시 퍼블릭으로"
    note "해석되고 NAT 를 거쳐 나갑니다. 새 커넥션은 즉시 복귀하며, 그 밖에는"
    note "아무것도 건드리지 않습니다."
    confirm "VPC $VPC 의 Bedrock·STS 엔드포인트를 삭제할까요?"
    tf destroy -no-color -lock-timeout=5m "${TARGETS[@]}" -auto-approve \
      || die "terraform destroy 실패."
    status
    ;;
esac
