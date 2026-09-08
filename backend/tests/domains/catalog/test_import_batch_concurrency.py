"""Bulk import preserves a concurrent edit of a field omitted from its file."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import Mock
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domains.catalog.import_batch import IMPORT_BATCH_MIN_ROWS
from app.domains.catalog.models import CatalogImportJob, TenantCatalog
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.tasks import catalog as worker

type ImportFixture = tuple[async_sessionmaker[AsyncSession], UUID, UUID]


async def test_batch_waits_for_manual_edit_and_preserves_omitted_field(
    committed_catalog_import: ImportFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, tenant_id, job_id = committed_catalog_import
    count = IMPORT_BATCH_MIN_ROWS
    raw = (
        "brand_name,base_price\n"
        + "".join(f"Concurrent batch {index:03d},19.75\n" for index in range(count))
    ).encode()
    async with factory.begin() as setup:
        service = CatalogService(CatalogRepository(setup))
        target = await service.create_item(
            tenant_id=tenant_id,
            fields={
                "brand_name": "Concurrent batch 000",
                "inn": "Before manual edit",
                "base_price": Decimal("12.50"),
            },
        )
        target_id = target.id
        await service.request_preview(job_id=job_id)
        await service.preview_import(job_id=job_id, raw=raw)
        job = await service.confirm_import(job_id=job_id, duplicate_strategy="update")
        execution_id = str(job.execution_id)
    monkeypatch.setattr(worker, "get_catalog_object", Mock(return_value=raw))

    # The observer uses the same limited login as the worker, so pg_stat_activity
    # exposes that role's real query/wait state without granting monitoring rights.
    # Observer + worker consume exactly the role's two permitted connections.
    async with factory.begin() as manual, worker.catalog_worker_engine.connect() as observer:
        manual_pid = await manual.scalar(text("SELECT pg_backend_pid()"))
        await manual.execute(
            select(TenantCatalog.id).where(TenantCatalog.id == target_id).with_for_update()
        )
        assert await observer.scalar(text("SELECT current_user")) == "aurum_catalog_worker"
        await observer.commit()
        async with asyncio.TaskGroup() as tasks:
            importing = tasks.create_task(
                worker._execute_job(str(job_id), str(tenant_id), execution_id, phase="importing")
            )
            async with asyncio.timeout(4):
                while True:
                    waiting = await observer.scalar(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                            "WHERE usename = current_user AND pid <> pg_backend_pid() "
                            "AND wait_event_type = 'Lock' "
                            "AND :manual_pid = ANY(pg_blocking_pids(pid)) "
                            "AND position('FOR UPDATE OF target' in query) > 0)"
                        ),
                        {"manual_pid": manual_pid},
                    )
                    # A fresh transaction avoids reusing a statistics snapshot.
                    await observer.commit()
                    if waiting:
                        break
                    await asyncio.sleep(0)
            assert not importing.done()
            await manual.execute(
                text("UPDATE tenant_catalog SET inn = :inn WHERE id = :id"),
                {"inn": "Concurrent manual edit", "id": target_id},
            )
            await manual.commit()
        assert importing.result() == {"valid": count, "errors": 0}

    async with factory() as check:
        saved = (
            await check.execute(
                select(TenantCatalog.id, TenantCatalog.inn, TenantCatalog.base_price).where(
                    TenantCatalog.tenant_id == tenant_id
                )
            )
        ).all()
        assert len(saved) == count
        assert {row.base_price for row in saved} == {Decimal("19.75")}
        edited = next(row for row in saved if row.id == target_id)
        assert edited.inn == "Concurrent manual edit"
        completed = await check.get(CatalogImportJob, job_id)
        assert completed is not None and completed.status == "success"
        assert completed.error_rows == 0
