# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""팀/조직별 앱 접근 정책: auth.team_allowed_clients + auth.org_allowed_clients

Revision ID: 0038
Revises: 0037
Create Date: 2026-09-22

## 무엇을 추가하나

``auth.user_allowed_clients``(0010)는 사용자×앱 축이었는데 **사용자별 행밖에 없어서**
유저가 많으면 앱 접근을 한 명씩 설정해야 했다. 모델 축의 ``model.team_allowed_models``
(팀 정책 → 개인 override)와 같은 구조를 앱 축에도 도입한다:

    auth.team_allowed_clients   팀 T 의 멤버는 앱 codex 를 쓸 수 있나   (팀 × 앱)
    auth.org_allowed_clients    조직 기본값 — 팀 정책도 없는 멤버에게 적용 (조직 × 앱)

## 우선순위 — user > team > org > 제한 없음

게이트웨이(auth_service)와 유효 정책(effective_policy_service)이 같은 순서로 해결한다:

    user_allowed_clients 행 존재  → 그 화이트리스트만 (하위 정책 무시)
    team_allowed_clients 행 존재 → 팀 화이트리스트
    org_allowed_clients  행 존재 → 조직 화이트리스트
    전부 없음                    → 제한 없음(모든 앱 허용, fail-open — 기존 동작 유지)

team 경유 해결이라 **team_id 가 없는 사용자**는 org 기본값도 타지 않는다
(user→team→dept→org 체인이 끊기므로) — 팀 미배정 사용자는 기존처럼 전체 허용이다.

⚠️ 0행 = "정책 없음"(상위로 폴백/전체 허용)이지 "전면 거부"가 아니다.
   model_aliases.allowed_clients 의 ``{}`` = 전면 거부와 의미가 다르다 — 같은
   필드명인데 반대 fail 방향이라 섞으면 사고 난다(0035 문서 참조).

## 되돌리기

테이블을 떨어뜨리면 팀/조직 정책이 함께 사라진다 — 되돌리는 방향은 접근을 넓힌다.
"""

from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS: 손으로 만들어 둔 환경에서도 멱등.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS auth.team_allowed_clients (
            team_id    UUID        NOT NULL REFERENCES auth.teams(id) ON DELETE CASCADE,
            client     VARCHAR(32) NOT NULL CHECK (client IN ('claude-code','cowork','codex')),
            created_by UUID        NOT NULL REFERENCES auth.users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (team_id, client)
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_team_allowed_clients_team "
        "ON auth.team_allowed_clients (team_id)"
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS auth.org_allowed_clients (
            org_id     UUID        NOT NULL REFERENCES auth.organizations(id) ON DELETE CASCADE,
            client     VARCHAR(32) NOT NULL CHECK (client IN ('claude-code','cowork','codex')),
            created_by UUID        NOT NULL REFERENCES auth.users(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (org_id, client)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS auth.org_allowed_clients")
    op.execute("DROP TABLE IF EXISTS auth.team_allowed_clients")
