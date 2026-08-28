#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# client-vpn.sh — AWS Client VPN 스탠드인 (고객사 S2S VPN 의 테스트 대체물)
#
# WHAT: 게이트웨이 VPC 에 Client VPN 엔드포인트(mutual TLS, split-tunnel)를 만들어
#       랩톱/PC 가 internal ALB(admin-ui · admin-api)에 닿게 한다.
# WHY:  ops/8-P-prod.md 2-9 — admin ALB 2개를 internal 로 세운 prod 를 VPN 없는 계정에서
#       검증하려면 사용자망→VPC 경로가 필요하다. 운영에선 S2S VPN 으로 대체된다.
#
# Usage: client-vpn.sh <env> up      # 인증서 → ACM → 엔드포인트 → 서브넷 연결 → VPC 인가
#        client-vpn.sh <env> config  # 클라이언트용 .ovpn 생성(인증서 인라인)
#        client-vpn.sh <env> status
#        client-vpn.sh <env> down    # 엔드포인트·ACM·SG 삭제 (인증서 파일은 남김)
#
# 입력: AWS_DEFAULT_REGION, terraform output(vpc_id · var.vpc_cidr),
#       VPN_CLIENT_CIDR (env 또는 docs/us-llm-gateway/update-scripts/config.env)
# 상태: ~/client-vpn/<env>/  (CA·서버·클라이언트 인증서, ARN, endpoint id)  — repo 밖
# 비용: 서브넷 연결 시간당 + 접속 시간당 과금 → 안 쓸 땐 down.
# ---------------------------------------------------------------------------
set -euo pipefail
ENV="${1:-}"; CMD="${2:-}"
[ -n "$ENV" ] && [ -n "$CMD" ] || { echo "Usage: $0 <dev|prod> up|config|status|down"; exit 1; }
: "${AWS_REGION:=${AWS_DEFAULT_REGION:-}}"
[ -n "$AWS_REGION" ] || { echo "ERROR: export AWS_DEFAULT_REGION=<region> 필요" >&2; exit 1; }
export AWS_DEFAULT_REGION="$AWS_REGION"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TF_DIR="$ROOT/deployment/terraform/environments/llm-gateway-$ENV"
CFG="$ROOT/docs/us-llm-gateway/update-scripts/config.env"
NAME="llm-gateway-$ENV-client-vpn"
D="$HOME/client-vpn/$ENV"; mkdir -p "$D"; chmod 700 "$D"

info() { echo "→ $*"; }; ok() { echo "✓ $*"; }; die() { echo "✗ $*" >&2; exit 1; }

# ── 입력값 ──────────────────────────────────────────────────────────────────
[ -d "$TF_DIR/.terraform" ] || die "terraform init 안 됨: $TF_DIR"
VPC_ID=$(cd "$TF_DIR" && terraform output -raw vpc_id)
VPC_CIDR=$(cd "$TF_DIR" && echo 'var.vpc_cidr' | terraform console 2>/dev/null | tr -d '"' | grep -oE '^[0-9.]+/[0-9]+$')
if [ -z "${VPN_CLIENT_CIDR:-}" ] && [ -f "$CFG" ]; then
  VPN_CLIENT_CIDR=$(sed -n 's/^VPN_CLIENT_CIDR="\{0,1\}\([^"]*\)"\{0,1\}$/\1/p' "$CFG")
fi
[ -n "${VPN_CLIENT_CIDR:-}" ] || die "VPN_CLIENT_CIDR 없음 — config.env 에 적거나 env 로 넘긴다 (8-P 2-6 ④-1)"
python3 - "$VPC_CIDR" "$VPN_CLIENT_CIDR" <<'PY' || exit 1
import sys, ipaddress
v, c = ipaddress.ip_network(sys.argv[1]), ipaddress.ip_network(sys.argv[2])
assert not v.overlaps(c), f"VPN_CLIENT_CIDR {c} 가 VPC {v} 와 겹침"
assert 12 <= c.prefixlen <= 22, f"VPN_CLIENT_CIDR 는 /12~/22 여야 함 (지금 /{c.prefixlen})"
PY
SUBNET_ID=$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" \
  "Name=tag:kubernetes.io/role/internal-elb,Values=1" \
  --query 'sort_by(Subnets,&AvailabilityZone)[0].SubnetId' --output text)
[ "$SUBNET_ID" != "None" ] && [ -n "$SUBNET_ID" ] || die "private 서브넷(internal-elb 태그)을 못 찾음: $VPC_ID"

endpoint_id() {
  aws ec2 describe-client-vpn-endpoints --filters "Name=tag:Name,Values=$NAME" \
    --query 'ClientVpnEndpoints[0].ClientVpnEndpointId' --output text 2>/dev/null | grep -v '^None$' || true
}

# ── 인증서 (openssl, mutual TLS) ─────────────────────────────────────────────
make_certs() {
  [ -f "$D/ca.crt" ] && { ok "인증서 재사용: $D"; return; }
  info "CA · 서버 · 클라이언트 인증서 생성 ($D)"
  # OpenVPN(AWS VPN Client) 의 `remote-cert-tls server` 는 서버 인증서에 keyUsage + EKU serverAuth 를
  # 요구한다 — keyUsage 가 빠지면 "TLS handshake error"(실제로 겪음). 클라이언트도 같은 형식으로.
  ( cd "$D"
    printf 'basicConstraints=critical,CA:TRUE\nkeyUsage=critical,keyCertSign,cRLSign\n' > ca.ext
    openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -keyout ca.key -out ca.crt \
      -subj "/CN=$NAME-ca" -extensions v3_ca -config <(printf '[req]\ndistinguished_name=dn\n[dn]\n[v3_ca]\n'; cat ca.ext) >/dev/null 2>&1
    for kind in server client; do
      openssl req -newkey rsa:2048 -nodes -keyout $kind.key -out $kind.csr -subj "/CN=$NAME-$kind" >/dev/null 2>&1
      [ "$kind" = server ] && KU="digitalSignature,keyEncipherment" || KU="digitalSignature"
      printf 'basicConstraints=CA:FALSE\nkeyUsage=critical,%s\nextendedKeyUsage=%sAuth\nsubjectAltName=DNS:%s\n' \
        "$KU" "$kind" "$NAME-$kind" > $kind.ext
      openssl x509 -req -in $kind.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 3650 \
        -out $kind.crt -extfile $kind.ext >/dev/null 2>&1
      openssl x509 -in $kind.crt -noout -text | grep -q "Key Usage" || { echo "✗ $kind.crt 에 keyUsage 누락" >&2; exit 1; }
    done
    chmod 600 ./*.key )
  ok "인증서 생성 완료"
}
import_certs() {
  for kind in server client; do
    if [ ! -s "$D/$kind.arn" ]; then
      aws acm import-certificate --certificate "fileb://$D/$kind.crt" --private-key "fileb://$D/$kind.key" \
        --certificate-chain "fileb://$D/ca.crt" --tags "Key=Name,Value=$NAME-$kind" \
        --query CertificateArn --output text > "$D/$kind.arn"
      ok "ACM import: $kind → $(cat "$D/$kind.arn")"
    fi
  done
}

cmd_up() {
  local EP; EP=$(endpoint_id)
  if [ -n "$EP" ]; then ok "엔드포인트 이미 있음: $EP"; else
    make_certs; import_certs
    local SG
    SG=$(aws ec2 describe-security-groups --filters "Name=vpc-id,Values=$VPC_ID" "Name=group-name,Values=$NAME" \
      --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null | grep -v '^None$' || true)
    [ -n "$SG" ] || SG=$(aws ec2 create-security-group --vpc-id "$VPC_ID" --group-name "$NAME" \
      --description "Client VPN endpoint ENIs ($ENV)" --query GroupId --output text)
    info "엔드포인트 생성 (client CIDR $VPN_CLIENT_CIDR, split-tunnel, mutual TLS)"
    EP=$(aws ec2 create-client-vpn-endpoint --client-cidr-block "$VPN_CLIENT_CIDR" \
      --server-certificate-arn "$(cat "$D/server.arn")" \
      --authentication-options "Type=certificate-authentication,MutualAuthentication={ClientRootCertificateChainArn=$(cat "$D/client.arn")}" \
      --connection-log-options Enabled=false --split-tunnel --transport-protocol udp \
      --vpc-id "$VPC_ID" --security-group-ids "$SG" \
      --tag-specifications "ResourceType=client-vpn-endpoint,Tags=[{Key=Name,Value=$NAME}]" \
      --query ClientVpnEndpointId --output text)
    echo "$EP" > "$D/endpoint.id"; ok "엔드포인트: $EP"
  fi
  local ASSOC
  ASSOC=$(aws ec2 describe-client-vpn-target-networks --client-vpn-endpoint-id "$EP" \
    --query "ClientVpnTargetNetworks[?TargetNetworkId=='$SUBNET_ID' && Status.Code!='disassociated'].AssociationId | [0]" --output text)
  if [ "$ASSOC" = "None" ] || [ -z "$ASSOC" ]; then
    info "private 서브넷 연결: $SUBNET_ID (과금 시작)"
    aws ec2 associate-client-vpn-target-network --client-vpn-endpoint-id "$EP" --subnet-id "$SUBNET_ID" >/dev/null
  else ok "서브넷 이미 연결됨: $SUBNET_ID"; fi
  aws ec2 authorize-client-vpn-ingress --client-vpn-endpoint-id "$EP" --target-network-cidr "$VPC_CIDR" \
    --authorize-all-groups --description "VPC $VPC_CIDR" >/dev/null 2>&1 \
    && ok "인가 규칙: $VPC_CIDR" || ok "인가 규칙 이미 있음: $VPC_CIDR"
  info "엔드포인트 available 대기(서브넷 연결 후 수 분)"
  for _ in $(seq 1 40); do
    local ST; ST=$(aws ec2 describe-client-vpn-endpoints --client-vpn-endpoint-ids "$EP" --query 'ClientVpnEndpoints[0].Status.Code' --output text)
    [ "$ST" = "available" ] && break; sleep 15
  done
  cmd_status; echo; echo "다음: $0 $ENV config   # .ovpn 생성 → 랩톱/PC 로 복사"
}

cmd_config() {
  local EP; EP=$(endpoint_id); [ -n "$EP" ] || die "엔드포인트 없음 — 먼저 up"
  local OUT="$D/$ENV-client.ovpn"
  aws ec2 export-client-vpn-client-configuration --client-vpn-endpoint-id "$EP" --output text > "$OUT"
  { echo "<cert>"; cat "$D/client.crt"; echo "</cert>"; echo "<key>"; cat "$D/client.key"; echo "</key>"; } >> "$OUT"
  chmod 600 "$OUT"
  ok "클라이언트 설정: $OUT"
  cat <<EOF
   랩톱으로 복사:  scp -i <키> ubuntu@<배포 EC2 IP>:$OUT .
   접속 클라이언트: AWS VPN Client(Mac/Windows) 또는 OpenVPN 호환 클라이언트에서 이 파일을 프로파일로 추가
   접속 후 확인:   dig +short admin.<DOMAIN> (사설 IP) → 브라우저 https://admin.<DOMAIN>
EOF
}

cmd_status() {
  local EP; EP=$(endpoint_id); [ -n "$EP" ] || { echo "엔드포인트 없음 ($NAME)"; return; }
  aws ec2 describe-client-vpn-endpoints --client-vpn-endpoint-ids "$EP" \
    --query 'ClientVpnEndpoints[0].{id:ClientVpnEndpointId,status:Status.Code,clientCidr:ClientCidrBlock,dns:DnsName,splitTunnel:SplitTunnel}' --output table
  aws ec2 describe-client-vpn-target-networks --client-vpn-endpoint-id "$EP" \
    --query 'ClientVpnTargetNetworks[].{subnet:TargetNetworkId,status:Status.Code}' --output table
  aws ec2 describe-client-vpn-authorization-rules --client-vpn-endpoint-id "$EP" \
    --query 'AuthorizationRules[].{cidr:DestinationCidr,status:Status.Code}' --output table
  echo "활성 접속: $(aws ec2 describe-client-vpn-connections --client-vpn-endpoint-id "$EP" \
    --query "length(Connections[?Status.Code=='active'])" --output text)"
}

cmd_down() {
  local EP; EP=$(endpoint_id); [ -n "$EP" ] || { echo "엔드포인트 없음 — 정리할 것 없음"; return; }
  info "서브넷 연결 해제"
  for A in $(aws ec2 describe-client-vpn-target-networks --client-vpn-endpoint-id "$EP" \
      --query "ClientVpnTargetNetworks[?Status.Code!='disassociated'].AssociationId" --output text); do
    aws ec2 disassociate-client-vpn-target-network --client-vpn-endpoint-id "$EP" --association-id "$A" >/dev/null
  done
  for _ in $(seq 1 40); do
    local N; N=$(aws ec2 describe-client-vpn-target-networks --client-vpn-endpoint-id "$EP" \
      --query "length(ClientVpnTargetNetworks[?Status.Code!='disassociated'])" --output text)
    [ "$N" = "0" ] && break; sleep 10
  done
  aws ec2 delete-client-vpn-endpoint --client-vpn-endpoint-id "$EP" >/dev/null && ok "엔드포인트 삭제: $EP"
  local SG; SG=$(aws ec2 describe-security-groups --filters "Name=vpc-id,Values=$VPC_ID" "Name=group-name,Values=$NAME" \
    --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null | grep -v '^None$' || true)
  [ -z "$SG" ] || { sleep 20; aws ec2 delete-security-group --group-id "$SG" >/dev/null 2>&1 && ok "SG 삭제: $SG" || echo "SG $SG 는 잠시 후 수동 삭제"; }
  for kind in server client; do
    [ -s "$D/$kind.arn" ] && aws acm delete-certificate --certificate-arn "$(cat "$D/$kind.arn")" && rm -f "$D/$kind.arn" && ok "ACM 삭제: $kind"
  done
  rm -f "$D/endpoint.id"; echo "인증서 파일은 $D 에 남김(재생성 시 재사용)"
}

case "$CMD" in
  up) cmd_up ;; config) cmd_config ;; status) cmd_status ;; down) cmd_down ;;
  *) die "unknown command: $CMD" ;;
esac
