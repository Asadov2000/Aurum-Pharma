"""Actual prefork hard timeout must release the durable catalog execution."""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from kombu import Queue
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.catalog_worker_config import get_catalog_worker_settings
from app.core.time import utc_now
from app.domains.catalog.models import Barcode, TenantCatalog
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.tasks.catalog_app import catalog_app

type ImportFixture = tuple[async_sessionmaker[AsyncSession], UUID, UUID]

_WORKER_PROGRAM = """
import sys
import time
from kombu import Queue
from sqlalchemy.engine import make_url
from tests.fixture_helpers import resolve_test_redis_url
import os
from app.core.catalog_worker_config import get_catalog_worker_settings
database = make_url(get_catalog_worker_settings().DATABASE_URL_CATALOG_WORKER).database or ''
if not database.endswith('_test'):
    raise RuntimeError('Hard-timeout worker requires a disposable test database')
from app.tasks import catalog as worker
def delayed_storage(*args, **kwargs):
    time.sleep(30)
    return b'brand_name\\nSynthetic timeout\\n'
worker.get_catalog_object = delayed_storage
worker.preview_catalog_job.time_limit = 1
worker.preview_catalog_job.soft_time_limit = None
worker.catalog_app.conf.update(
    broker_url=resolve_test_redis_url(os.environ),
    task_queues=(Queue(sys.argv[1]),),
    worker_enable_remote_control=False,
    worker_send_task_events=False,
)
worker.catalog_app.worker_main([
    'worker', '--pool=prefork', '--concurrency=1', '--queues=' + sys.argv[1],
    '--hostname=' + sys.argv[1] + '@%h', '--without-gossip', '--without-mingle',
    '--without-heartbeat', '--loglevel=ERROR',
])
"""

_WRITE_CSV = b"brand_name,barcode\nTimeout first,900000000001\nTimeout second,900000000002\n"
_DB_WRITE_WORKER_PROGRAM = """
import os
import sys
from kombu import Queue
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from tests.fixture_helpers import resolve_test_redis_url
from app.core.catalog_worker_config import get_catalog_worker_settings
database = make_url(get_catalog_worker_settings().DATABASE_URL_CATALOG_WORKER).database or ''
if not database.endswith('_test'):
    raise RuntimeError('Write-timeout worker requires a disposable test database')
from app.tasks import catalog as worker
from app.domains.catalog.models import Barcode, TenantCatalog
from app.domains.catalog.service import CatalogService
raw = b'brand_name,barcode\\nTimeout first,900000000001\\nTimeout second,900000000002\\n'
worker.get_catalog_object = lambda *args, **kwargs: raw
original_import = CatalogService.process_import
async def interrupt_after_writes(self, *, job_id, raw):
    job = await original_import(self, job_id=job_id, raw=raw)
    if str(job_id) == sys.argv[2]:
        # Real catalog and barcode writes must exist inside this uncommitted
        # transaction before the operating-system signal interrupts PostgreSQL.
        for model in (TenantCatalog, Barcode):
            count = await self.repo.session.scalar(
                select(func.count()).select_from(model).where(model.tenant_id == job.tenant_id)
            )
            if count != 2:
                raise RuntimeError('Synthetic write precondition failed')
        print('CATALOG_TIMEOUT_REAL_WRITES_READY', flush=True)
        await self.repo.session.execute(text('SELECT pg_sleep(30)'))
    return job
CatalogService.process_import = interrupt_after_writes
worker.catalog_app.conf.update(
    broker_url=resolve_test_redis_url(os.environ),
    task_queues=(Queue(sys.argv[1]),),
    worker_enable_remote_control=False,
    worker_send_task_events=False,
)
worker.catalog_app.worker_main([
    'worker', '--pool=prefork', '--concurrency=1', '--queues=' + sys.argv[1],
    '--hostname=' + sys.argv[1] + '@%h', '--without-gossip', '--without-mingle',
    '--without-heartbeat', '--loglevel=WARNING',
])
"""


def _stop_worker(process: subprocess.Popen[str]) -> str:
    if process.poll() is None:
        process.terminate()
    try:
        output, _ = process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        # This session contains only the worker and its prefork children.
        os.killpg(process.pid, signal.SIGKILL)
        output, _ = process.communicate(timeout=5)
    return output


def _delete_queue(queue: str) -> None:
    with catalog_app.connection_for_write() as connection:
        connection.default_channel.queue_delete(queue)


async def _observe_hard_timeout(
    factory: async_sessionmaker[AsyncSession], job_id: UUID, process: subprocess.Popen[str]
) -> None:
    claimed = False
    async with asyncio.timeout(20):
        while True:
            assert process.poll() is None, "Main Celery worker exited unexpectedly"
            async with factory.begin() as db:
                repo = CatalogRepository(db)
                job = await repo.get_job(job_id)
                assert job is not None
                if not claimed and job.execution_started_at is not None:
                    claimed = True
                    # Do not wait the production abandonment interval in CI.
                    await repo.update_job(job, deadline_at=utc_now() + timedelta(seconds=2))
                if claimed and await repo.try_lock_execution(job_id):
                    recovered = await CatalogService(repo).get_job(job_id)
                    if recovered.status == "failed":
                        assert recovered.finished_at is not None
                        assert recovered.valid_rows in (None, 0)
                        assert recovered.preview_data in (None, [])
                        assert await repo.get_active_import(job.tenant_id) is None
                        next_job = await repo.create_job(
                            tenant_id=job.tenant_id,
                            user_id=job.user_id,
                            source_filename="next.csv",
                            source_path=job.source_path,
                            status="pending",
                        )
                        admitted = await CatalogService(repo).request_preview(job_id=next_job.id)
                        assert admitted.status == "previewing"
                        return
            await asyncio.sleep(0.05)


@pytest.mark.skipif(sys.platform != "linux", reason="Celery prefork hard limits require Linux")
async def test_real_celery_hard_timeout_releases_claim_and_import_slot(
    committed_catalog_import: ImportFixture,
) -> None:
    factory, tenant_id, job_id = committed_catalog_import
    database = make_url(get_catalog_worker_settings().DATABASE_URL_CATALOG_WORKER).database or ""
    if not database.endswith("_test"):
        raise RuntimeError("Hard-timeout integration requires a disposable '_test' database")
    async with factory.begin() as db:
        job = await CatalogService(CatalogRepository(db)).request_preview(job_id=job_id)
        execution_id = str(job.execution_id)
    queue = f"catalog-hard-timeout-{uuid4().hex}"
    # Dynamic queues are restricted to this disposable integration test.
    catalog_app.amqp.queues.add(Queue(queue))
    process = subprocess.Popen(
        [sys.executable, "-c", _WORKER_PROGRAM, queue],
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    output = ""
    try:
        await asyncio.to_thread(
            catalog_app.send_task,
            "catalog.preview_catalog_job",
            args=[str(job_id), str(tenant_id), execution_id],
            queue=queue,
            time_limit=1,
            soft_time_limit=None,
            retry=False,
        )
        await _observe_hard_timeout(factory, job_id, process)
        assert process.poll() is None, "Hard limit must kill the task child, not the main worker"
    finally:
        output = await asyncio.to_thread(_stop_worker, process)
        await asyncio.to_thread(_delete_queue, queue)
        catalog_app.amqp.queues.pop(queue, None)
    # Keep captured output private: the assertion needs only this fixed marker.
    hard_timeout_logged = "Hard time limit (1s) exceeded" in output
    assert hard_timeout_logged, "Celery must report the enforced hard timeout"


async def _observe_write_timeout(
    factory: async_sessionmaker[AsyncSession],
    tenant_id: UUID,
    job_id: UUID,
    process: subprocess.Popen[str],
) -> None:
    async with asyncio.timeout(20):
        while True:
            assert process.poll() is None, "Main Celery worker exited unexpectedly"
            async with factory.begin() as db:
                repo = CatalogRepository(db)
                job = await repo.get_job(job_id)
                assert job is not None
                if job.execution_started_at is not None and await repo.try_lock_execution(job_id):
                    # A hard-killed child cannot update the durable claim.
                    # Only after its session lock is released may the test
                    # shorten the abandonment wait and use normal recovery.
                    if job.status == "importing":
                        await repo.update_job(job, deadline_at=utc_now() - timedelta(seconds=1))
                    recovered = await CatalogService(repo).get_job(job_id)
                    assert recovered.status == "failed"
                    assert recovered.finished_at is not None
                    assert await repo.get_active_import(tenant_id) is None
                    for model in (TenantCatalog, Barcode):
                        assert (
                            await db.scalar(
                                select(func.count())
                                .select_from(model)
                                .where(model.tenant_id == tenant_id)
                            )
                            == 0
                        )
                    return
            await asyncio.sleep(0.05)


@pytest.mark.skipif(sys.platform != "linux", reason="Celery prefork time limits require Linux")
async def test_real_celery_timeout_rolls_back_database_writes_and_next_import_succeeds(
    committed_catalog_import: ImportFixture,
) -> None:
    factory, tenant_id, job_id = committed_catalog_import
    database = make_url(get_catalog_worker_settings().DATABASE_URL_CATALOG_WORKER).database or ""
    if not database.endswith("_test"):
        raise RuntimeError("Write-timeout integration requires a disposable '_test' database")
    async with factory.begin() as db:
        service = CatalogService(CatalogRepository(db))
        await service.request_preview(job_id=job_id)
        await service.preview_import(job_id=job_id, raw=_WRITE_CSV)
        job = await service.confirm_import(job_id=job_id, duplicate_strategy="skip")
        execution_id = str(job.execution_id)
    queue = f"catalog-write-timeout-{uuid4().hex}"
    catalog_app.amqp.queues.add(Queue(queue))
    process = subprocess.Popen(
        [sys.executable, "-c", _DB_WRITE_WORKER_PROGRAM, queue, str(job_id)],
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
    )
    try:
        await asyncio.to_thread(
            catalog_app.send_task,
            "catalog.import_catalog_job",
            args=[str(job_id), str(tenant_id), execution_id],
            queue=queue,
            soft_time_limit=2,
            time_limit=5,
            retry=False,
        )
        await _observe_write_timeout(factory, tenant_id, job_id, process)

        async with factory.begin() as db:
            repo = CatalogRepository(db)
            original = await repo.get_job(job_id)
            assert original is not None
            next_job = await repo.create_job(
                tenant_id=tenant_id,
                user_id=original.user_id,
                source_filename="after-timeout.csv",
                source_path=original.source_path,
                status="pending",
            )
            service = CatalogService(repo)
            await service.request_preview(job_id=next_job.id)
            await service.preview_import(job_id=next_job.id, raw=_WRITE_CSV)
            next_job = await service.confirm_import(job_id=next_job.id, duplicate_strategy="skip")
            next_id, next_execution = next_job.id, str(next_job.execution_id)
        await asyncio.to_thread(
            catalog_app.send_task,
            "catalog.import_catalog_job",
            args=[str(next_id), str(tenant_id), next_execution],
            queue=queue,
            soft_time_limit=2,
            time_limit=5,
            retry=False,
        )
        async with asyncio.timeout(20):
            while True:
                assert process.poll() is None, "Main Celery worker must accept the next import"
                async with factory.begin() as db:
                    repo = CatalogRepository(db)
                    completed = await repo.get_job(next_id)
                    assert completed is not None
                    assert completed.status != "failed"
                    if completed.status == "success":
                        assert completed.valid_rows == 2
                        assert completed.error_rows == 0
                        assert await repo.get_active_import(tenant_id) is None
                        for model in (TenantCatalog, Barcode):
                            assert (
                                await db.scalar(
                                    select(func.count())
                                    .select_from(model)
                                    .where(model.tenant_id == tenant_id)
                                )
                                == 2
                            )
                        break
                await asyncio.sleep(0.05)
    finally:
        output = await asyncio.to_thread(_stop_worker, process)
        await asyncio.to_thread(_delete_queue, queue)
        catalog_app.amqp.queues.pop(queue, None)
    # Never include raw captured driver/worker output in assertion messages.
    assert "CATALOG_TIMEOUT_REAL_WRITES_READY" in output, "Real writes must precede interruption"
    timeout_logged = (
        "Soft time limit (2s) exceeded" in output or "Hard time limit (5s) exceeded" in output
    )
    assert timeout_logged, "Celery must enforce the test task's operating-system time limit"
