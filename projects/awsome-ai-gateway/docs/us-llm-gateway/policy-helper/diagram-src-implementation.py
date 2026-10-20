#!/usr/bin/env python3
"""02-policy-helper-gateway-design.md 의 그림 생성기. 그림은 손으로 고치지 않고 이 파일로 다시 만든다.
사용: python3 diagram-src-implementation.py [arch|flow]  (인자 없으면 둘 다)"""
import os, sys
SKILL = os.path.expanduser('~/llm-gateway-workspace/.claude/skills/aws-draw-ascii-diagram')
sys.path.insert(0, SKILL)
from canvas import Canvas, boxed, harrow, vrail, dw


def arch():
    c = Canvas(96, 20)
    BW = 24
    X1, X2, X3 = 0, 36, 72
    M2, M3 = X2 + 12, X3 + 12
    # row A: PC side
    boxed(c, 0, X1, 5, BW, 'Claude Code (CLI)', ['managed settings', '= helper output'])
    boxed(c, 0, X2, 5, BW, 'policy-helper.exe', ['baseline (built-in)', '+ server fragment'])
    boxed(c, 0, X3, 5, BW, 'policy cache', ['last good policy', 'per user'])
    harrow(c, 1, X1 + BW - 1, X2, '(1) exec')
    harrow(c, 3, X1 + BW - 1, X2, '(5) JSON', reverse=True)
    harrow(c, 2, X2 + BW - 1, X3, '(4) save')
    # helper -> gateway (public)
    vrail(c, M2, 4, 8, ['(2) GET /v1/policy, Bearer vk'])
    # row B
    boxed(c, 8, X2, 4, BW, 'gateway-proxy', ['VK auth -> user_id', 'no admin-api call'])
    boxed(c, 8, X3, 4, BW, 'admin UI (prod)', ['policy editor', 'preview + audit'])
    # gateway -> DB, admin UI -> admin-api
    vrail(c, M2, 11, 15, ['(3) read'])
    vrail(c, M3, 11, 15, ['(a) save'])
    # row C
    boxed(c, 15, X2, 5, BW, 'policy DB', ['PoC: existing tables', 'prod: saved results'])
    boxed(c, 15, X3, 5, BW, 'admin-api (prod)', ['validate', 'compute on save'])
    harrow(c, 17, X2 + BW - 1, X3, '(b) write', reverse=True)
    out = c.render().split('\n')
    legend = [
        "",
        "  (1) Claude Code 가 시작할 때와 refreshIntervalMs 마다 helper 를 인자 없이 실행",
        "  (2) helper 가 api-key-helper 의 VK 캐시를 읽어 게이트웨이(공개 주소)에 정책을 요청",
        "  (3) PoC: 게이트웨이가 기존 테이블(허용 모델 등)로 그때그때 계산",
        "      운영: admin-api 가 미리 계산해 둔 행을 사용자 ID 로 읽기만 — admin-api 호출 없음",
        "  (4) 성공한 조각을 캐시에 저장 — 다음번 서버 불통 때 이 값으로 응답",
        "  (5) 기본값 + 조각을 stdout 으로 출력 -> 그 세션의 유일한 관리형 설정",
        "  (a)(b) 운영: 관리자가 저장하면 admin-api 가 검증하고,",
        "         영향받는 사용자의 조각을 다시 계산해 저장",
    ]
    return out + legend


def flow():
    c = Canvas(96, 34)
    BW = 42
    X1, X2 = 0, 54
    M1 = X1 + 21
    boxed(c, 0, X1, 3, BW, '1. Start', ['run by Claude Code, no args'])
    vrail(c, M1, 2, 5)
    boxed(c, 5, X1, 4, BW, '2. Load baseline (built-in)', ['gateway URL, apiKeyHelper,', 'fixed env'])
    vrail(c, M1, 8, 11)
    boxed(c, 11, X1, 3, BW, '3. Read VK (api-key-helper cache)', ['vk-cache.json, read only'])
    boxed(c, 11, X2, 3, BW, 'A. no VK / expired', ['skip fetch -> use cache/baseline'])
    harrow(c, 12, X1 + BW - 1, X2, 'no VK')
    vrail(c, M1, 13, 16)
    boxed(c, 16, X1, 3, BW, '4. GET /v1/policy (timeout 3s)', ['server fragment'])
    boxed(c, 16, X2, 3, BW, 'B. gateway down / timeout', ['use cache, else baseline only'])
    harrow(c, 17, X1 + BW - 1, X2, 'fail')
    vrail(c, M1, 18, 21)
    boxed(c, 21, X1, 3, BW, '5. Check fragment', ['JSON object, no "_" keys'])
    boxed(c, 21, X2, 3, BW, 'C. bad fragment', ['drop it -> use cache, else baseline'])
    harrow(c, 22, X1 + BW - 1, X2, 'invalid')
    vrail(c, M1, 23, 26)
    boxed(c, 26, X1, 3, BW, '6. Merge + save cache', ['baseline <- fragment, save to cache'])
    vrail(c, M1, 28, 31)
    boxed(c, 31, X1, 3, BW, '7. Print envelope, exit 0', ['{"managedSettings": {...}}'])
    out = c.render().split('\n')
    legend = [
        "",
        "  A·B·C 는 실패가 아니라 대체 경로입니다 — 모두 6·7 로 이어져 항상 exit 0 으로 끝납니다",
        "  4 의 타임아웃(3초)은 policyHelper.timeoutMs(10초)보다 충분히 짧게 잡습니다",
    ]
    return out + legend


if __name__ == '__main__':
    which = sys.argv[1:] or ['arch', 'flow']
    for i, name in enumerate(which):
        lines = {'arch': arch, 'flow': flow}[name]()
        if i:
            print()
        print('\n'.join(lines))
        print(f"[check] {name} max width {max(dw(l) for l in lines)}", file=sys.stderr)
