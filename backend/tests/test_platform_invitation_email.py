"""SMTP transport tests without external network access."""

from __future__ import annotations

import smtplib
import ssl
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from types import TracebackType
from uuid import uuid4

import pytest

from app.domains.platform_accounts.repository import PlatformInvitationEmailClaim
from app.tasks import platform_accounts as email_task


def _claim() -> PlatformInvitationEmailClaim:
    return PlatformInvitationEmailClaim(
        outbox_id=uuid4(),
        claim_token=uuid4(),
        recipient_email="candidate@example.com",
        activation_token="smtp-platform-activation-token-123456789",
        invitation_expires_at=datetime.now(UTC) + timedelta(hours=24),
        attempt_count=1,
    )


def test_smtp_transport_requires_tls_and_uses_fragment_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[EmailMessage] = []
    calls: list[str] = []
    tls_context = ssl.create_default_context()

    class FakeSMTP:
        def __init__(self, host: str, port: int, *, timeout: int) -> None:
            assert (host, port, timeout) == ("smtp.example.com", 587, 10)

        def __enter__(self) -> FakeSMTP:
            return self

        def __exit__(
            self,
            exc_type: type[BaseException] | None,
            exc: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

        def ehlo(self) -> None:
            calls.append("ehlo")

        def starttls(self, *, context: ssl.SSLContext) -> None:
            assert context is tls_context
            calls.append("starttls")

        def login(self, user: str, password: str) -> None:
            assert (user, password) == ("mailer@example.com", "provider-token")
            calls.append("login")

        def send_message(self, message: EmailMessage) -> None:
            sent.append(message)

    monkeypatch.setattr(email_task.settings, "EMAIL_HOST", "smtp.example.com")
    monkeypatch.setattr(email_task.settings, "EMAIL_PORT", 587)
    monkeypatch.setattr(email_task.settings, "EMAIL_USER", "mailer@example.com")
    monkeypatch.setattr(email_task.settings, "EMAIL_PASSWORD", "provider-token")
    monkeypatch.setattr(email_task.settings, "EMAIL_FROM", "no-reply@example.com")
    monkeypatch.setattr(email_task.settings, "EMAIL_USE_TLS", True)
    monkeypatch.setattr(email_task.settings, "EMAIL_SMTP_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(email_task.settings, "PUBLIC_APP_URL", "https://app.example.com")
    monkeypatch.setattr(email_task.ssl, "create_default_context", lambda: tls_context)
    monkeypatch.setattr(email_task.smtplib, "SMTP", FakeSMTP)

    claim = _claim()
    assert email_task._send_smtp(claim) == email_task.DeliveryResult("sent")
    assert calls == ["ehlo", "starttls", "ehlo", "login"]
    assert len(sent) == 1
    body = sent[0].get_content()
    assert f"#token={claim.activation_token}" in body
    assert f"?token={claim.activation_token}" not in body
    assert str(claim.outbox_id) in sent[0]["Message-ID"]


def test_smtp_transport_fails_closed_when_tls_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(email_task.settings, "EMAIL_USE_TLS", False)
    monkeypatch.setattr(
        email_task.smtplib,
        "SMTP",
        lambda *_args, **_kwargs: pytest.fail("SMTP must not be contacted without TLS"),
    )

    assert email_task._send_smtp(_claim()) == email_task.DeliveryResult(
        "permanent_failure",
        "smtp_tls_required",
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            smtplib.SMTPResponseException(451, b"temporary recipient detail"),
            email_task.DeliveryResult("transient_failure", "smtp_transient_error"),
        ),
        (
            smtplib.SMTPResponseException(550, b"permanent recipient detail"),
            email_task.DeliveryResult("permanent_failure", "smtp_permanent_error"),
        ),
        (
            ssl.SSLError("certificate details"),
            email_task.DeliveryResult("permanent_failure", "tls_verification_failed"),
        ),
        (
            TimeoutError("network details"),
            email_task.DeliveryResult("transient_failure", "smtp_unavailable"),
        ),
    ],
)
def test_smtp_errors_are_classified_without_persisting_exception_text(
    monkeypatch: pytest.MonkeyPatch,
    error: BaseException,
    expected: email_task.DeliveryResult,
) -> None:
    class FailingSMTP:
        def __init__(self, host: str, port: int, *, timeout: int) -> None:
            raise error

    monkeypatch.setattr(email_task.smtplib, "SMTP", FailingSMTP)

    result = email_task._send_smtp(_claim())
    assert result == expected
    assert "detail" not in (result.error_code or "")
