"""Celery tasks for the foundation domain.

`auto_start_trials` reuses the same locked readiness transition as the owner
button. Expired setup tenants that are not fully ready are skipped without
partially changing billing or tenant state.
"""

from __future__ import annotations

import asyncio
from uuid import NAMESPACE_URL, UUID, uuid5

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import WorkerSessionLocal
from app.core.errors import BusinessRuleError, ConflictError, NotFoundError
from app.core.time import utc_now
from app.domains.foundation.models import Tenant
from app.domains.onboarding.models import OnboardingChecklist
from app.domains.onboarding.repository import OnboardingRepository
from app.domains.onboarding.service import OnboardingService
from app.tasks.celery_app import celery_app

logger = structlog.get_logger("tasks.foundation")


async def _auto_start_trials_async(
    *,
    session_factory: async_sessionmaker[AsyncSession] = WorkerSessionLocal,
    candidate_tenant_ids: frozenset[UUID] | None = None,
) -> dict[str, int]:
    now = utc_now()
    started = 0
    skipped = 0
    async with session_factory() as db:
        async with db.begin():
            candidates = (
                select(Tenant.id)
                .join(
                    OnboardingChecklist,
                    OnboardingChecklist.tenant_id == Tenant.id,
                )
                .where(
                    Tenant.status == "setup",
                    OnboardingChecklist.setup_ends_at <= now,
                )
                .order_by(OnboardingChecklist.setup_ends_at, Tenant.id)
                .limit(100)
            )
            if candidate_tenant_ids is not None:
                if not candidate_tenant_ids:
                    return {"started": 0, "skipped": 0}
                candidates = candidates.where(Tenant.id.in_(candidate_tenant_ids))
            candidate_ids = list((await db.execute(candidates)).scalars())

    for tenant_id in candidate_ids:
        try:
            async with session_factory() as db:
                async with db.begin():
                    service = OnboardingService(OnboardingRepository(db))
                    await service.start_trial(
                        tenant_id=tenant_id,
                        source="automatic",
                        operation_id=uuid5(
                            NAMESPACE_URL,
                            f"aurum-pharma:automatic-trial:{tenant_id}",
                        ),
                    )
            started += 1
            logger.info("trial_started_automatically", tenant_id=str(tenant_id))
        except (BusinessRuleError, ConflictError, NotFoundError) as exc:
            skipped += 1
            logger.info(
                "trial_auto_start_skipped",
                tenant_id=str(tenant_id),
                reason=exc.code,
            )
    return {"started": started, "skipped": skipped}


@celery_app.task(name="foundation.auto_start_trials")  # type: ignore[misc]
def auto_start_trials() -> dict[str, int]:
    result = asyncio.run(_auto_start_trials_async())
    logger.info("auto_start_trials", **result)
    return result
