"""Bounded, tenant-scoped execution of catalog preview and import.

A session advisory lock spans the committed claim and the work transaction.
The durable claim prevents a lost worker's message from parsing a poison file
again. The work transaction either finishes within the deadline or rolls back.
"""

from __future__ import annotations

import asyncio
from typing import Literal
from uuid import UUID

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.catalog_worker_db import catalog_worker_engine
from app.core.catalog_worker_storage import get_catalog_object
from app.core.time import utc_now
from app.domains.catalog.import_limits import (
    IMPORT_HARD_TIME_LIMIT_SECONDS,
    IMPORT_SOFT_TIME_LIMIT_SECONDS,
    catalog_execution_lock_key,
)
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.tasks.catalog_app import catalog_app

logger = structlog.get_logger("tasks.catalog")
Phase = Literal["previewing", "importing"]
_EMPTY_RESULT = {"valid": 0, "errors": 0}
_FAILURE_MESSAGE = "Обработка файла прервана. Загрузите файл заново и повторите попытку."


class CatalogExecutionBusy(RuntimeError):
    """A new phase arrived before the previous phase released its session lock."""


async def _seed_tenant(db: AsyncSession, tenant_id: str) -> None:
    await db.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": tenant_id},
    )


async def _fail_execution(db: AsyncSession, job_id: UUID) -> None:
    repo = CatalogRepository(db)
    job = await repo.get_job_for_update(job_id)
    if job is not None:
        await repo.update_job(
            job,
            status="failed",
            errors=[{"row": 0, "messages": [_FAILURE_MESSAGE]}],
            finished_at=utc_now(),
        )


async def _execute_job(
    job_id: str, tenant_id: str, execution_id: str, *, phase: Phase
) -> dict[str, int]:
    job_uuid = UUID(job_id)
    execution_uuid = UUID(execution_id)
    # Keep the same physical connection over both transactions. NullPool closes
    # it on exit, releasing the session lock even if exception cleanup fails.
    async with catalog_worker_engine.connect() as connection:
        acquired = await connection.scalar(
            text("SELECT pg_try_advisory_lock(hashtextextended(:key, 0))"),
            {"key": catalog_execution_lock_key(job_uuid)},
        )
        await connection.commit()
        if not acquired:
            async with AsyncSession(bind=connection) as observer, observer.begin():
                await _seed_tenant(observer, tenant_id)
                waiting = await CatalogRepository(observer).get_job(job_uuid)
                if (
                    waiting is not None
                    and waiting.status == phase
                    and waiting.execution_id == execution_uuid
                    and waiting.execution_started_at is None
                    and waiting.deadline_at is not None
                    and waiting.deadline_at > utc_now()
                ):
                    raise CatalogExecutionBusy("Catalog execution lock is busy")
            return dict(_EMPTY_RESULT)
        try:
            async with AsyncSession(bind=connection, expire_on_commit=False) as db:
                async with db.begin():
                    await _seed_tenant(db, tenant_id)
                    repo = CatalogRepository(db)
                    job = await repo.get_job_for_update(job_uuid)
                    if (
                        job is None
                        or str(job.tenant_id) != tenant_id
                        or job.status != phase
                        or job.execution_id != execution_uuid
                    ):
                        return dict(_EMPTY_RESULT)
                    if (
                        job.execution_started_at is not None
                        or job.deadline_at is None
                        or job.deadline_at <= utc_now()
                        or not job.source_path
                    ):
                        await _fail_execution(db, job_uuid)
                        return dict(_EMPTY_RESULT)
                    source_path = job.source_path
                    deadline = job.deadline_at
                    await repo.update_job(job, execution_started_at=utc_now())

                # Claim is durable before reading or parsing untrusted input.
                try:
                    async with db.begin():
                        await _seed_tenant(db, tenant_id)
                        raw = await asyncio.to_thread(
                            get_catalog_object, source_path, tenant_id=tenant_id
                        )
                        service = CatalogService(CatalogRepository(db))
                        if phase == "previewing":
                            updated = await service.preview_import(job_id=job_uuid, raw=raw)
                        else:
                            updated = await service.process_import(job_id=job_uuid, raw=raw)
                        if utc_now() >= deadline:
                            raise TimeoutError("Catalog execution deadline exceeded")
                        result = {
                            "valid": updated.valid_rows or 0,
                            "errors": updated.error_rows or 0,
                        }
                    return result
                except Exception as exc:
                    # Never log exception messages/tracebacks containing file
                    # contents or database parameters.
                    logger.error(
                        "catalog_execution_failed", job_id=job_id, error_type=type(exc).__name__
                    )
                    async with db.begin():
                        await _seed_tenant(db, tenant_id)
                        await _fail_execution(db, job_uuid)
                    raise
        finally:
            await connection.execute(
                text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                {"key": catalog_execution_lock_key(job_uuid)},
            )
            await connection.commit()


# Celery does not expose typed task decorators.
@catalog_app.task(  # type: ignore[misc]
    name="catalog.preview_catalog_job",
    soft_time_limit=IMPORT_SOFT_TIME_LIMIT_SECONDS,
    time_limit=IMPORT_HARD_TIME_LIMIT_SECONDS,
    autoretry_for=(CatalogExecutionBusy,),
    retry_kwargs={"max_retries": 5, "countdown": 1},
)
def preview_catalog_job(job_id: str, tenant_id: str, execution_id: str) -> dict[str, int]:
    return _run_phase(job_id, tenant_id, execution_id, phase="previewing")


# Celery does not expose typed task decorators.
@catalog_app.task(  # type: ignore[misc]
    name="catalog.import_catalog_job",
    soft_time_limit=IMPORT_SOFT_TIME_LIMIT_SECONDS,
    time_limit=IMPORT_HARD_TIME_LIMIT_SECONDS,
    autoretry_for=(CatalogExecutionBusy,),
    retry_kwargs={"max_retries": 5, "countdown": 1},
)
def import_catalog_job(
    job_id: str, tenant_id: str, execution_id: str | None = None
) -> dict[str, int]:
    if execution_id is None:
        # Pre-upgrade messages cannot claim a new execution. Their old job
        # expires through the tenant-scoped recovery path for lost dispatch.
        return dict(_EMPTY_RESULT)
    return _run_phase(job_id, tenant_id, execution_id, phase="importing")


def _run_phase(job_id: str, tenant_id: str, execution_id: str, *, phase: Phase) -> dict[str, int]:
    try:
        return asyncio.run(_execute_job(job_id, tenant_id, execution_id, phase=phase))
    except CatalogExecutionBusy:
        raise
    except Exception as exc:
        logger.error("catalog_task_failed", job_id=job_id, error_type=type(exc).__name__)
        # Celery logs uncaught task exceptions itself. Only a fixed message may
        # escape this boundary; parser/driver details can contain uploaded data.
        raise RuntimeError("Catalog file processing failed") from None
