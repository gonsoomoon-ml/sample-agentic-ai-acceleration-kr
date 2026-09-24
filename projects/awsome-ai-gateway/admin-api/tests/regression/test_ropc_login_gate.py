# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""ADMIN_ROPC_ENABLED 게이트 — OIDC hosted-UI 배포에서 ROPC 로그인 경로 폐쇄.

배경 — admin-ui 의 ``/login`` 폼 경로(``POST /v1/auth/admin/login``,
``/new-password``)는 평문 비밀번호가 admin-api 를 통과하는 ROPC 경로다.
HTTPS/도메인이 준비돼 admin-ui 가 OIDC hosted-UI 로그인으로 동작하는 배포에서는
이 경로를 끄는 것이 맞고, admin-api 는 공인 ALB 에 노출되므로 UI 게이트만으로는
엔드포인트가 닫히지 않는다 — 서버 쪽에서도 닫아야 한다.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.routers.auth_admin import _ropc_enabled


def test_ropc_gate_returns_404_when_disabled():
    settings = MagicMock()
    settings.ADMIN_ROPC_ENABLED = False
    with patch("app.core.config.get_settings", return_value=settings):
        with pytest.raises(HTTPException) as exc:
            _ropc_enabled()
    assert exc.value.status_code == 404


def test_ropc_gate_passes_when_enabled():
    settings = MagicMock()
    settings.ADMIN_ROPC_ENABLED = True
    with patch("app.core.config.get_settings", return_value=settings):
        assert _ropc_enabled() is None


def test_ropc_gate_is_wired_as_router_dependency():
    """게이트가 두 엔드포인트에 실제로 걸려 있는가 — 함수만 있고 배선이 빠지면
    위 테스트들이 통과해도 경로는 열려 있다."""
    from app.routers.auth_admin import router

    dep_fns = {getattr(d.dependency, "__name__", "") for d in router.dependencies}
    assert "_ropc_enabled" in dep_fns
