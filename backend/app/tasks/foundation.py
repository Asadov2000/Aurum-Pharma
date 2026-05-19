"""Celery tasks for the foundation domain.

`auto_start_trials` automatically moves long-standing 'setup' tenants into
'trial' so they don't sit unbilled forever. Phase 1 criterion is simply
"created more than 60 days ago" — the original spec also wants
`wizard_state.current_step >= 5`, but the wizard table is added in migration
0011. When that table lands we'll AND that condition in here.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import structlog
from sqlalchemy import select, update

from app.core.db import SupportSessionLocal
from app.core.time import utc_now
from app.domains.foundation.models import Tenant
from app.tasks.celery_app import celery_app

logger = structlog.get_logger("tasks.foundation")

SETUP_GRACE = timedelta(days=60)
TRIAL_DURATION = timedelta(days=14)


async def _auto_start_trials_async() -> int:
    now = utc_now()
    cutoff = now - SETUP_GRACE
    started = 0
    async with SupportSessionLocal() as db:
        async with db.begin():
            stmt = select(Tenant).where(
                Tenant.status == "setup",
                Tenant.created_at < cutoff,
            )
            result = await db.execute(stmt)
            for tenant in result.scalars().all():
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
    return started


@celery_app.task(name="foundation.auto_start_trials")  # type: ignore[misc]
def auto_start_trials() -> int:
    started = asyncio.run(_auto_start_trials_async())
    logger.info("auto_start_trials", started=started)
    return started
