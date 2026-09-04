"""Celery tasks for the notifications domain.

`process_pending_deliveries` — runs every minute (configured in
celery_app.beat_schedule). Pulls up to N pending deliveries and lets
the service attempt each one. Phase 1 is log-only.

`purge_old_notifications` — weekly retention cleanup of read messages.

`check_expiring_licenses` — daily 08:00 scan for branches whose
license_expires_at is within 30 days; notifies the tenant owner.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.system_worker_db import SystemWorkerSessionLocal
from app.core.time import utc_now
from app.domains.auth.models import AppUser
from app.domains.foundation.models import Branch
from app.domains.notifications.repository import NotificationsRepository
from app.domains.notifications.service import NotificationsService
from app.domains.roles.models import TenantMembership, TenantOwnership
from app.tasks.celery_app import celery_app

logger = structlog.get_logger("tasks.notifications")

DEFAULT_BATCH = 50


async def _active_tenant_owners(db: AsyncSession, *, tenant_id: UUID) -> list[AppUser]:
    result = await db.execute(
        select(AppUser)
        .join(
            TenantMembership,
            TenantMembership.user_id == AppUser.id,
        )
        .join(
            TenantOwnership,
            TenantOwnership.membership_id == TenantMembership.id,
        )
        .where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.status == "active",
            TenantOwnership.tenant_id == tenant_id,
            TenantOwnership.is_active.is_(True),
            AppUser.status == "active",
        )
    )
    return list(result.scalars().all())


async def _process_pending_async() -> dict[str, int]:
    sent = 0
    failed = 0
    async with SystemWorkerSessionLocal() as db:
        async with db.begin():
            repo = NotificationsRepository(db)
            service = NotificationsService(repo)
            pending = await repo.list_pending_deliveries(limit=DEFAULT_BATCH)
            for delivery in pending:
                updated = await service.attempt_delivery(delivery)
                if updated.status == "sent":
                    sent += 1
                elif updated.status == "failed":
                    failed += 1
    return {"sent": sent, "failed": failed}


@celery_app.task(name="notifications.process_pending_deliveries")  # type: ignore[misc]
def process_pending_deliveries() -> dict[str, int]:
    result = asyncio.run(_process_pending_async())
    logger.info("process_pending_deliveries", **result)
    return result


async def _purge_old_async() -> int:
    async with SystemWorkerSessionLocal() as db:
        async with db.begin():
            repo = NotificationsRepository(db)
            service = NotificationsService(repo)
            return await service.purge_old()


@celery_app.task(name="notifications.purge_old_notifications")  # type: ignore[misc]
def purge_old_notifications() -> int:
    removed = asyncio.run(_purge_old_async())
    logger.info("purge_old_notifications", removed=removed)
    return removed


async def _check_expiring_licenses_async() -> int:
    """Notify active tenant owners about licenses expiring within 30 days."""
    notified = 0
    cutoff: date = utc_now().date() + timedelta(days=30)
    async with SystemWorkerSessionLocal() as db:
        async with db.begin():
            stmt = select(Branch).where(
                Branch.is_active.is_(True),
                Branch.license_expires_at.is_not(None),
                Branch.license_expires_at <= cutoff,
            )
            branches = list((await db.execute(stmt)).scalars().all())
            if not branches:
                return 0
            service = NotificationsService(NotificationsRepository(db))
            for branch in branches:
                # branch.license_expires_at is non-null thanks to the WHERE
                # clause above; assert so mypy sees the narrowed type.
                assert branch.license_expires_at is not None
                users = await _active_tenant_owners(
                    db,
                    tenant_id=branch.tenant_id,
                )
                for user in users:
                    await service.notify(
                        tenant_id=branch.tenant_id,
                        user_id=user.id,
                        event_type="license_expiring",
                        title="Лицензия скоро истекает",
                        body=(
                            f"У точки '{branch.name}' срок действия лицензии — "
                            f"{branch.license_expires_at}. Подайте документы заранее."
                        ),
                        data={
                            "branch_id": str(branch.id),
                            "license_expires_at": branch.license_expires_at.isoformat(),
                        },
                        severity="warning",
                    )
                    notified += 1
    return notified


@celery_app.task(name="notifications.check_expiring_licenses")  # type: ignore[misc]
def check_expiring_licenses() -> int:
    notified = asyncio.run(_check_expiring_licenses_async())
    logger.info("check_expiring_licenses", notified=notified)
    return notified
