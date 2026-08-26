"""Authentication email rendering and local SMTP transport tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from types import TracebackType
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.core import smtp_delivery
from app.core.auth_mailer_repository import LoginEmailClaim
from app.tasks import auth_mailer


def _claim() -> LoginEmailClaim:
    return LoginEmailClaim(
        outbox_id=uuid4(),
        claim_token=uuid4(),
        recipient_email="local-user@example.com",
        login_code="482913",
        code_expires_at=datetime.now(UTC) + timedelta(minutes=10),
        attempt_count=1,
    )


def test_local_mail_catcher_accepts_plain_smtp_only_in_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[EmailMessage] = []
    calls: list[str] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, *, timeout: int) -> None:
            assert (host, port, timeout) == ("mailpit", 1025, 10)

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

        def login(self, user: str, password: str) -> None:
            pytest.fail("Local Mailpit must not receive credentials")

        def send_message(self, message: EmailMessage) -> None:
            sent.append(message)

    monkeypatch.setattr(auth_mailer.settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(auth_mailer.settings, "EMAIL_HOST", "mailpit")
    monkeypatch.setattr(auth_mailer.settings, "EMAIL_PORT", 1025)
    monkeypatch.setattr(auth_mailer.settings, "EMAIL_USER", "")
    monkeypatch.setattr(auth_mailer.settings, "EMAIL_USE_TLS", False)
    monkeypatch.setattr(auth_mailer.settings, "EMAIL_SMTP_TIMEOUT_SECONDS", 10)
    monkeypatch.setattr(smtp_delivery.smtplib, "SMTP", FakeSMTP)

    claim = _claim()
    assert auth_mailer._send_smtp(claim) == smtp_delivery.DeliveryResult("sent")
    assert calls == ["ehlo"]
    assert len(sent) == 1
    assert claim.login_code in sent[0].get_content()
    assert str(claim.outbox_id) in sent[0]["Message-ID"]


def test_auth_email_never_falls_back_to_plain_smtp_outside_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth_mailer.settings, "ENVIRONMENT", "staging")
    monkeypatch.setattr(auth_mailer.settings, "EMAIL_USE_TLS", False)
    monkeypatch.setattr(
        smtp_delivery.smtplib,
        "SMTP",
        lambda *_args, **_kwargs: pytest.fail("SMTP must not be contacted without TLS"),
    )

    assert auth_mailer._send_smtp(_claim()) == smtp_delivery.DeliveryResult(
        "permanent_failure",
        "smtp_tls_required",
    )


@pytest.mark.parametrize(
    ("host", "user", "password"),
    [
        ("smtp.example.com", "", ""),
        ("mailpit", "local-user", ""),
        ("mailpit", "", "local-password"),
    ],
)
def test_development_plain_smtp_is_restricted_to_credentialless_local_mailpit(
    monkeypatch: pytest.MonkeyPatch,
    host: str,
    user: str,
    password: str,
) -> None:
    monkeypatch.setattr(auth_mailer.settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(auth_mailer.settings, "EMAIL_HOST", host)
    monkeypatch.setattr(auth_mailer.settings, "EMAIL_USER", user)
    monkeypatch.setattr(auth_mailer.settings, "EMAIL_PASSWORD", SecretStr(password))
    monkeypatch.setattr(auth_mailer.settings, "EMAIL_USE_TLS", False)
    monkeypatch.setattr(
        smtp_delivery.smtplib,
        "SMTP",
        lambda *_args, **_kwargs: pytest.fail("Unsafe plaintext SMTP must not be contacted"),
    )

    assert auth_mailer._send_smtp(_claim()) == smtp_delivery.DeliveryResult(
        "permanent_failure",
        "smtp_tls_required",
    )
