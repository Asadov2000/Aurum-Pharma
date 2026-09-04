"""Maintenance tasks for the authentication domain."""

from __future__ import annotations

import asyncio

import structlog
from sqlalchemy import text

from app.core.system_worker_db import SystemWorkerSessionLocal
from app.tasks.celery_app import celery_app

logger = structlog.get_logger("tasks.auth")


async def _expire_email_codes_async() -> int:
    async with SystemWorkerSessionLocal() as db:
        async with db.begin():
            result = await db.execute(
                text("SELECT public.worker_purge_expired_email_codes(:limit)"),
                {"limit": 1000},
            )
            return int(result.scalar_one())


@celery_app.task(name="auth.expire_email_codes")  # type: ignore[misc]
def expire_email_codes() -> int:
    removed = asyncio.run(_expire_email_codes_async())
    logger.info("expire_email_codes", removed=removed)
    return removed


async def _expire_sessions_async() -> int:
    async with SystemWorkerSessionLocal() as db:
        async with db.begin():
            result = await db.execute(
                text("SELECT public.worker_purge_expired_sessions(:limit)"),
                {"limit": 1000},
            )
            return int(result.scalar_one())


@celery_app.task(name="auth.expire_sessions")  # type: ignore[misc]
def expire_sessions() -> int:
    removed = asyncio.run(_expire_sessions_async())
    logger.info("expire_sessions", removed=removed)
    return removed
