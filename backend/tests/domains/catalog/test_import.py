"""Import job pipeline: preview, process, rollback (24h window)."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from app.core.errors import BusinessRuleError, ValidationError
from app.core.time import utc_now
from app.domains.catalog import import_parser
from app.domains.catalog.models import TenantCatalog
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.router import _read_import_file
from app.domains.catalog.service import CatalogService
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.inventory.repository import InventoryRepository

SAMPLE_CSV = (
    b"brand_name,inn,manufacturer,dosage,pack_size,dispensing_type,base_price,barcode\n"
    b"Aspirin,acetylsalicylic acid,Bayer,500mg,10 tablets,otc,12.50,1234567890123\n"
    b"Paracetamol,paracetamol,GSK,500mg,20 tablets,otc,8.75,2222222222222\n"
    b"Amiksin,tilorone,Lekko,125mg,6 tablets,otc,99.00,\n"
)


def test_import_parser_rejects_excessive_row_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(import_parser, "MAX_IMPORT_ROWS", 2)
    raw = b"brand_name\nFirst\nSecond\nThird\n"

    with pytest.raises(ValueError, match="more than 2 rows"):
        import_parser.parse_csv(raw)


async def test_import_upload_stops_reading_after_size_limit() -> None:
    upload = UploadFile(filename="oversized.csv", file=BytesIO(b"123"))

    with pytest.raises(ValidationError, match="Файл больше"):
        await _read_import_file(upload, max_bytes=2)


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


async def test_import_preview_maps_parser_failure_to_validation_error(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    service = CatalogService(CatalogRepository(db_session))
    job = await service.create_import_job(
        tenant_id=tenant.id,
        user_id=user.id,
        source_filename="broken.csv",
        source_path="aurum/test/broken.csv",
    )

    with pytest.raises(ValidationError, match="brand_name"):
        await service.preview_import(job_id=job.id, raw=b"wrong_header\nvalue\n")


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


async def test_confirm_import_is_idempotent_for_same_strategy(
    db_session: AsyncSession,
    make_tenant,
    make_user,
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    service = CatalogService(CatalogRepository(db_session))
    job = await service.create_import_job(
        tenant_id=tenant.id,
        user_id=user.id,
        source_filename="idempotent.csv",
        source_path="aurum/test/idempotent.csv",
    )

    first = await service.confirm_import(job_id=job.id, duplicate_strategy="skip")
    repeated = await service.confirm_import(job_id=job.id, duplicate_strategy="skip")

    assert first.status == repeated.status == "importing"


async def test_confirm_import_blocks_second_active_job_for_tenant(
    db_session: AsyncSession,
    make_tenant,
    make_user,
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    service = CatalogService(CatalogRepository(db_session))
    first = await service.create_import_job(
        tenant_id=tenant.id,
        user_id=user.id,
        source_filename="first.csv",
        source_path="aurum/test/first.csv",
    )
    second = await service.create_import_job(
        tenant_id=tenant.id,
        user_id=user.id,
        source_filename="second.csv",
        source_path="aurum/test/second.csv",
    )

    await service.confirm_import(job_id=first.id, duplicate_strategy="skip")
    with pytest.raises(BusinessRuleError) as exc_info:
        await service.confirm_import(job_id=second.id, duplicate_strategy="skip")

    assert exc_info.value.details == {"reason": "catalog_import_in_progress"}


async def test_process_import_is_idempotent_after_success(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    service = CatalogService(CatalogRepository(db_session))
    job = await service.create_import_job(
        tenant_id=tenant.id,
        user_id=user.id,
        source_filename="retry.csv",
        source_path="aurum/test/retry.csv",
    )
    await service.repo.update_job(
        job,
        status="importing",
        duplicate_strategy="skip",
        started_at=utc_now(),
    )

    first = await service.process_import(job_id=job.id, raw=SAMPLE_CSV)
    repeated = await service.process_import(job_id=job.id, raw=SAMPLE_CSV)

    assert first.status == repeated.status == "success"
    assert await _count_items(db_session, tenant.id, import_job_id=job.id) == 3


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


async def test_import_rollback_rejects_item_with_positive_stock(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    service = CatalogService(CatalogRepository(db_session))
    job = await service.create_import_job(
        tenant_id=tenant.id,
        user_id=user.id,
        source_filename="stocked.csv",
        source_path="aurum/test/stocked.csv",
    )
    await service.repo.update_job(
        job,
        status="importing",
        duplicate_strategy="skip",
        started_at=utc_now(),
    )
    await service.process_import(job_id=job.id, raw=SAMPLE_CSV)
    imported = (
        await db_session.execute(
            select(TenantCatalog)
            .where(TenantCatalog.import_job_id == job.id)
            .order_by(TenantCatalog.id)
            .limit(1)
        )
    ).scalar_one()
    branch = await FoundationService(FoundationRepository(db_session)).create_branch(
        tenant_id=tenant.id,
        fields={"name": "Stocked import branch"},
    )
    await InventoryRepository(db_session).create_batch(
        tenant_id=tenant.id,
        branch_id=branch.id,
        catalog_id=imported.id,
        expires_at=date.today() + timedelta(days=90),
        purchase_price=Decimal("5.00"),
        sale_price=Decimal("10.00"),
        qty_initial=Decimal("2.000"),
        qty_remaining=Decimal("2.000"),
    )

    with pytest.raises(BusinessRuleError):
        await service.rollback_import(job.id)


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


async def test_import_row_conflict_does_not_abort_following_rows(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    service = CatalogService(CatalogRepository(db_session))
    csv_with_duplicate_barcode = (
        b"brand_name,barcode\n"
        b"Savepoint first,3333333333333\n"
        b"Savepoint conflict,3333333333333\n"
        b"Savepoint after,4444444444444\n"
    )
    job = await service.create_import_job(
        tenant_id=tenant.id,
        user_id=user.id,
        source_filename="savepoints.csv",
        source_path="aurum/test/savepoints.csv",
    )
    await service.repo.update_job(
        job, status="importing", duplicate_strategy="skip", started_at=utc_now()
    )

    result = await service.process_import(job_id=job.id, raw=csv_with_duplicate_barcode)

    assert result.status == "success"
    assert result.valid_rows == 2
    assert result.error_rows == 1
    assert await _count_items(db_session, tenant.id, import_job_id=job.id) == 2
