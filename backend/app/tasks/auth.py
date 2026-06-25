"""Celery tasks for the auth domain.

Phase 1 has no real SMTP yet. The login endpoint returns `dev_code` only in
development; the worker records dispatch attempts without logging credentials.
A real provider is wired up in phase 2; the call signature does not change.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import structlog

from app.core.config import get_settings
from app.core.db import WorkerSessionLocal
from app.core.time import utc_now
from app.domains.auth.repository import AuthRepository
from app.tasks.celery_app import celery_app

logger = structlog.get_logger("tasks.auth")


@celery_app.task(name="auth.send_email_code")  # type: ignore[misc]
def send_email_code(email: str, code: str) -> None:
    """Phase 1 stub — no real SMTP yet (wired up in phase 2; signature unchanged).

    A login code is a credential and email is PII, so neither goes to logs. In
    development the API response carries `dev_code`; production SMTP will use
    this same task signature without changing callers.
    """
    logger.info("send_email_code", environment=get_settings().ENVIRONMENT)


async def _expire_email_codes_async() -> int:
    async with WorkerSessionLocal() as db:
        async with db.begin():
            repo = AuthRepository(db)
            removed = await repo.delete_expired_email_codes(
                older_than=utc_now() - timedelta(hours=24)
            )
            return removed


@celery_app.task(name="auth.expire_email_codes")  # type: ignore[misc]
def expire_email_codes() -> int:
    removed = asyncio.run(_expire_email_codes_async())
    logger.info("expire_email_codes", removed=removed)
    return removed


async def _expire_sessions_async() -> int:
    async with WorkerSessionLocal() as db:
        async with db.begin():
            repo = AuthRepository(db)
            removed = await repo.delete_expired_sessions(older_than=utc_now() - timedelta(days=30))
            return removed


@celery_app.task(name="auth.expire_sessions")  # type: ignore[misc]
def expire_sessions() -> int:
    removed = asyncio.run(_expire_sessions_async())
    logger.info("expire_sessions", removed=removed)
    return removed
