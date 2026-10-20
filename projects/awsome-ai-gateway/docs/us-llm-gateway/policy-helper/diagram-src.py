#!/usr/bin/env python3
"""policyHelper 입문 문서용 다이어그램 4장 — 벽 안·연결선은 ASCII 만, 한글은 그림 아래 범례에만.
실행: python3 diagram-src.py [arch|flow|compare|desktop]   (인자 없으면 4장 모두)
검증: python3 diagram-src.py arch | python3 <skill>/validate.py
canvas.py 위치: .claude/skills/aws-draw-ascii-diagram/ (CANVAS_DIR 환경변수로 덮어쓰기 가능)"""
import os, sys
CANVAS_DIR = os.environ.get(
    'CANVAS_DIR',
    os.path.expanduser('~/llm-gateway-workspace/.claude/skills/aws-draw-ascii-diagram'))
sys.path.insert(0, CANVAS_DIR)
from canvas import Canvas, boxed, harrow, vrail

W = 85  # GitHub 다크 가독성: 코드 블록 85열 이하


def arch():
    """구성 요소: 정책 소스 -> Claude Code -> helper -> (선택) 정책 서비스 -> 세션."""
    c = Canvas(W, 22)
    X1, BW = 0, 36          # 왼쪽 열
    X2, BW2 = 50, 35        # 오른쪽 열 (끝 = 84)
    M1, M2 = 12, 62         # 열별 레일
    boxed(c, 0, X1, 5, BW, 'Admin policy source',
          ['Windows HKLM | macOS plist', 'managed-settings.json',
           '"policyHelper": { path, ... }'])
    vrail(c, M1, 4, 8, ['(1) read at launch'])
    boxed(c, 8, X1, 5, BW, 'Claude Code  (CLI / Agent SDK)',
          ['loads settings at launch', 'managed settings <- helper', ''])
    boxed(c, 8, X2, 5, BW2, 'policyHelper executable',
          ['no args', 'env: CLAUDE_CODE_VERSION', 'stdout: JSON <= 1 MiB'])
    harrow(c, 9, X1 + BW - 1, X2, '(2) exec')
    harrow(c, 11, X1 + BW - 1, X2, '(4) stdout', reverse=True)
    vrail(c, M2, 12, 16, ['(3) HTTPS (optional)'])
    boxed(c, 16, X2, 4, BW2, 'Policy service',
          ['e.g. admin-api GET /policy', 'user/group -> models, env'])
    vrail(c, M1, 12, 16, ['(5) apply'])
    boxed(c, 16, X1, 4, BW, 'Session',
          ['permissions, availableModels,', 'env, apiKeyHelper, MCP ...'])
    return c.render()


def flow():
    """실행 흐름: 시작 -> 소스 선택 -> helper 실행 -> 성공/실패/빈 봉투 -> 주기 갱신."""
    c = Canvas(W, 34)
    X1, BW = 0, 40          # 본 흐름
    X2, BW2 = 52, 33        # 분기 결과 (끝 = 84)
    M = 18
    RW = X1 + BW - 1        # 본 흐름 박스 오른쪽 벽
    boxed(c, 0, X1, 3, BW, '1. Launch', ['claude / claude -p / Agent SDK'])
    vrail(c, M, 2, 5)
    boxed(c, 5, X1, 4, BW, '2. Select managed source',
          ['server-managed > HKLM/plist > file', 'policyHelper key in selected source?'])
    boxed(c, 5, X2, 4, BW2, 'Helper NOT run',
          ['server-managed settings win', '(no key -> static policy)'])
    harrow(c, 6, RW, X2, 'shadowed')
    vrail(c, M, 8, 11, ['yes'])
    boxed(c, 11, X1, 10, BW, '3. Run helper',
          ['no args, within timeoutMs', 'parse stdout (single JSON object)',
           '', '', '', '', '', ''])
    boxed(c, 11, X2, 4, BW2, 'FAIL -> refuse to start',
          ['non-zero / timeout / bad JSON', 'print reason and exit'])
    harrow(c, 12, RW, X2, 'fail')
    boxed(c, 16, X2, 4, BW2, 'Output w/o managedSettings',
          ['exit 0, contributes nothing', 'other sources apply'])
    harrow(c, 17, RW, X2, 'empty')
    vrail(c, M, 20, 24, ['OK: {"managedSettings": {...}}'])
    boxed(c, 24, X1, 4, BW, '4. Session starts',
          ['helper output = ONLY managed source', 'MDM / file / HKCU / parent ignored'])
    vrail(c, M, 27, 30, ['every refreshIntervalMs'])
    boxed(c, 30, X1, 4, BW, '5. Background refresh',
          ['OK   -> replace policy (no restart)', 'FAIL -> keep last policy, /status'])
    return c.render()


def compare():
    """apiKeyHelper(자격증명) vs policyHelper(정책) — 한 세션 안에서의 역할 분담."""
    c = Canvas(W, 18)
    X1, BW = 0, 26
    X2, BW2 = 40, 45        # 끝 = 84
    RW = X1 + BW - 1
    boxed(c, 0, X1, 17, BW, 'Claude Code',
          ['CLI / Agent SDK', '', 'at launch:', ' resolve policy', '', '',
           '', '', 'per API call:', ' get credential', '', '', '', 'then:',
           ' send request'])
    boxed(c, 0, X2, 6, BW2, 'policyHelper  (policy plane)',
          ['when: launch + every refreshIntervalMs', 'out : {"managedSettings": {...}}',
           'sets: models, permissions, env,', '      apiKeyHelper path'])
    harrow(c, 3, RW, X2, '(1) launch')
    boxed(c, 7, X2, 5, BW2, 'apiKeyHelper  (credential plane)',
          ['when: before API calls (cached, TTL)', 'out : API key (VK: vk-...)',
           'uses: OIDC login cache'])
    harrow(c, 9, RW, X2, '(2) request')
    boxed(c, 13, X2, 4, BW2, 'LLM Gateway -> Bedrock',
          ['Authorization: Bearer vk-...', 'gateway-proxy checks VK, budget'])
    harrow(c, 14, RW, X2, '(3) Bearer')
    return c.render()


def desktop():
    """Claude Desktop: 자체 관리형 설정(+bootstrapUrl) 과 PC 의 Claude Code 관리형 소스가 만나는 곳."""
    c = Canvas(W, 22)
    X1, BW = 0, 38
    X2, BW2 = 50, 35        # 끝 = 84
    M1 = 12
    RW = X1 + BW - 1
    boxed(c, 0, X1, 5, BW, 'Desktop managed config',
          ['HKLM\\SOFTWARE\\Policies\\Claude', 'gateway URL, credential helper',
           'bootstrapUrl  (no policyHelper)'])
    boxed(c, 0, X2, 5, BW2, 'Bootstrap endpoint (optional)',
          ['HTTPS, per-user settings', 'Desktop keys only', ''])
    harrow(c, 1, RW, X2, '(a) fetch')
    vrail(c, M1, 4, 8, ['(b) apply'])
    boxed(c, 8, X1, 4, BW, 'Claude Desktop app',
          ['Cowork / Code / Chat tabs', 'model list, disabled tools'])
    vrail(c, M1, 11, 15, ['(c) parent settings'])
    boxed(c, 15, X1, 5, BW, 'Claude Code inside Desktop',
          ['no admin source -> Desktop rules', 'policyHelper -> helper output only',
           'helper fail -> session refused'])
    boxed(c, 15, X2, 5, BW2, 'Claude Code admin source',
          ['HKLM\\...\\Policies\\ClaudeCode', 'or managed-settings.json',
           '"policyHelper": { ... }'])
    harrow(c, 17, RW, X2, '(d) read', reverse=True)
    return c.render()


if __name__ == '__main__':
    parts = {'arch': arch, 'flow': flow, 'compare': compare, 'desktop': desktop}
    names = sys.argv[1:] or list(parts)
    print('\n\n'.join(parts[n]() for n in names))
