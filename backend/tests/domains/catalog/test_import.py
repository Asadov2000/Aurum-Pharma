"""Import job pipeline: preview, process, rollback (24h window)."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError
from app.core.time import utc_now
from app.domains.catalog.models import TenantCatalog
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService

SAMPLE_CSV = (
    b"brand_name,inn,manufacturer,dosage,pack_size,dispensing_type,base_price,barcode\n"
    b"Aspirin,acetylsalicylic acid,Bayer,500mg,10 tablets,otc,12.50,1234567890123\n"
    b"Paracetamol,paracetamol,GSK,500mg,20 tablets,otc,8.75,2222222222222\n"
    b"Amiksin,tilorone,Lekko,125mg,6 tablets,otc,99.00,\n"
)


async def _count_items(
    db: AsyncSession, tenant_id: UUID, *, import_job_id: UUID | None = None
) -> int:
    """Count this tenant's live catalog rows directly. Test sessions run on the
    BYPASSRLS pool with no app.tenant_id GUC, so service.search() would see
    every tenant's rows in the shared dev DB — we scope explicitly instead."""
    stmt = (
        select(func.count())
        .select_from(TenantCatalog)
        .where(TenantCatalog.tenant_id == tenant_id, TenantCatalog.deleted_at.is_(None))
    )
    if import_job_id is not None:
        stmt = stmt.where(TenantCatalog.import_job_id == import_job_id)
    return int((await db.execute(stmt)).scalar_one())


async def test_import_preview_returns_stats(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    service = CatalogService(CatalogRepository(db_session))

    job = await service.create_import_job(
        tenant_id=tenant.id,
        user_id=user.id,
        source_filename="import.csv",
        source_path="aurum/test/import.csv",
    )
    updated = await service.preview_import(job_id=job.id, raw=SAMPLE_CSV)

    assert updated.status == "validating"
    assert updated.total_rows == 3
    assert updated.valid_rows == 3
    assert updated.error_rows == 0
    assert updated.preview_data is not None and len(updated.preview_data) == 3


async def test_import_preview_collects_errors(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    service = CatalogService(CatalogRepository(db_session))

    bad_csv = (
        b"brand_name,base_price,dispensing_type\n"
        b",10.00,otc\n"  # missing brand_name → error
        b"Valid,not-a-number,otc\n"  # bad base_price → error
        b"Other,15.00,unknown_type\n"  # bad dispensing_type → error
    )
    job = await service.create_import_job(
        tenant_id=tenant.id,
        user_id=user.id,
        source_filename="bad.csv",
        source_path="aurum/test/bad.csv",
    )
    updated = await service.preview_import(job_id=job.id, raw=bad_csv)
    assert updated.error_rows == 3
    assert updated.valid_rows == 0


async def test_import_process_creates_items(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    service = CatalogService(CatalogRepository(db_session))

    job = await service.create_import_job(
        tenant_id=tenant.id,
        user_id=user.id,
        source_filename="import.csv",
        source_path="aurum/test/import.csv",
    )
    # confirm_import normally kicks Celery; in tests we step around it and
    # set the status + duplicate strategy by hand so process_import runs
    # against a job in a sensible state.
    await service.repo.update_job(
        job, status="importing", duplicate_strategy="skip", started_at=utc_now()
    )
    result = await service.process_import(job_id=job.id, raw=SAMPLE_CSV)

    assert result.status == "success"
    assert result.valid_rows == 3
    assert result.expires_at_for_rollback is not None

    # All three items are on disk, tagged with this import job (scoped to the
    # job, so accumulated rows in the shared dev DB don't affect the count).
    assert await _count_items(db_session, tenant.id, import_job_id=job.id) == 3
    assert await _count_items(db_session, tenant.id) == 3


async def test_import_rollback_soft_deletes(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    service = CatalogService(CatalogRepository(db_session))

    job = await service.create_import_job(
        tenant_id=tenant.id,
        user_id=user.id,
        source_filename="import.csv",
        source_path="aurum/test/import.csv",
    )
    await service.repo.update_job(
        job, status="importing", duplicate_strategy="skip", started_at=utc_now()
    )
    await service.process_import(job_id=job.id, raw=SAMPLE_CSV)

    rolled = await service.rollback_import(job.id)
    assert rolled.status == "rolled_back"

    # The job's rows are soft-deleted, so none of this tenant's items remain.
    assert await _count_items(db_session, tenant.id, import_job_id=job.id) == 0
    assert await _count_items(db_session, tenant.id) == 0


async def test_import_rollback_blocked_after_24h(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    service = CatalogService(CatalogRepository(db_session))

    job = await service.create_import_job(
        tenant_id=tenant.id,
        user_id=user.id,
        source_filename="import.csv",
        source_path="aurum/test/import.csv",
    )
    await service.repo.update_job(
        job, status="importing", duplicate_strategy="skip", started_at=utc_now()
    )
    finished_job = await service.process_import(job_id=job.id, raw=SAMPLE_CSV)

    # Push expires_at_for_rollback into the past through the repo so the
    # ORM identity map sees the new value on the next read.
    await service.repo.update_job(
        finished_job, expires_at_for_rollback=utc_now() - timedelta(hours=1)
    )

    with pytest.raises(BusinessRuleError):
        await service.rollback_import(job.id)


async def test_import_duplicate_skip_strategy(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    service = CatalogService(CatalogRepository(db_session))

    # Seed an existing row that matches one of the import lines.
    await service.create_item(
        tenant_id=tenant.id,
        fields={
            "brand_name": "Aspirin",
            "manufacturer": "Bayer",
            "dosage": "500mg",
            "pack_size": "10 tablets",
        },
    )

    job = await service.create_import_job(
        tenant_id=tenant.id,
        user_id=user.id,
        source_filename="import.csv",
        source_path="aurum/test/import.csv",
    )
    await service.repo.update_job(
        job, status="importing", duplicate_strategy="skip", started_at=utc_now()
    )
    await service.process_import(job_id=job.id, raw=SAMPLE_CSV)

    # Existing Aspirin + 2 new (Paracetamol, Amiksin); the dup is skipped.
    assert await _count_items(db_session, tenant.id) == 3
    # Only the 2 non-duplicate rows are tagged with this import job.
    assert await _count_items(db_session, tenant.id, import_job_id=job.id) == 2
