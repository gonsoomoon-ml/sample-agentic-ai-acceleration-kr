#!/usr/bin/env bash
# bootstrap-ec2.sh
# Deployment EC2 (Ubuntu LTS, x86_64) 1회 부트스트랩:
#   설치 도구(aws-cli v2·terraform·kubectl·helm·docker·jq·psql16) + Claude Code + 버전 검증.
# 멱등: 이미 설치된 것은 건너뜀. 실패 시 즉시 중단.
#
# 요구: Ubuntu(apt + lsb_release 로 PGDG·HashiCorp 저장소를 붙임) · x86_64
#       (aws-cli/kubectl 을 amd64 로 내려받음). 22.04/24.04/26.04 에서 확인.
#
# 사용:  bash bootstrap-ec2.sh
# 참고:  docker 그룹 반영은 재로그인(또는 `newgrp docker`) 후 적용됨.
set -euo pipefail

# ---- 튜너블 (env 로 override 가능) --------------------------------------------
AWS_REGION="${AWS_REGION:-us-west-2}"
# kubectl 은 EKS API 서버 ±1 minor 스큐 내여야 함. EKS 기본 1.29~1.30 → 1.30 계열이 안전.
KUBECTL_VERSION="${KUBECTL_VERSION:-v1.30.9}"
PG_MAJOR="${PG_MAJOR:-16}"          # Aurora PostgreSQL 16.x 에 맞춤

log()  { printf '\033[1;34m[bootstrap]\033[0m %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

# ---- 0. 기본 apt 패키지 -------------------------------------------------------
# docker-buildx 필수: apt 의 docker.io 는 buildx 플러그인을 안 깔아 legacy builder
# 가 기본이 되는데, 일부 Dockerfile 의 optional-glob(`COPY foo.cr[t] ...`, 파일
# 없으면 skip)이 legacy 에서 "no source files were specified" 로 하드 실패한다
# (§3-5 이미지 빌드에서 gateway-proxy·admin-api·migration 3개가 죽음). buildx 를
# 깔아야 `docker build` 가 BuildKit 백엔드를 쓴다(daemon.json 설정만으로는 부족).
log "apt 패키지 (unzip jq git tmux docker.io docker-buildx) 설치"
sudo apt-get update -y
sudo apt-get install -y unzip jq git tmux docker.io docker-buildx ca-certificates gnupg lsb-release wget curl
sudo usermod -aG docker "$USER" || true   # 재로그인 후 그룹 반영

# BuildKit 을 데몬 기본으로도 명시(buildx 와 함께). (멱등)
if [ "$(sudo cat /etc/docker/daemon.json 2>/dev/null | jq -r '.features.buildkit' 2>/dev/null)" != "true" ]; then
  log "Docker BuildKit 활성화 (/etc/docker/daemon.json)"
  sudo mkdir -p /etc/docker
  existing=$(sudo cat /etc/docker/daemon.json 2>/dev/null || echo '{}')
  echo "$existing" | jq '.features.buildkit = true' | sudo tee /etc/docker/daemon.json >/dev/null
  sudo systemctl restart docker || true
else
  log "Docker BuildKit 이미 활성 — 건너뜀"
fi

# ---- 1. psql (PostgreSQL client, PG_MAJOR) — PGDG 레포 ------------------------
# 배포판 기본 postgresql-client 는 릴리스마다 다르므로(22.04=14 등), Aurora 16 에
# 맞춰 PGDG 에서 PG_MAJOR 를 명시 설치한다.
if ! psql --version 2>/dev/null | grep -q " ${PG_MAJOR}\."; then
  log "postgresql-client-${PG_MAJOR} 설치 (PGDG)"
  sudo install -d /usr/share/keyrings
  wget -qO- https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    | sudo gpg --dearmor -o /usr/share/keyrings/postgresql.gpg
  echo "deb [signed-by=/usr/share/keyrings/postgresql.gpg] http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" \
    | sudo tee /etc/apt/sources.list.d/pgdg.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y "postgresql-client-${PG_MAJOR}"
else
  log "psql ${PG_MAJOR} 이미 설치됨 — 건너뜀"
fi

# ---- 2. aws-cli v2 ------------------------------------------------------------
if ! aws --version 2>/dev/null | grep -q "aws-cli/2"; then
  log "aws-cli v2 설치"
  curl -fsSL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o /tmp/awscliv2.zip
  ( cd /tmp && unzip -q -o awscliv2.zip && sudo ./aws/install --update )
  rm -f /tmp/awscliv2.zip
else
  log "aws-cli v2 이미 설치됨 — 건너뜀"
fi

# ---- 3. terraform (>= 1.9, HashiCorp apt) ------------------------------------
if ! have terraform; then
  log "terraform 설치 (HashiCorp apt)"
  wget -qO- https://apt.releases.hashicorp.com/gpg \
    | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp.gpg
  echo "deb [signed-by=/usr/share/keyrings/hashicorp.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" \
    | sudo tee /etc/apt/sources.list.d/hashicorp.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y terraform
else
  log "terraform 이미 설치됨 — 건너뜀"
fi

# ---- 4. kubectl (KUBECTL_VERSION, EKS 스큐 맞춤) ------------------------------
if ! kubectl version --client 2>/dev/null | grep -q "${KUBECTL_VERSION}"; then
  log "kubectl ${KUBECTL_VERSION} 설치"
  curl -fsSLo /tmp/kubectl "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl"
  sudo install -m 0755 /tmp/kubectl /usr/local/bin/kubectl
  rm -f /tmp/kubectl
else
  log "kubectl ${KUBECTL_VERSION} 이미 설치됨 — 건너뜀"
fi

# ---- 5. helm 3 ---------------------------------------------------------------
if ! have helm; then
  log "helm 3 설치"
  curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash
else
  log "helm 이미 설치됨 — 건너뜀"
fi

# ---- 6. Claude Code (native installer → ~/.local/bin/claude, Node 불필요) -----
export PATH="$HOME/.local/bin:$PATH"   # 이 스크립트 안에서만 유효 (아래에서 영속화)
if ! have claude; then
  log "Claude Code 설치 (native installer, stable 채널)"
  curl -fsSL https://claude.ai/install.sh | bash -s stable
else
  log "Claude Code 이미 설치됨 — 건너뜀"
fi

# PATH 영속화 — ~/.profile 에 의존하면 안 된다:
#  (a) ~/.profile 의 ~/.local/bin 블록은 `[ -d ... ]` 를 **로그인 시점에** 평가하는데,
#      그 디렉터리는 방금 이 스크립트가 만들었으므로 이번 로그인엔 이미 늦었다.
#  (b) VS Code/Cursor 통합 터미널은 non-login 셸이라 ~/.profile 을 아예 안 읽는다.
# → 모든 대화형 셸이 읽는 ~/.bashrc 에 넣는다(멱등).
if ! grep -qs '\.local/bin' "$HOME/.bashrc"; then
  log "PATH 영속화: ~/.bashrc 에 ~/.local/bin 추가"
  printf '\n# Claude Code (bootstrap-ec2.sh)\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$HOME/.bashrc"
else
  log "~/.bashrc 에 ~/.local/bin 이미 있음 — 건너뜀"
fi

# Bedrock(US Geo) 설정 — 배포 EC2 instance role 자격증명 사용.
# us-west-2 는 In-Region 미지원이라 반드시 US Geo 프로파일(us.anthropic.*)로 pin 해야 함.
# 설치 프로그램이 settings.json 을 먼저 만들어 두므로(예: {"autoUpdatesChannel":"stable"})
# "파일 없을 때만 생성" 으로는 절대 안 써진다 → 기존 내용을 보존한 채 .env 만 병합한다.
CLAUDE_SETTINGS="$HOME/.claude/settings.json"
mkdir -p "$HOME/.claude"
[ -f "$CLAUDE_SETTINGS" ] || echo '{}' > "$CLAUDE_SETTINGS"
if jq -e '.env.CLAUDE_CODE_USE_BEDROCK' "$CLAUDE_SETTINGS" >/dev/null 2>&1; then
  log "Claude Code Bedrock 설정 이미 있음 — 건너뜀"
else
  log "Claude Code Bedrock(US Geo) 설정 병합: $CLAUDE_SETTINGS"
  _tmp=$(mktemp)
  jq --arg region "$AWS_REGION" '.env = (.env // {}) + {
    "CLAUDE_CODE_USE_BEDROCK": "1",
    "AWS_REGION": $region,
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "us.anthropic.claude-opus-4-8",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "us.anthropic.claude-sonnet-5",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "us.anthropic.claude-haiku-4-5-20251001-v1:0"
  }' "$CLAUDE_SETTINGS" > "$_tmp" && mv "$_tmp" "$CLAUDE_SETTINGS"
fi

# ---- 7. 버전 검증 (설치 확인 + 최소/정확 버전 게이팅) --------------------------
log "버전 검증"
fail=0
ver_ge() { [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]; }  # $1 >= $2 ?
report()  { local mark="✅"; [ "$3" -ne 0 ] && { mark="❌"; fail=1; }
            printf '  %-9s %-16s %s %s\n' "$1:" "${2:-—}" "$mark" "${4:-}"; }

# aws-cli major = 2
v=$(aws --version 2>&1 | grep -oE 'aws-cli/[0-9]+(\.[0-9]+)*' | cut -d/ -f2 || true)
case "$v" in 2.*) report aws-cli "$v" 0 ;; *) report aws-cli "$v" 1 "major=2 필요" ;; esac

# terraform >= 1.9
v=$(terraform version 2>/dev/null | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)
if [ -n "$v" ] && ver_ge "$v" 1.9.0; then report terraform "$v" 0; else report terraform "$v" 1 "≥ 1.9 필요"; fi

# kubectl 설치 확인 + EKS 스큐(1.29~1.31 권장) 경고
v=$(kubectl version --client -o yaml 2>/dev/null | grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' | head -1 | tr -d v || true)
if [ -z "$v" ]; then report kubectl "$v" 1 "설치 안 됨"
elif ver_ge "$v" 1.29.0 && ver_ge 1.31.99 "$v"; then report kubectl "$v" 0 "(EKS 스큐 OK)"
else report kubectl "$v" 0 "⚠️ EKS 버전과 ±1 minor 확인"; fi

# helm major = 3
v=$(helm version --short 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)
case "$v" in 3.*) report helm "$v" 0 ;; *) report helm "$v" 1 "major=3 필요" ;; esac

# docker >= 24
v=$(docker --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1 || true)
if [ -n "$v" ] && ver_ge "$v" 24.0.0; then report docker "$v" 0; else report docker "$v" 1 "≥ 24 필요"; fi

# jq >= 1.6
v=$(jq --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1 || true)
if [ -n "$v" ] && ver_ge "$v" 1.6; then report jq "$v" 0; else report jq "$v" 1 "≥ 1.6 필요"; fi

# psql major = PG_MAJOR (16)
v=$(psql --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1 || true)
case "$v" in ${PG_MAJOR}.*) report psql "$v" 0 ;; *) report psql "$v" 1 "major=${PG_MAJOR} 필요" ;; esac

# claude 설치 확인
v=$(claude --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1 || true)
if [ -n "$v" ]; then report claude "$v" 0; else report claude "$v" 1 "설치 안 됨"; fi

echo
if [ "$fail" -eq 0 ]; then
  log "✅ 전부 설치·버전 검증 통과."
  echo
  log "다음: ⚠️ docker 그룹은 이 셸에 반영 안 됨 ('docker ps' → permission denied)."
  log "  그룹 자격은 세션 생성 시점에 박히므로 usermod 로는 지금 셸이 안 바뀐다."
  log "  → SSH 연결을 끊고 재접속(권장). 지금 셸에서만 급히 쓰려면: newgrp docker"
  log "  (claude 의 PATH 는 ~/.bashrc 에 넣었으니 새 셸이면 자동 해결된다.)"
  echo
  log "새 셸에서:"
  log "  echo 'export AWS_DEFAULT_REGION=${AWS_REGION}' >> ~/.bashrc   # 셸마다 유지"
  log "  export AWS_DEFAULT_REGION=${AWS_REGION}"
  log "  claude --version && docker ps && aws sts get-caller-identity   # 셋 다 되면 완료"
else
  log "⚠️ 위 ❌ 항목의 버전 미달/누락을 해결하고 재실행"
  exit 1
fi
