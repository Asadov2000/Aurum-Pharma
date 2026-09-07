"""Admission, abandonment recovery and cancellation of catalog executions."""

from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import BusinessRuleError
from app.core.time import utc_now
from app.domains.catalog import import_parser
from app.domains.catalog.import_limits import (
    CATALOG_EXECUTION_TIMEOUT_SECONDS,
    catalog_execution_lock_key,
)
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService


async def _job(service, make_tenant, make_user):
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    return await service.create_import_job(
        tenant_id=tenant.id,
        user_id=user.id,
        source_filename="lifecycle.csv",
        source_path=f"aurum/{tenant.id}/imports/lifecycle.csv",
    )


async def test_preview_admission_does_not_parse_and_retry_keeps_execution(
    db_session: AsyncSession, make_tenant, make_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = utc_now()
    service = CatalogService(CatalogRepository(db_session), now=lambda: now)
    job = await _job(service, make_tenant, make_user)
    parser = Mock(side_effect=AssertionError("API admission must not parse files"))
    monkeypatch.setattr("app.domains.catalog.service.parse_import", parser)

    started = await service.request_preview(job_id=job.id)
    execution_id = started.execution_id
    deadline = started.deadline_at
    now += timedelta(minutes=1)
    repeated = await service.request_preview(job_id=job.id)

    assert repeated.status == "previewing"
    assert execution_id is not None and repeated.execution_id == execution_id
    assert repeated.execution_started_at is None
    assert deadline == repeated.deadline_at
    assert deadline == repeated.started_at + timedelta(seconds=CATALOG_EXECUTION_TIMEOUT_SECONDS)
    parser.assert_not_called()


async def test_confirm_requires_completed_nonempty_preview(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    service = CatalogService(CatalogRepository(db_session))
    job = await _job(service, make_tenant, make_user)
    with pytest.raises(BusinessRuleError):
        await service.confirm_import(job_id=job.id, duplicate_strategy="skip")
    await service.request_preview(job_id=job.id)
    with pytest.raises(BusinessRuleError):
        await service.confirm_import(job_id=job.id, duplicate_strategy="skip")
    await service.preview_import(job_id=job.id, raw=b"brand_name\n")
    with pytest.raises(BusinessRuleError, match="no valid rows"):
        await service.confirm_import(job_id=job.id, duplicate_strategy="skip")


async def test_confirmation_issues_new_execution_and_retry_preserves_it(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    service = CatalogService(CatalogRepository(db_session))
    job = await _job(service, make_tenant, make_user)
    await service.request_preview(job_id=job.id)
    preview_token = job.execution_id
    await service.preview_import(job_id=job.id, raw=b"brand_name\nTest\n")
    await service.confirm_import(job_id=job.id, duplicate_strategy="skip")
    import_token = job.execution_id
    deadline = job.deadline_at
    assert import_token != preview_token
    assert job.execution_started_at is None

    await service.confirm_import(job_id=job.id, duplicate_strategy="skip")
    assert job.execution_id == import_token
    assert job.deadline_at == deadline
    with pytest.raises(BusinessRuleError, match="another duplicate strategy"):
        await service.confirm_import(job_id=job.id, duplicate_strategy="update")


@pytest.mark.parametrize("phase", ["previewing", "importing"])
async def test_status_recovers_abandoned_execution(
    db_session: AsyncSession, make_tenant, make_user, phase: str
) -> None:
    service = CatalogService(CatalogRepository(db_session))
    job = await _job(service, make_tenant, make_user)
    await service.request_preview(job_id=job.id)
    await service.repo.update_job(job, status=phase, deadline_at=utc_now() - timedelta(seconds=1))

    recovered = await service.get_job(job.id)

    assert recovered.status == "failed"
    assert recovered.finished_at is not None
    assert recovered.source_path == job.source_path
    assert recovered.errors and "Загрузите файл повторно" in recovered.errors[0]["messages"][0]


async def test_status_does_not_wait_for_live_worker_execution_lock(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    make_tenant,
    make_user,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = CatalogService(CatalogRepository(db_session))
    job = await _job(service, make_tenant, make_user)
    await service.request_preview(job_id=job.id)
    await service.repo.update_job(job, deadline_at=utc_now() - timedelta(seconds=1))
    row_lock = AsyncMock(side_effect=AssertionError("Must not wait for the live worker row lock"))
    monkeypatch.setattr(service.repo, "get_job_for_update", row_lock)
    async with db_engine.connect() as worker_connection:
        async with worker_connection.begin():
            await worker_connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": catalog_execution_lock_key(job.id)},
            )
            result = await service.get_job(job.id)
            assert result.status == "previewing"
            row_lock.assert_not_called()


async def test_new_preview_recovers_old_execution_and_preserves_uploaded_jobs(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    service = CatalogService(CatalogRepository(db_session))
    old = await _job(service, make_tenant, make_user)
    new = await service.create_import_job(
        tenant_id=old.tenant_id,
        user_id=old.user_id,
        source_filename="replacement.csv",
        source_path=f"aurum/{old.tenant_id}/imports/replacement.csv",
    )
    await service.request_preview(job_id=old.id)
    with pytest.raises(BusinessRuleError, match="already running"):
        await service.request_preview(job_id=new.id)
    await service.repo.update_job(old, deadline_at=utc_now() - timedelta(seconds=1))

    result = await service.request_preview(job_id=new.id)

    assert result.status == "previewing"
    assert old.status == "failed"
    assert await service.repo.get_job(old.id) is not None
    assert old.source_path is not None


async def test_active_previews_for_different_tenants_do_not_block(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    service = CatalogService(CatalogRepository(db_session))
    first = await _job(service, make_tenant, make_user)
    second = await _job(service, make_tenant, make_user)

    await service.request_preview(job_id=first.id)
    await service.request_preview(job_id=second.id)

    assert first.status == second.status == "previewing"


async def test_soft_timeout_is_not_swallowed_as_a_row_error(
    db_session: AsyncSession, make_tenant, make_user, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = CatalogService(CatalogRepository(db_session))
    job = await _job(service, make_tenant, make_user)
    await service.request_preview(job_id=job.id)
    await service.preview_import(job_id=job.id, raw=b"brand_name\nFirst\nSecond\n")
    await service.confirm_import(job_id=job.id, duplicate_strategy="skip")
    apply_row = AsyncMock(side_effect=SoftTimeLimitExceeded())
    monkeypatch.setattr(service, "_apply_import_row", apply_row)

    with pytest.raises(SoftTimeLimitExceeded):
        await service.process_import(job_id=job.id, raw=b"brand_name\nFirst\nSecond\n")

    assert apply_row.await_count == 1
    assert job.status == "importing"


def test_xlsx_loader_does_not_mask_soft_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(import_parser, "_validate_xlsx_archive", lambda _raw: None)
    monkeypatch.setattr(import_parser, "load_workbook", Mock(side_effect=SoftTimeLimitExceeded()))
    with pytest.raises(SoftTimeLimitExceeded):
        import_parser.parse_xlsx(b"placeholder")
