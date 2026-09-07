"""Real transaction and process-loss checks for bounded catalog execution."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from datetime import timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy import func, select, text

from app.core.catalog_worker_storage import get_catalog_object
from app.core.time import utc_now
from app.domains.catalog.import_limits import catalog_execution_lock_key
from app.domains.catalog.models import CatalogImportJob, TenantCatalog
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.tasks import catalog as worker

_CSV = b"brand_name\nWorker first\nWorker second\n"


async def _prepare(factory, job_id, *, importing=False):
    async with factory.begin() as db:
        service = CatalogService(CatalogRepository(db))
        job = await service.request_preview(job_id=job_id)
        if importing:
            await service.preview_import(job_id=job_id, raw=_CSV)
            job = await service.confirm_import(job_id=job_id, duplicate_strategy="skip")
        return str(job.execution_id)


async def test_worker_preview_is_durable_and_duplicate_delivery_does_not_read(
    committed_catalog_import, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, tenant_id, job_id = committed_catalog_import
    execution_id = await _prepare(factory, job_id)
    storage = Mock(return_value=_CSV)
    monkeypatch.setattr(worker, "get_catalog_object", storage)
    result = await worker._execute_job(
        str(job_id), str(tenant_id), execution_id, phase="previewing"
    )
    assert result == {"valid": 2, "errors": 0}
    async with factory() as db:
        job = await db.get(CatalogImportJob, job_id)
        assert job.status == "validating"
        assert job.execution_started_at is not None
    await worker._execute_job(str(job_id), str(tenant_id), execution_id, phase="previewing")
    storage.assert_called_once()


async def test_worker_rejects_foreign_tenant_and_stale_execution_before_storage(
    committed_catalog_import, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, tenant_id, job_id = committed_catalog_import
    execution_id = await _prepare(factory, job_id)
    storage = Mock(side_effect=AssertionError("Storage must not be accessed"))
    monkeypatch.setattr(worker, "get_catalog_object", storage)
    await worker._execute_job(str(job_id), str(uuid4()), execution_id, phase="previewing")
    await worker._execute_job(str(job_id), str(tenant_id), str(uuid4()), phase="previewing")
    storage.assert_not_called()
    async with factory() as db:
        assert (await db.get(CatalogImportJob, job_id)).execution_started_at is None


async def test_live_execution_rejects_duplicate_without_waiting_or_reading(
    committed_catalog_import, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, tenant_id, job_id = committed_catalog_import
    execution_id = await _prepare(factory, job_id)
    storage = Mock(side_effect=AssertionError("Duplicate must not read"))
    monkeypatch.setattr(worker, "get_catalog_object", storage)
    async with factory.begin() as claimed:
        job = await claimed.get(CatalogImportJob, job_id)
        await CatalogRepository(claimed).update_job(job, execution_started_at=utc_now())
    async with factory.begin() as db:
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": catalog_execution_lock_key(job_id)},
        )
        await asyncio.wait_for(
            worker._execute_job(str(job_id), str(tenant_id), execution_id, phase="previewing"),
            timeout=2,
        )
    storage.assert_not_called()


async def test_new_phase_retries_when_previous_phase_has_not_released_lock(
    committed_catalog_import, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, tenant_id, job_id = committed_catalog_import
    execution_id = await _prepare(factory, job_id, importing=True)
    storage = Mock(return_value=_CSV)
    monkeypatch.setattr(worker, "get_catalog_object", storage)
    async with factory.begin() as previous_phase:
        await previous_phase.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": catalog_execution_lock_key(job_id)},
        )
        with pytest.raises(worker.CatalogExecutionBusy):
            await worker._execute_job(str(job_id), str(tenant_id), execution_id, phase="importing")
        await worker._execute_job(str(job_id), str(tenant_id), str(uuid4()), phase="previewing")
        storage.assert_not_called()
    result = await worker._execute_job(str(job_id), str(tenant_id), execution_id, phase="importing")
    assert result == {"valid": 2, "errors": 0}
    storage.assert_called_once()


async def test_soft_timeout_rolls_back_every_imported_row_and_releases_slot(
    committed_catalog_import, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, tenant_id, job_id = committed_catalog_import
    execution_id = await _prepare(factory, job_id, importing=True)
    monkeypatch.setattr(worker, "get_catalog_object", Mock(return_value=_CSV))
    original = CatalogService._apply_import_row
    calls = 0

    async def interrupt_second(self, job, row, *, strategy):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise SoftTimeLimitExceeded()
        return await original(self, job, row, strategy=strategy)

    monkeypatch.setattr(CatalogService, "_apply_import_row", interrupt_second)
    with pytest.raises(SoftTimeLimitExceeded):
        await worker._execute_job(str(job_id), str(tenant_id), execution_id, phase="importing")
    async with factory() as db:
        assert (await db.get(CatalogImportJob, job_id)).status == "failed"
        assert (
            await db.scalar(
                select(func.count())
                .select_from(TenantCatalog)
                .where(TenantCatalog.tenant_id == tenant_id)
            )
            == 0
        )
    assert calls == 2


async def test_lost_process_claim_prevents_poison_file_redelivery(
    committed_catalog_import, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, tenant_id, job_id = committed_catalog_import
    execution_id = await _prepare(factory, job_id)
    # Exit the actual child after the committed claim, without finally/on_failure.
    # This reproduces the application state left by hard timeout, SIGKILL or OOM.
    program = (
        "import asyncio, os, sys\n"
        "from app.tasks import catalog as worker\n"
        "def lost(*args, **kwargs): os._exit(77)\n"
        "worker.get_catalog_object = lost\n"
        "asyncio.run(worker._execute_job(*sys.argv[1:], phase='previewing'))\n"
    )
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", program, str(job_id), str(tenant_id), execution_id],
        env=os.environ.copy(),
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 77, "Child must stop only after the durable claim"
    async with factory() as db:
        job = await db.get(CatalogImportJob, job_id)
        assert job.status == "previewing"
        assert job.execution_started_at is not None
    storage = Mock(side_effect=AssertionError("Poison file must not be retried"))
    monkeypatch.setattr(worker, "get_catalog_object", storage)
    await worker._execute_job(str(job_id), str(tenant_id), execution_id, phase="previewing")
    storage.assert_not_called()
    async with factory.begin() as db:
        assert (await db.get(CatalogImportJob, job_id)).status == "failed"
        repo = CatalogRepository(db)
        previous = await repo.get_job(job_id)
        next_job = await repo.create_job(
            tenant_id=tenant_id,
            user_id=previous.user_id,
            source_filename="new.csv",
            source_path=previous.source_path,
            status="pending",
        )
        assert (
            await CatalogService(repo).request_preview(job_id=next_job.id)
        ).status == "previewing"


async def test_deadline_after_parsing_cannot_commit_success(
    committed_catalog_import, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory, tenant_id, job_id = committed_catalog_import
    execution_id = await _prepare(factory, job_id, importing=True)
    monkeypatch.setattr(worker, "get_catalog_object", Mock(return_value=_CSV))
    original = CatalogService.process_import

    async def finish_late(self, *, job_id, raw):
        updated = await original(self, job_id=job_id, raw=raw)
        future = utc_now() + timedelta(hours=1)
        monkeypatch.setattr(worker, "utc_now", lambda: future)
        return updated

    monkeypatch.setattr(CatalogService, "process_import", finish_late)
    with pytest.raises(TimeoutError):
        await worker._execute_job(str(job_id), str(tenant_id), execution_id, phase="importing")
    async with factory() as db:
        assert (await db.get(CatalogImportJob, job_id)).status == "failed"
        assert (
            await db.scalar(
                select(func.count())
                .select_from(TenantCatalog)
                .where(TenantCatalog.tenant_id == tenant_id)
            )
            == 0
        )


def test_storage_limit_stops_at_one_excess_byte_and_releases_connection(monkeypatch) -> None:
    from app.core import catalog_worker_storage as storage

    response = Mock()
    response.read.return_value = b"1234"
    client = Mock()
    client.get_object.return_value = response
    monkeypatch.setattr(storage, "MAX_IMPORT_FILE_BYTES", 3)
    monkeypatch.setattr(storage, "get_catalog_minio", lambda: client)
    settings = storage.get_catalog_worker_settings()
    tenant_id = str(uuid4())
    with pytest.raises(ValueError, match="size limit"):
        get_catalog_object(
            f"{settings.MINIO_BUCKET}/{tenant_id}/imports/file.csv", tenant_id=tenant_id
        )
    response.read.assert_called_once_with(4)
    response.close.assert_called_once()
    response.release_conn.assert_called_once()


def test_task_boundary_does_not_expose_parser_error(monkeypatch) -> None:
    async def fail(*args, **kwargs):
        raise ValueError("sensitive synthetic input")

    monkeypatch.setattr(worker, "_execute_job", fail)
    with pytest.raises(RuntimeError, match="^Catalog file processing failed$") as raised:
        worker.preview_catalog_job("job", "tenant", "execution")
    assert raised.value.__suppress_context__ is True
