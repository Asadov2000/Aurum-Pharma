"""Celery task that runs the catalog import inside a worker.

Reads the uploaded file out of MinIO and calls CatalogService.process_import.
The worker uses the ordinary application role and seeds one explicit tenant
scope before reading the job, so a forged job ID cannot bypass RLS.
"""

from __future__ import annotations

import asyncio

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.catalog_worker_db import CatalogWorkerSessionLocal
from app.core.catalog_worker_storage import get_catalog_object
from app.core.time import utc_now
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.tasks.catalog_app import catalog_app

logger = structlog.get_logger("tasks.catalog")
IMPORT_SOFT_TIME_LIMIT_SECONDS = 120
IMPORT_HARD_TIME_LIMIT_SECONDS = 150


async def _seed_tenant(db: AsyncSession, tenant_id: str) -> None:
    await db.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": tenant_id},
    )


async def _run_import(job_id: str, tenant_id: str) -> dict[str, int]:
    from uuid import UUID

    async with CatalogWorkerSessionLocal() as db:
        async with db.begin():
            await _seed_tenant(db, tenant_id)
            repo = CatalogRepository(db)
            service = CatalogService(repo)
            job = await repo.get_job(UUID(job_id))
            if job is None or str(job.tenant_id) != tenant_id or not job.source_path:
                logger.warning("import_job_missing", job_id=job_id)
                return {"created": 0, "errors": 0}
            raw = await asyncio.to_thread(
                get_catalog_object,
                job.source_path,
                tenant_id=tenant_id,
            )
            updated = await service.process_import(job_id=UUID(job_id), raw=raw)
            return {
                "valid": updated.valid_rows or 0,
                "errors": updated.error_rows or 0,
            }


async def _mark_import_failed(job_id: str, tenant_id: str) -> None:
    from uuid import UUID

    async with CatalogWorkerSessionLocal() as db:
        async with db.begin():
            await _seed_tenant(db, tenant_id)
            repo = CatalogRepository(db)
            job = await repo.get_job_for_update(UUID(job_id))
            if job is None or job.status != "importing":
                return
            await repo.update_job(
                job,
                status="failed",
                errors=[{"row": 0, "messages": ["Импорт прерван. Повторите загрузку файла."]}],
                finished_at=utc_now(),
            )


@catalog_app.task(  # type: ignore[misc]
    name="catalog.import_catalog_job",
    soft_time_limit=IMPORT_SOFT_TIME_LIMIT_SECONDS,
    time_limit=IMPORT_HARD_TIME_LIMIT_SECONDS,
)
def import_catalog_job(job_id: str, tenant_id: str) -> dict[str, int]:
    try:
        result = asyncio.run(_run_import(job_id, tenant_id))
    except Exception as exc:
        logger.error(
            "import_catalog_job_failed",
            job_id=job_id,
            error_type=type(exc).__name__,
        )
        try:
            asyncio.run(_mark_import_failed(job_id, tenant_id))
        except Exception as mark_exc:
            logger.error(
                "import_catalog_job_failure_state_failed",
                job_id=job_id,
                error_type=type(mark_exc).__name__,
            )
        raise
    logger.info("import_catalog_job_done", job_id=job_id, **result)
    return result
