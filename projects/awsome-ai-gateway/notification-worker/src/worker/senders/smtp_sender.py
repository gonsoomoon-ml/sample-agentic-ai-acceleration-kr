# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

from __future__ import annotations

import structlog

from worker.config import Settings
from worker.schemas.recipients import RenderedEmail
from worker.senders.base import EmailSendError

logger = structlog.get_logger(__name__)


class SMTPEmailSender:
    """SMTP를 통한 이메일 전송 (Post-MVP, optional-deps: aiosmtplib)."""

    def __init__(self, settings: Settings) -> None:
        try:
            import aiosmtplib  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "aiosmtplib is required for SMTP sender. Install with: uv sync --extra smtp"
            ) from exc

        if not settings.smtp_host:
            raise ValueError("SMTP_HOST is required for smtp sender type")

        self._host = settings.smtp_host
        self._port = settings.smtp_port or 587
        self._starttls = settings.smtp_starttls
        self._username = settings.smtp_username
        self._password = (
            settings.smtp_password.get_secret_value() if settings.smtp_password else None
        )
        self._sender_address = settings.email_sender_address
        self._sender_name = settings.email_sender_name

    async def send(self, email: RenderedEmail) -> None:
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        import aiosmtplib

        msg = MIMEMultipart("alternative")
        msg["Subject"] = email.subject
        msg["From"] = f"{self._sender_name} <{self._sender_address}>"
        msg["To"] = email.recipient.email
        msg.attach(MIMEText(email.html_body, "html", "utf-8"))

        try:
            await aiosmtplib.send(
                msg,
                hostname=self._host,
                port=self._port,
                username=self._username,
                password=self._password,
                use_tls=False,
                start_tls=self._starttls,
            )
        except aiosmtplib.SMTPRecipientsRefused as exc:
            raise EmailSendError(str(exc), retryable=False) from exc
        except aiosmtplib.SMTPAuthenticationError as exc:
            raise EmailSendError(str(exc), retryable=False) from exc
        except Exception as exc:
            raise EmailSendError(str(exc), retryable=True) from exc
