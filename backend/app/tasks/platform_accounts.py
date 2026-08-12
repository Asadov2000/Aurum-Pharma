"""Secure SMTP delivery for platform account invitations."""

from __future__ import annotations

import asyncio
import smtplib
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime
from typing import Literal
from urllib.parse import quote

import structlog

from app.core.mailer_config import get_mailer_settings
from app.core.mailer_db import MailerSessionLocal
from app.domains.platform_accounts.repository import (
    PlatformAccountsRepository,
    PlatformInvitationEmailClaim,
)
from app.tasks.mailer_app import mailer_app

logger = structlog.get_logger("tasks.platform_accounts")
settings = get_mailer_settings()
DeliveryOutcome = Literal["sent", "transient_failure", "permanent_failure"]


@dataclass(frozen=True)
class DeliveryResult:
    outcome: DeliveryOutcome
    error_code: str | None = None


def _activation_url(token: str) -> str:
    base_url = str(settings.PUBLIC_APP_URL).rstrip("/")
    return f"{base_url}/activate-platform#token={quote(token, safe='')}"


def _message(claim: PlatformInvitationEmailClaim) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = "Приглашение в команду Aurum Pharma"
    message["From"] = settings.EMAIL_FROM
    message["To"] = claim.recipient_email
    message["Date"] = format_datetime(datetime.now(UTC))
    sender_domain = settings.EMAIL_FROM.rsplit("@", maxsplit=1)[-1]
    message["Message-ID"] = f"<platform-invitation-{claim.outbox_id}@{sender_domain}>"
    expiry = claim.invitation_expires_at.astimezone(UTC).strftime("%d.%m.%Y %H:%M UTC")
    message.set_content(
        "Здравствуйте!\n\n"
        "Вас пригласили в команду Aurum Pharma. Для создания пароля откройте ссылку:\n"
        f"{_activation_url(claim.activation_token)}\n\n"
        f"Ссылка действует до {expiry}. Если вы не ожидали это письмо, проигнорируйте его.\n"
    )
    return message


def _send_smtp(claim: PlatformInvitationEmailClaim) -> DeliveryResult:
    if not settings.EMAIL_USE_TLS:
        return DeliveryResult("permanent_failure", "smtp_tls_required")
    result = DeliveryResult("sent")
    try:
        with smtplib.SMTP(
            settings.EMAIL_HOST,
            settings.EMAIL_PORT,
            timeout=settings.EMAIL_SMTP_TIMEOUT_SECONDS,
        ) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            if settings.EMAIL_USER:
                smtp.login(settings.EMAIL_USER, settings.EMAIL_PASSWORD.get_secret_value())
            smtp.send_message(_message(claim))
    except smtplib.SMTPRecipientsRefused:
        result = DeliveryResult("permanent_failure", "recipient_rejected")
    except smtplib.SMTPAuthenticationError:
        result = DeliveryResult("permanent_failure", "smtp_authentication_failed")
    except smtplib.SMTPSenderRefused:
        result = DeliveryResult("permanent_failure", "sender_rejected")
    except smtplib.SMTPResponseException as exc:
        outcome: DeliveryOutcome = (
            "permanent_failure" if exc.smtp_code >= 500 else "transient_failure"
        )
        error_code = "smtp_permanent_error" if exc.smtp_code >= 500 else "smtp_transient_error"
        result = DeliveryResult(outcome, error_code)
    except (ssl.CertificateError, ssl.SSLError):
        result = DeliveryResult("permanent_failure", "tls_verification_failed")
    except (smtplib.SMTPException, OSError, TimeoutError):
        result = DeliveryResult("transient_failure", "smtp_unavailable")
    return result


async def _claim() -> PlatformInvitationEmailClaim | None:
    async with MailerSessionLocal() as db:
        async with db.begin():
            return await PlatformAccountsRepository(db).claim_invitation_email(
                encryption_keyring=settings.encryption_keyring_json(),
                lease_seconds=settings.EMAIL_OUTBOX_CLAIM_TIMEOUT_SECONDS,
            )


async def _complete(
    claim: PlatformInvitationEmailClaim,
    result: DeliveryResult,
) -> str | None:
    async with MailerSessionLocal() as db:
        async with db.begin():
            return await PlatformAccountsRepository(db).complete_invitation_email(
                outbox_id=claim.outbox_id,
                claim_token=claim.claim_token,
                outcome=result.outcome,
                error_code=result.error_code,
            )


async def _process_pending() -> int:
    processed = 0
    for _ in range(settings.EMAIL_OUTBOX_BATCH_SIZE):
        claim = await _claim()
        if claim is None:
            break
        result = await asyncio.to_thread(_send_smtp, claim)
        status = await _complete(claim, result)
        logger.info(
            "platform_invitation_delivery_completed",
            outbox_id=str(claim.outbox_id),
            attempt=claim.attempt_count,
            status=status or "stale_claim",
            error_code=result.error_code,
        )
        processed += 1
    return processed


@mailer_app.task(name="platform_accounts.process_invitation_emails")  # type: ignore[misc]
def process_invitation_emails() -> int:
    return asyncio.run(_process_pending())
