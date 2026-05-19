"""Celery task that runs the catalog import inside a worker.

Reads the uploaded file out of MinIO and calls CatalogService.process_import.
We open our own DB session against the support pool — the worker has no
HTTP request context to seed RLS GUCs, and tenancy is taken from the job
row itself.
"""

from __future__ import annotations

import asyncio

import structlog

from app.core.db import SupportSessionLocal
from app.core.storage import get_object
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.tasks.celery_app import celery_app

logger = structlog.get_logger("tasks.catalog")


async def _run_import(job_id: str) -> dict[str, int]:
    from uuid import UUID

    async with SupportSessionLocal() as db:
        async with db.begin():
            repo = CatalogRepository(db)
            service = CatalogService(repo)
            job = await repo.get_job(UUID(job_id))
            if job is None or not job.source_path:
                logger.warning("import_job_missing", job_id=job_id)
                return {"created": 0, "errors": 0}
            raw = get_object(job.source_path)
            updated = await service.process_import(job_id=UUID(job_id), raw=raw)
            return {
                "valid": updated.valid_rows or 0,
                "errors": updated.error_rows or 0,
            }


@celery_app.task(name="catalog.import_catalog_job")  # type: ignore[misc]
def import_catalog_job(job_id: str) -> dict[str, int]:
    result = asyncio.run(_run_import(job_id))
    logger.info("import_catalog_job_done", job_id=job_id, **result)
    return result
