"""Isolated delivery of short-lived authentication email codes."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import format_datetime

import structlog

from app.core.auth_mailer_repository import AuthMailerRepository, LoginEmailClaim
from app.core.mailer_config import get_mailer_settings
from app.core.mailer_db import MailerSessionLocal
from app.core.smtp_delivery import DeliveryResult, send_smtp_message
from app.tasks.mailer_app import mailer_app

logger = structlog.get_logger("tasks.auth_mailer")
settings = get_mailer_settings()


def _message(claim: LoginEmailClaim) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = "Код входа в Aurum Pharma"
    message["From"] = settings.EMAIL_FROM
    message["To"] = claim.recipient_email
    message["Date"] = format_datetime(datetime.now(UTC))
    sender_domain = settings.EMAIL_FROM.rsplit("@", maxsplit=1)[-1]
    message["Message-ID"] = f"<auth-login-{claim.outbox_id}@{sender_domain}>"
    expiry = claim.code_expires_at.astimezone(UTC).strftime("%d.%m.%Y %H:%M UTC")
    message.set_content(
        "Здравствуйте!\n\n"
        f"Код входа в Aurum Pharma: {claim.login_code}\n\n"
        f"Код действует до {expiry} и может быть использован только один раз. "
        "Если вы не запрашивали вход, проигнорируйте это письмо.\n"
    )
    return message


def _send_smtp(claim: LoginEmailClaim) -> DeliveryResult:
    return send_smtp_message(_message(claim), settings)


async def _claim() -> LoginEmailClaim | None:
    async with MailerSessionLocal() as db:
        async with db.begin():
            return await AuthMailerRepository(db).claim_login_email(
                encryption_keyring=settings.encryption_keyring_json(),
                lease_seconds=settings.EMAIL_OUTBOX_CLAIM_TIMEOUT_SECONDS,
            )


async def _complete(claim: LoginEmailClaim, result: DeliveryResult) -> str | None:
    async with MailerSessionLocal() as db:
        async with db.begin():
            return await AuthMailerRepository(db).complete_login_email(
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
            "auth_email_delivery_completed",
            outbox_id=str(claim.outbox_id),
            attempt=claim.attempt_count,
            status=status or "stale_claim",
            error_code=result.error_code,
        )
        processed += 1
    return processed


@mailer_app.task(name="auth.process_login_emails")  # type: ignore[misc]
def process_login_emails() -> int:
    return asyncio.run(_process_pending())
