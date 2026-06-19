"""Celery tasks for the foundation domain.

`auto_start_trials` automatically moves long-standing 'setup' tenants into
'trial' so they don't sit unbilled forever. A tenant is promoted when:
  - status='setup',
  - tenant.created_at < now() - 60 days,
  - onboarding_checklist.catalog_items_count >= 100.

Tenants that miss the catalog gate are skipped — eventually we will send
them a "please upload your catalog" reminder (TODO).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import structlog
from sqlalchemy import select, update

from app.core.db import WorkerSessionLocal
from app.core.time import utc_now
from app.domains.foundation.models import Tenant
from app.domains.onboarding.models import OnboardingChecklist
from app.tasks.celery_app import celery_app

logger = structlog.get_logger("tasks.foundation")

SETUP_GRACE = timedelta(days=60)
TRIAL_DURATION = timedelta(days=14)
TRIAL_MIN_CATALOG_ITEMS = 100


async def _auto_start_trials_async() -> dict[str, int]:
    now = utc_now()
    cutoff = now - SETUP_GRACE
    started = 0
    skipped = 0
    async with WorkerSessionLocal() as db:
        async with db.begin():
            stmt = select(Tenant).where(
                Tenant.status == "setup",
                Tenant.created_at < cutoff,
            )
            result = await db.execute(stmt)
            for tenant in result.scalars().all():
                checklist = await db.get(OnboardingChecklist, tenant.id)
                count = checklist.catalog_items_count if checklist is not None else 0
                if count < TRIAL_MIN_CATALOG_ITEMS:
                    skipped += 1
                    logger.info(
                        "trial_skipped_no_catalog",
                        tenant_id=str(tenant.id),
                        catalog_items_count=count,
                    )
                    continue
                await db.execute(
                    update(Tenant)
                    .where(Tenant.id == tenant.id)
                    .values(
                        status="trial",
                        trial_started_at=now,
                        trial_ends_at=now + TRIAL_DURATION,
                    )
                )
                started += 1
                logger.info("trial_started", tenant_id=str(tenant.id))
    return {"started": started, "skipped": skipped}


@celery_app.task(name="foundation.auto_start_trials")  # type: ignore[misc]
def auto_start_trials() -> dict[str, int]:
    result = asyncio.run(_auto_start_trials_async())
    logger.info("auto_start_trials", **result)
    return result
