# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""TEAM_LEADER 의 열람 가능 팀 집합 — analytics·dashboard 공용 단일 진실원.

정책(2026-09-18 확정): TEAM_LEADER 는 **리더로 지정된 팀만** 본다.
소속 팀(`User.team_id`)은 범위에 포함하지 않는다 — 소속과 리더 지정은 다른 개념이다:

  * 소속 팀 = Cognito `Claude_*` 그룹에서 해석된 `User.team_id` (1개)
  * 리더 지정 = `auth.teams.leader_user_id` — 복수 팀이 같은 사용자를 가리킬 수
    있고(한 사람이 여러 팀의 리더), 자신이 소속되지 않은 팀의 리더도 가능하다.

따라서 "소속 team1+team2 이지만 리더는 team1" 인 사용자는 team1 만 본다 —
team2 의 비용·사용자·추이는 그 사람에게 열리지 않는다.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import CurrentUser
from app.models.auth import Team


async def led_team_ids(session: AsyncSession, actor: CurrentUser) -> set[uuid.UUID]:
    """``actor`` 가 리더로 지정된 팀 id 집합을 반환한다 (TEAM_LEADER 전용 의미).

    ADMIN 에게는 호출 의미가 없다(전사가 범위) — 호출자가 role 을 먼저 분기할 것.
    리더인 팀이 하나도 없으면 빈 set — 호출자가 그 경우를 정책대로 처리한다
    (analytics/dashboard 는 빈 결과 또는 403).
    """
    rows = await session.execute(
        select(Team.id).where(Team.leader_user_id == actor.user_id)
    )
    return set(rows.scalars().all())


def scope_cache_token(team_ids: set[uuid.UUID] | list[uuid.UUID]) -> str:
    """캐시 키용 안정 토큰 — 같은 팀 집합은 항상 같은 문자열이 된다.

    집합을 그대로 넣으면 행위자·조회 경로와 무관하게 동일 키가 되어, 서로 다른
    리더의 응답이 캐시에서 섞이지 않는다. 전사(ADMIN)는 별도 상수("all")를 쓸 것.
    """
    import hashlib

    joined = ",".join(sorted(str(t) for t in team_ids))
    return "led:" + hashlib.sha1(joined.encode()).hexdigest()[:12]
