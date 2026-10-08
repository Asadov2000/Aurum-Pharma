"""Upload storage follows real request commits and tenant database guards."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import urlparse
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from redis.asyncio import Redis
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session as SyncSession
from sqlalchemy.pool import NullPool

from app.core import deps
from app.core.config import get_settings
from app.core.security import create_access_token, hash_token
from app.core.storage import default_object_path
from app.core.time import utc_now
from app.domains.auth.models import Session
from app.domains.catalog import router as catalog_router
from app.domains.catalog.models import CatalogImportJob
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.main import app
from tests.role_version_helpers import provision_test_owner

UPLOAD = "/api/v1/catalog/import/upload"
CSV = b"brand_name\nUpload fixture\n"
JobState = tuple[UUID, UUID, str | None, str]


def _require_isolated_services() -> None:
    settings = get_settings()
    allowed = {
        ("postgres", "aurum_verify_test"),
        ("postgres-test", "aurum_test"),
        ("localhost", "aurum_test"),
        ("127.0.0.1", "aurum_test"),
    }
    for url in (settings.DATABASE_URL_APP, settings.DATABASE_URL_SUPPORT):
        parsed = urlparse(url)
        if (parsed.hostname, parsed.path.removeprefix("/")) not in allowed:
            raise RuntimeError("Upload commit tests require owned disposable PostgreSQL")
    redis_url = os.environ.get("TEST_REDIS_URL")
    if redis_url is None:
        raise RuntimeError("Upload commit tests require explicit TEST_REDIS_URL")
    parsed_redis = urlparse(redis_url)
    hosts = (
        {"redis"}
        if urlparse(settings.DATABASE_URL_APP).path == "/aurum_verify_test"
        else {"redis-test", "localhost", "127.0.0.1"}
    )
    if (
        parsed_redis.hostname not in hosts
        or parsed_redis.path != "/0"
        or redis_url != settings.REDIS_URL
    ):
        raise RuntimeError("Upload commit tests require the matching dedicated test Redis")


# Protect shared services before the Redis fixture performs its test cleanup.
_require_isolated_services()


@dataclass
class UploadFixture:
    tenant_id: UUID
    foreign_tenant_id: UUID
    user_id: UUID
    token: str = field(repr=False)
    engine: AsyncEngine
    old_jobs: set[JobState]
    objects: dict[str, bytes]
    puts: list[str] = field(default_factory=list)
    removes: list[str] = field(default_factory=list)
    enqueues: list[UUID] = field(default_factory=list)

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}


@pytest_asyncio.fixture
async def upload_fixture(
    db_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[UploadFixture]:
    app_engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    monkeypatch.setattr(deps, "AppSessionLocal", async_sessionmaker(app_engine))
    monkeypatch.setattr(deps, "SupportSessionLocal", async_sessionmaker(db_engine))
    assert deps.get_db not in app.dependency_overrides
    subjects: list[tuple[UUID, UUID, UUID]] = []
    old_jobs: set[JobState] = set()
    objects: dict[str, bytes] = {}
    try:
        async with async_sessionmaker(db_engine, expire_on_commit=False)() as db:
            async with db.begin():
                for index in range(2):
                    await db.execute(
                        text(
                            "SELECT set_config('app.support_session','true',true), "
                            "set_config('app.tenant_id','',true)"
                        )
                    )
                    tenant = await FoundationService(FoundationRepository(db)).create_tenant(
                        payload={
                            "name": f"Upload fixture {uuid4().hex}",
                            "contact_email": f"upload-{uuid4().hex}@example.test",
                        }
                    )
                    user, _, _, _ = await provision_test_owner(
                        db,
                        tenant_id=tenant.id,
                        email=f"upload-owner-{uuid4().hex}@example.test",
                        full_name="Upload fixture owner",
                    )
                    auth_session = Session(
                        user_id=user.id,
                        refresh_token_hash=hash_token(f"upload-session-{uuid4()}"),
                        expires_at=utc_now() + timedelta(hours=1),
                    )
                    db.add(auth_session)
                    await db.flush()
                    subjects.append((tenant.id, user.id, auth_session.id))
                    path = default_object_path(f"{tenant.id}/imports/old-{uuid4().hex}.csv")
                    old = await CatalogRepository(db).create_job(
                        tenant_id=tenant.id,
                        user_id=user.id,
                        source_filename="old.csv",
                        source_path=path,
                        status="failed",
                    )
                    old_jobs.add((old.id, old.tenant_id, old.source_path, old.status))
                    objects[path] = f"old-{index}".encode()
        tenant_id, user_id, session_id = subjects[0]
        seeded = UploadFixture(
            tenant_id=tenant_id,
            foreign_tenant_id=subjects[1][0],
            user_id=user_id,
            token=create_access_token(
                user_id,
                tenant_id=tenant_id,
                session_id=session_id,
                is_developer=False,
                is_administrator=False,
            ),
            engine=app_engine,
            old_jobs=old_jobs,
            objects=objects,
        )

        def put(*, object_name: str, data: bytes, content_type: str) -> str:
            assert content_type == "text/csv"
            path = default_object_path(object_name)
            assert path not in seeded.objects
            seeded.puts.append(path)
            seeded.objects[path] = data
            return path

        def remove(path: str) -> None:
            assert path in seeded.puts
            seeded.removes.append(path)
            seeded.objects.pop(path)

        def enqueue(job_id: UUID, _tenant_id: UUID, _execution_id: UUID) -> None:
            seeded.enqueues.append(job_id)

        monkeypatch.setattr(catalog_router, "put_object", put)
        monkeypatch.setattr(catalog_router, "remove_object", remove)
        monkeypatch.setattr(catalog_router, "_enqueue_catalog_preview", enqueue)
        yield seeded
    finally:
        await app_engine.dispose()


async def _jobs(seeded: UploadFixture, tenant_id: UUID) -> set[JobState]:
    # Fresh connection and runtime role prove actual commits and tenant RLS.
    async with async_sessionmaker(seeded.engine)() as db:
        async with db.begin():
            assert await db.scalar(text("SELECT session_user")) == "aurum_app"
            await db.execute(
                text(
                    "SELECT set_config('app.tenant_id',:tenant,true), "
                    "set_config('app.user_id',:actor,true)"
                ),
                {"tenant": str(tenant_id), "actor": str(seeded.user_id)},
            )
            rows = await db.execute(
                select(
                    CatalogImportJob.id,
                    CatalogImportJob.tenant_id,
                    CatalogImportJob.source_path,
                    CatalogImportJob.status,
                )
            )
            result = set(rows.tuples().all())
            assert all(row[1] == tenant_id for row in result)
            return result


async def _assert_preserved(seeded: UploadFixture) -> None:
    own = await _jobs(seeded, seeded.tenant_id)
    foreign = await _jobs(seeded, seeded.foreign_tenant_id)
    assert seeded.old_jobs <= own | foreign
    assert foreign == {row for row in seeded.old_jobs if row[1] == seeded.foreign_tenant_id}
    for index, tenant_id in enumerate((seeded.tenant_id, seeded.foreign_tenant_id)):
        path = next(row[2] for row in seeded.old_jobs if row[1] == tenant_id)
        assert path is not None
        assert seeded.objects[path] == f"old-{index}".encode()
    assert seeded.enqueues == []


async def _upload(seeded: UploadFixture) -> int:
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            UPLOAD, headers=seeded.headers, files={"file": ("import.csv", CSV, "text/csv")}
        )
        return response.status_code


@pytest.mark.parametrize("restriction", ["readonly", "catalog"])
async def test_access_guard_runs_before_storage_put(
    upload_fixture: UploadFixture,
    maintenance_engine: AsyncEngine,
    redis: Redis,
    restriction: str,
) -> None:
    seeded = upload_fixture
    # This engine is the owner of the disposable test database, never dev.
    async with maintenance_engine.begin() as owner:
        assert str(await owner.scalar(text("SELECT current_database()"))).endswith("_test")
        await owner.execute(
            text("ALTER TABLE tenant_access_policy DISABLE TRIGGER a00_guard_tenant_access_policy")
        )
        await owner.execute(
            text(
                "UPDATE tenant_access_policy SET version=version+1, mode=:mode, "
                "blocked_modules=:modules WHERE tenant_id=:tenant"
            ),
            {
                "tenant": seeded.tenant_id,
                "mode": "readonly" if restriction == "readonly" else "full",
                "modules": ["catalog"] if restriction == "catalog" else [],
            },
        )
        await owner.execute(
            text("ALTER TABLE tenant_access_policy ENABLE TRIGGER a00_guard_tenant_access_policy")
        )
    for _ in range(2):
        assert await _upload(seeded) == 403
    assert seeded.puts == seeded.removes == []
    assert len(seeded.objects) == 2
    assert len(await _jobs(seeded, seeded.tenant_id)) == 1
    await _assert_preserved(seeded)


async def test_successful_upload_commits_job_and_preserves_storage(
    upload_fixture: UploadFixture, redis: Redis
) -> None:
    seeded = upload_fixture
    assert await _upload(seeded) == 201
    new_jobs = await _jobs(seeded, seeded.tenant_id) - seeded.old_jobs
    assert len(new_jobs) == 1
    new_job = new_jobs.pop()
    assert new_job[3] == "pending"
    path = new_job[2]
    assert path is not None
    assert seeded.puts == [path]
    assert seeded.removes == []
    assert seeded.objects[path] == CSV
    await _assert_preserved(seeded)


async def test_failure_after_put_rolls_back_job_and_removes_only_new_object(
    upload_fixture: UploadFixture, redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = upload_fixture

    class FailingResponse:
        @staticmethod
        def model_validate(_job: CatalogImportJob) -> None:
            raise RuntimeError("Synthetic response validation failure")

    monkeypatch.setattr(catalog_router, "ImportJobRead", FailingResponse)
    assert await _upload(seeded) == 500
    assert len(seeded.puts) == 1
    assert seeded.removes == seeded.puts
    assert len(seeded.objects) == 2
    assert len(await _jobs(seeded, seeded.tenant_id)) == 1
    await _assert_preserved(seeded)


async def test_uncertain_put_error_retains_object_and_rolls_back_job(
    upload_fixture: UploadFixture, redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = upload_fixture

    def uncertain_put(*, object_name: str, data: bytes, content_type: str) -> str:
        path = default_object_path(object_name)
        seeded.puts.append(path)
        seeded.objects[path] = data
        raise ConnectionError("Synthetic lost storage acknowledgment")

    monkeypatch.setattr(catalog_router, "put_object", uncertain_put)
    assert await _upload(seeded) == 500
    assert len(seeded.puts) == 1
    assert seeded.removes == []
    assert seeded.objects[seeded.puts[0]] == CSV
    assert len(await _jobs(seeded, seeded.tenant_id)) == 1
    await _assert_preserved(seeded)


async def test_error_after_real_commit_preserves_job_and_object(
    upload_fixture: UploadFixture, redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = upload_fixture
    original_create = CatalogService.create_import_job
    committed: list[bool] = []

    def fail_after_commit(session: SyncSession) -> None:
        assert not session.in_nested_transaction()
        committed.append(True)
        raise RuntimeError("Synthetic error after acknowledged commit")

    async def create_with_commit_fault(
        service: CatalogService,
        *,
        tenant_id: UUID,
        user_id: UUID,
        source_filename: str,
        source_path: str,
    ) -> CatalogImportJob:
        job = await original_create(
            service,
            tenant_id=tenant_id,
            user_id=user_id,
            source_filename=source_filename,
            source_path=source_path,
        )
        # Instance-local listener: no changes to SQLAlchemy's base classes.
        event.listen(service.repo.session.sync_session, "after_commit", fail_after_commit)
        return job

    monkeypatch.setattr(CatalogService, "create_import_job", create_with_commit_fault)
    assert await _upload(seeded) == 500
    assert committed == [True]
    new_jobs = await _jobs(seeded, seeded.tenant_id) - seeded.old_jobs
    assert len(new_jobs) == 1
    new_job = new_jobs.pop()
    assert new_job[3] == "pending"
    path = new_job[2]
    assert path is not None
    assert seeded.puts == [path]
    assert seeded.removes == []
    assert seeded.objects[path] == CSV
    await _assert_preserved(seeded)


async def test_failed_storage_cleanup_preserves_request_failure_and_old_objects(
    upload_fixture: UploadFixture, redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = upload_fixture

    class FailingResponse:
        @staticmethod
        def model_validate(_job: CatalogImportJob) -> None:
            raise RuntimeError("Synthetic response validation failure")

    def failed_remove(path: str) -> None:
        assert path in seeded.puts
        seeded.removes.append(path)
        raise ConnectionError("Synthetic storage delete failure")

    monkeypatch.setattr(catalog_router, "ImportJobRead", FailingResponse)
    monkeypatch.setattr(catalog_router, "remove_object", failed_remove)
    assert await _upload(seeded) == 500
    assert len(seeded.puts) == 1
    assert seeded.removes == seeded.puts
    assert seeded.objects[seeded.puts[0]] == CSV
    assert len(await _jobs(seeded, seeded.tenant_id)) == 1
    await _assert_preserved(seeded)
