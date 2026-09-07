"""Actual two-transaction admission races on a disposable PostgreSQL database."""

from __future__ import annotations

import asyncio
from typing import Literal
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import BusinessRuleError
from app.domains.catalog.models import CatalogImportJob
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService

type ImportFixture = tuple[async_sessionmaker[AsyncSession], UUID, UUID]


async def _admit(
    service: CatalogService, job_id: UUID, phase: Literal["previewing", "importing"]
) -> CatalogImportJob:
    if phase == "importing":
        return await service.confirm_import(job_id=job_id, duplicate_strategy="skip")
    return await service.request_preview(job_id=job_id)


@pytest.mark.parametrize("phase", ["previewing", "importing"])
@pytest.mark.parametrize("same_job", [False, True])
async def test_concurrent_admission_serializes_tenant_and_preserves_same_execution(
    committed_catalog_import: ImportFixture,
    phase: Literal["previewing", "importing"],
    same_job: bool,
) -> None:
    factory, tenant_id, first_id = committed_catalog_import
    async with factory.begin() as setup:
        service = CatalogService(CatalogRepository(setup))
        first = await service.get_job(first_id)
        second = await service.create_import_job(
            tenant_id=tenant_id,
            user_id=first.user_id,
            source_filename="second.csv",
            source_path=f"aurum/{tenant_id}/imports/second.csv",
        )
        second_id = second.id
    if phase == "importing":
        async with factory.begin() as setup:
            service = CatalogService(CatalogRepository(setup))
            await service.request_preview(job_id=first_id)
            await service.preview_import(job_id=first_id, raw=b"brand_name\nReady\n")

    contender_id = first_id if same_job else second_id
    contender_phase = phase if same_job else "previewing"
    contender_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def contend() -> tuple[str, UUID | None]:
        try:
            async with factory.begin() as session:
                pid = int((await session.execute(text("SELECT pg_backend_pid()"))).scalar_one())
                contender_pid.set_result(pid)
                job = await _admit(
                    CatalogService(CatalogRepository(session)), contender_id, contender_phase
                )
                return job.status, job.execution_id
        except BusinessRuleError as exc:
            assert exc.details == {"reason": "catalog_import_in_progress"}
            return "rejected", None

    async with factory.begin() as winner:
        repo = CatalogRepository(winner)
        await repo.lock_import_slot(tenant_id)
        winner_pid = int((await winner.execute(text("SELECT pg_backend_pid()"))).scalar_one())
        async with asyncio.TaskGroup() as tasks:
            contender = tasks.create_task(contend())
            async with asyncio.timeout(10):
                blocked_pid = await contender_pid
                while not await winner.scalar(
                    text("SELECT :winner = ANY(pg_blocking_pids(:contender))"),
                    {"winner": winner_pid, "contender": blocked_pid},
                ):
                    await asyncio.sleep(0)
            # The loser has reached the database lock, not merely been scheduled.
            admitted = await _admit(CatalogService(repo), first_id, phase)
            execution_id = admitted.execution_id
            await winner.commit()
        status, contender_execution = contender.result()

    if same_job:
        assert status == phase
        assert contender_execution == execution_id
    else:
        assert status == "rejected"
    async with factory.begin() as observer:
        active = list(
            (
                await observer.scalars(
                    select(CatalogImportJob).where(
                        CatalogImportJob.tenant_id == tenant_id,
                        CatalogImportJob.status.in_(("previewing", "importing")),
                    )
                )
            ).all()
        )
        assert len(active) == 1
        assert active[0].id == first_id
        assert active[0].execution_id == execution_id
