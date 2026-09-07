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
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.catalog_worker_config import get_catalog_worker_settings
from app.core.time import utc_now
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.tasks.catalog_app import catalog_app

type ImportFixture = tuple[async_sessionmaker[AsyncSession], UUID, UUID]

_WORKER_PROGRAM = """
import sys
import time
from sqlalchemy.engine import make_url
from app.core.catalog_worker_config import get_catalog_worker_settings
database = make_url(get_catalog_worker_settings().DATABASE_URL_APP).database or ''
if not database.endswith('_test'):
    raise RuntimeError('Hard-timeout worker requires a disposable test database')
from app.tasks import catalog as worker
def delayed_storage(*args, **kwargs):
    time.sleep(30)
    return b'brand_name\\nSynthetic timeout\\n'
worker.get_catalog_object = delayed_storage
worker.preview_catalog_job.time_limit = 1
worker.preview_catalog_job.soft_time_limit = None
worker.catalog_app.conf.update(worker_enable_remote_control=False, worker_send_task_events=False)
worker.catalog_app.worker_main([
    'worker', '--pool=prefork', '--concurrency=1', '--queues=' + sys.argv[1],
    '--hostname=' + sys.argv[1] + '@%h', '--without-gossip', '--without-mingle',
    '--without-heartbeat', '--loglevel=ERROR',
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
    database = make_url(get_catalog_worker_settings().DATABASE_URL_APP).database or ""
    if not database.endswith("_test"):
        raise RuntimeError("Hard-timeout integration requires a disposable '_test' database")
    async with factory.begin() as db:
        job = await CatalogService(CatalogRepository(db)).request_preview(job_id=job_id)
        execution_id = str(job.execution_id)
    queue = f"catalog-hard-timeout-{uuid4().hex}"
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
    # Keep captured output private: the assertion needs only this fixed marker.
    hard_timeout_logged = "Hard time limit (1s) exceeded" in output
    assert hard_timeout_logged, "Celery must report the enforced hard timeout"
