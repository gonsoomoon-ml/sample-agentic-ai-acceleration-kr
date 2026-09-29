# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import PaginatedResponse, UserRole


# ── Requests ──


class DepartmentCreateRequest(BaseModel):
    name: str = Field(max_length=255)
    org_id: str | None = None  # defaults to the single org in MVP


class TeamCreateRequest(BaseModel):
    name: str = Field(max_length=255)
    department_id: str


class SetLeaderRequest(BaseModel):
    user_id: str


class TransferUserRequest(BaseModel):
    team_id: str


# ── Responses ──


class DepartmentResponse(BaseModel):
    id: str
    name: str
    org_id: str
    created_at: datetime


class TeamResponse(BaseModel):
    id: str
    name: str
    department_id: str
    leader_user_id: str | None = None
    created_at: datetime


class UserResponse(BaseModel):
    id: str
    email: str
    display_name: str
    role: UserRole
    team_id: str | None = None
    team_name: str | None = None
    is_active: bool
    created_at: datetime


class UserListResponse(PaginatedResponse[UserResponse]):
    pass


class TeamListItem(BaseModel):
    id: str
    name: str
    department_id: str
    department_name: str | None = None
    leader_user_id: str | None = None
    member_count: int = 0


class TeamListResponse(BaseModel):
    items: list[TeamListItem]


# ── Org Tree ──


class UserSearchItem(BaseModel):
    """조직 트리 검색 결과의 사용자 항목.

    ``UserResponse`` 를 쓰지 않는 이유: 검색은 트리에서 노드를 찾아 선택하는 용도라
    ``created_at``/``is_active`` 가 불필요하고, 반대로 ``team_id`` 는 **반드시** 필요하다 —
    팀 멤버는 트리에서 lazy-load 되므로 조상 경로를 펼치려면 팀 id 를 알아야 한다.
    """

    id: str
    email: str
    display_name: str
    role: UserRole
    team_id: str | None = None
    team_name: str | None = None


class UserSearchResponse(BaseModel):
    items: list[UserSearchItem] = []
    #: limit 에서 잘렸는지. UI 가 "결과가 더 있습니다" 힌트를 띄운다 — 잘림을 숨기면
    #: 사용자는 찾는 사람이 없다고 결론 내린다.
    truncated: bool = False


class OrgNodeMeta(BaseModel):
    #: **항상 사람 수.** 노드 타입과 무관하다.
    #:
    #: ⚠️ 예전엔 이 한 필드가 노드 타입에 따라 다른 것을 뜻했다 — TEAM 에서는 사람 수,
    #:    DEPARTMENT 에서는 (서버가 넣은) 사람 수인데 UI 는 그걸 **팀 수**로 읽었다.
    #:    그래서 20팀×50명 부서가 화면에 "팀 1000개" 로 떴다. 필드 하나가 두 의미를
    #:    가지면 그 오독은 언젠가 반드시 일어난다 — 그래서 team_count 를 분리했다.
    member_count: int | None = None
    #: 하위 팀 수. DEPARTMENT / ORGANIZATION 에서만 채운다. TEAM·USER 는 None.
    team_count: int | None = None
    leader_name: str | None = None
    leader_user_id: str | None = None
    email: str | None = None
    role: UserRole | None = None
    team_name: str | None = None


class OrgTreeNode(BaseModel):
    id: str
    name: str
    type: str  # ORGANIZATION | DEPARTMENT | TEAM | USER
    children: list["OrgTreeNode"] = []
    meta: OrgNodeMeta


OrgTreeNode.model_rebuild()


class ScopedAllowedClientsResponse(BaseModel):
    """팀/조직 단위 앱 접근 정책 (alembic 0038). scope_id = team_id 또는 org_id.

    clients 0개 = 정책 없음(상위로 폴백 / 최종적으로는 제한 없음) — 전면 거부가 아님.
    """

    scope_id: str
    clients: list[str]
