"""Celery tasks for the notifications domain.

`process_pending_deliveries` — remains callable but intentionally does not
claim deliveries until a real provider adapter with idempotency is configured.

`purge_old_notifications` — weekly retention cleanup of read messages.

`check_expiring_licenses` — daily 08:00 scan for branches whose
license_expires_at is within 30 days; notifies the tenant owner.
"""

from __future__ import annotations

import asyncio

import structlog
from sqlalchemy import text

from app.core.system_worker_db import SystemWorkerSessionLocal
from app.tasks.celery_app import celery_app

logger = structlog.get_logger("tasks.notifications")


async def _process_pending_async() -> dict[str, int]:
    return {"sent": 0, "failed": 0}


@celery_app.task(name="notifications.process_pending_deliveries")  # type: ignore[misc]
def process_pending_deliveries() -> dict[str, int]:
    result = asyncio.run(_process_pending_async())
    logger.info("notification_delivery_adapter_disabled", **result)
    return result


async def _purge_old_async() -> int:
    async with SystemWorkerSessionLocal() as db:
        async with db.begin():
            result = await db.execute(
                text("SELECT public.worker_purge_old_notifications(:limit)"),
                {"limit": 1000},
            )
            return int(result.scalar_one())


@celery_app.task(name="notifications.purge_old_notifications")  # type: ignore[misc]
def purge_old_notifications() -> int:
    removed = asyncio.run(_purge_old_async())
    logger.info("purge_old_notifications", removed=removed)
    return removed


async def _check_expiring_licenses_async() -> int:
    """Notify active tenant owners about licenses expiring within 30 days."""
    async with SystemWorkerSessionLocal() as db:
        async with db.begin():
            result = await db.execute(
                text("SELECT public.worker_enqueue_expiring_license_notifications(:limit)"),
                {"limit": 500},
            )
            return int(result.scalar_one())


@celery_app.task(name="notifications.check_expiring_licenses")  # type: ignore[misc]
def check_expiring_licenses() -> int:
    notified = asyncio.run(_check_expiring_licenses_async())
    logger.info("check_expiring_licenses", notified=notified)
    return notified
