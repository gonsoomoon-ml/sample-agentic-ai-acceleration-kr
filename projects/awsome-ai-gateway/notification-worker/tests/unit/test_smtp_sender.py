# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""SMTPEmailSender TLS 판정 — use_tls 미설정(None)은 포트로 자동 결정한다."""

from __future__ import annotations

import pytest

pytest.importorskip("aiosmtplib")

from worker.config import Settings
from worker.senders.smtp_sender import SMTPEmailSender


def _settings(**kw) -> Settings:
    base = {"smtp_host": "smtp.example.com"}
    base.update(kw)
    return Settings(**base)


def test_unset_use_tls_on_465_uses_implicit_tls() -> None:
    """useTls 미설정 + 465 — implicit TLS(옛 하드코딩 use_tls=True 와 동일 효과)."""
    s = SMTPEmailSender(_settings(smtp_port=465))
    assert s._use_tls is True
    assert s._starttls is False


def test_unset_use_tls_on_587_uses_starttls() -> None:
    """useTls 미설정 + 587 — STARTTLS. 차트 기본값(useTls 비움)의 일반 배포."""
    s = SMTPEmailSender(_settings(smtp_port=587))
    assert s._use_tls is False
    assert s._starttls is True


def test_explicit_use_tls_wins_over_port() -> None:
    """useTls 명시는 포트 판정을 이긴다 — 465 + false = STARTTLS on 465(비표준이지만 허용)."""
    s = SMTPEmailSender(_settings(smtp_port=465, smtp_use_tls=False))
    assert s._use_tls is False
    assert s._starttls is True


def test_explicit_use_tls_disables_starttls() -> None:
    s = SMTPEmailSender(_settings(smtp_port=465, smtp_use_tls=True, smtp_starttls=True))
    assert s._use_tls is True
    assert s._starttls is False
