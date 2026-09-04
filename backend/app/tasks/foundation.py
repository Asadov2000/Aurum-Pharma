"""Celery tasks for the foundation domain.

`auto_start_trials` reuses the same locked readiness transition as the owner
button. Expired setup tenants that are not fully ready are skipped without
partially changing billing or tenant state.
"""

from __future__ import annotations

import asyncio
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.system_worker_db import SystemWorkerSessionLocal
from app.tasks.celery_app import celery_app

logger = structlog.get_logger("tasks.foundation")


async def _auto_start_trials_async(
    *,
    session_factory: async_sessionmaker[AsyncSession] = SystemWorkerSessionLocal,
    candidate_tenant_ids: frozenset[UUID] | None = None,
) -> dict[str, int]:
    async with session_factory() as db:
        async with db.begin():
            result = await db.execute(
                text(
                    "SELECT tenant_id "
                    "FROM public.worker_list_automatic_trial_candidates("
                    ":limit, :tenant_ids)"
                ),
                {
                    "limit": 100,
                    "tenant_ids": (
                        list(candidate_tenant_ids) if candidate_tenant_ids is not None else None
                    ),
                },
            )
            tenant_ids = [UUID(str(row["tenant_id"])) for row in result.mappings()]

    started = 0
    skipped = 0
    for tenant_id in tenant_ids:
        try:
            async with session_factory() as db:
                async with db.begin():
                    result = await db.execute(
                        text(
                            "SELECT started, reason "
                            "FROM public.worker_start_automatic_trial(:tenant_id)"
                        ),
                        {"tenant_id": tenant_id},
                    )
                    outcome = result.mappings().one()
        except Exception:
            skipped += 1
            logger.exception("auto_start_trial_failed", tenant_id=str(tenant_id))
            continue

        if bool(outcome["started"]):
            started += 1
        else:
            skipped += 1
            logger.info(
                "auto_start_trial_skipped",
                tenant_id=str(tenant_id),
                reason=str(outcome["reason"]),
            )
    return {"started": started, "skipped": skipped}


@celery_app.task(name="foundation.auto_start_trials")  # type: ignore[misc]
def auto_start_trials() -> dict[str, int]:
    result = asyncio.run(_auto_start_trials_async())
    logger.info("auto_start_trials", **result)
    return result
