"""Committed bulk imports, fallback semantics and whole-transaction rollback."""

from __future__ import annotations

import csv
from decimal import Decimal
from io import StringIO
from unittest.mock import Mock
from uuid import UUID

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.catalog.import_batch import IMPORT_BATCH_SIZE
from app.domains.catalog.models import Barcode, CatalogImportJob, TenantCatalog
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.tasks import catalog as worker


def _rows(count: int) -> list[list[str]]:
    return [[f"Batch {index:05d}", "12.50", f"288{index:010d}"] for index in range(count)]


def _csv(rows: list[list[str]]) -> bytes:
    stream = StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["brand_name", "base_price", "barcode"])
    writer.writerows(rows)
    return stream.getvalue().encode()


async def _import(db: AsyncSession, tenant_id: UUID, user_id: UUID, rows, strategy="skip"):
    service = CatalogService(CatalogRepository(db))
    job = await service.create_import_job(
        tenant_id=tenant_id,
        user_id=user_id,
        source_filename="batch.csv",
        source_path=f"aurum/{tenant_id}/imports/batch.csv",
    )
    raw = _csv(rows)
    await service.request_preview(job_id=job.id)
    await service.preview_import(job_id=job.id, raw=raw)
    await service.confirm_import(job_id=job.id, duplicate_strategy=strategy)
    return await service.process_import(job_id=job.id, raw=raw)


async def _worker_import(factory, tenant_id, job_id, raw, strategy, monkeypatch):
    monkeypatch.setattr(worker, "get_catalog_object", Mock(return_value=raw))
    async with factory.begin() as db:
        job = await CatalogService(CatalogRepository(db)).request_preview(job_id=job_id)
        execution_id = str(job.execution_id)
    await worker._execute_job(str(job_id), str(tenant_id), execution_id, phase="previewing")
    async with factory.begin() as db:
        job = await CatalogService(CatalogRepository(db)).confirm_import(
            job_id=job_id, duplicate_strategy=strategy
        )
        execution_id = str(job.execution_id)
    return await worker._execute_job(str(job_id), str(tenant_id), execution_id, phase="importing")


async def test_batch_worker_create_skip_update_and_noop_preserve_ids_and_audit(
    committed_catalog_import, maintenance_engine, monkeypatch
) -> None:
    factory, tenant_id, job_id = committed_catalog_import
    count = IMPORT_BATCH_SIZE + 1
    original_ids = original_codes = None
    async with worker.catalog_worker_engine.connect() as connection:
        assert await connection.scalar(text("SELECT current_user")) == "aurum_catalog_worker"
    for iteration, strategy in enumerate(("skip", "skip", "update", "update")):
        rows = _rows(count)
        if iteration:
            for row in rows:
                row[1] = "19.75"
            async with factory.begin() as db:
                previous = await db.get(CatalogImportJob, job_id)
                job = await CatalogService(CatalogRepository(db)).create_import_job(
                    tenant_id=tenant_id,
                    user_id=previous.user_id,
                    source_filename="batch.csv",
                    source_path=previous.source_path,
                )
                job_id = job.id
        result = await _worker_import(factory, tenant_id, job_id, _csv(rows), strategy, monkeypatch)
        assert result == {"valid": count, "errors": 0}
        async with factory() as db:
            records = (
                await db.execute(
                    select(TenantCatalog.id, TenantCatalog.base_price).where(
                        TenantCatalog.tenant_id == tenant_id
                    )
                )
            ).all()
            codes = set(
                (
                    await db.execute(
                        select(Barcode.id, Barcode.catalog_id, Barcode.code).where(
                            Barcode.tenant_id == tenant_id
                        )
                    )
                ).all()
            )
        ids = {row.id for row in records}
        assert len(ids) == len(codes) == count
        assert {row.base_price for row in records} == {
            Decimal("19.75" if iteration >= 2 else "12.50")
        }
        if iteration == 0:
            original_ids, original_codes = ids, codes
        assert ids == original_ids and codes == original_codes
        async with maintenance_engine.connect() as connection:
            audit = (
                await connection.execute(
                    text(
                        "SELECT count(*) FILTER (WHERE action = 'INSERT'), "
                        "count(*) FILTER (WHERE action = 'UPDATE') FROM public.audit_log "
                        "WHERE tenant_id = :tenant AND table_name = 'tenant_catalog'"
                    ),
                    {"tenant": tenant_id},
                )
            ).one()
        assert audit == (count, count if iteration >= 2 else 0)


async def test_duplicate_at_batch_boundary_updates_existing_product(
    db_session, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    rows = _rows(IMPORT_BATCH_SIZE)
    rows.append([rows[0][0], "29.00", rows[0][2]])
    result = await _import(db_session, tenant.id, user.id, rows, "update")
    assert result.valid_rows == len(rows) and result.error_rows == 0
    records = (
        await db_session.execute(
            select(TenantCatalog.brand_name, TenantCatalog.base_price).where(
                TenantCatalog.tenant_id == tenant.id
            )
        )
    ).all()
    assert len(records) == IMPORT_BATCH_SIZE
    assert dict(records)[rows[0][0]] == Decimal("29.00")


async def test_batch_barcode_conflict_rolls_back_only_bad_update_and_continues(
    db_session, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    rows = _rows(IMPORT_BATCH_SIZE + 1)
    assert (await _import(db_session, tenant.id, user.id, rows)).error_rows == 0
    for row in rows:
        row[1] = "19.75"
    rows[1][2] = rows[0][2]
    result = await _import(db_session, tenant.id, user.id, rows, "update")
    assert result.status == "success"
    assert result.valid_rows == len(rows) - 1 and result.error_rows == 1
    records = dict(
        (
            await db_session.execute(
                select(TenantCatalog.brand_name, TenantCatalog.base_price).where(
                    TenantCatalog.tenant_id == tenant.id
                )
            )
        ).all()
    )
    assert len(records) == len(rows)
    assert records.pop(rows[1][0]) == Decimal("12.50")
    assert set(records.values()) == {Decimal("19.75")}


@pytest.mark.parametrize("conflict_owner", [False, True])
async def test_batch_mixed_new_and_existing_barcodes_verify_every_association(
    db_session, make_tenant, make_user, conflict_owner
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    source = _rows(IMPORT_BATCH_SIZE + 1)
    existing = source[: IMPORT_BATCH_SIZE // 2]
    assert (await _import(db_session, tenant.id, user.id, existing)).error_rows == 0
    original_ids = dict(
        (
            await db_session.execute(
                select(Barcode.code, Barcode.id).where(Barcode.tenant_id == tenant.id)
            )
        ).all()
    )
    # The first existing owner is outside the input: a conflicting code remains
    # unique within this chunk, so mixed RETURNING + conflict lookup is exercised.
    rows = [[name, "19.75", code] for name, _, code in source[1:]]
    if conflict_owner:
        rows[-1][2] = source[0][2]
    result = await _import(db_session, tenant.id, user.id, rows, "update")
    assert result.status == "success"
    assert result.valid_rows == len(rows) - int(conflict_owner)
    assert result.error_rows == int(conflict_owner)
    accepted = rows[:-1] if conflict_owner else rows
    expected = {code: (name, Decimal(price)) for name, price, code in existing}
    expected.update({code: (name, Decimal(price)) for name, price, code in accepted})
    actual = (
        await db_session.execute(
            select(Barcode.code, Barcode.id, TenantCatalog.brand_name, TenantCatalog.base_price)
            .join(TenantCatalog, Barcode.catalog_id == TenantCatalog.id)
            .where(Barcode.tenant_id == tenant.id, TenantCatalog.tenant_id == tenant.id)
        )
    ).all()
    assert {code: (name, price) for code, _, name, price in actual} == expected
    assert {
        code: barcode_id for code, barcode_id, _, _ in actual if code in original_ids
    } == original_ids
    catalog_names = set(
        (
            await db_session.scalars(
                select(TenantCatalog.brand_name).where(TenantCatalog.tenant_id == tenant.id)
            )
        ).all()
    )
    assert catalog_names == {name for name, _ in expected.values()}


@pytest.mark.parametrize("with_barcodes", [False, True])
async def test_batch_create_copy_preserves_conflicting_originals(
    db_session, make_tenant, make_user, with_barcodes
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    rows = _rows(IMPORT_BATCH_SIZE)
    if not with_barcodes:
        for row in rows:
            row[2] = ""
    await _import(db_session, tenant.id, user.id, rows)
    result = await _import(db_session, tenant.id, user.id, rows, "create_copy")
    assert result.error_rows == (len(rows) if with_barcodes else 0)
    assert result.valid_rows == (0 if with_barcodes else len(rows))
    ids = (
        await db_session.scalars(
            select(TenantCatalog.id).where(TenantCatalog.tenant_id == tenant.id)
        )
    ).all()
    assert len(ids) == len(rows) * (1 if with_barcodes else 2)


async def test_batch_unicode_duplicates_keep_database_lower_contract(
    db_session, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    rows = _rows(IMPORT_BATCH_SIZE)
    names = ["İ", "ΟΣ", "ẞ", "АСПИРИН"]
    for index, name in enumerate(names):
        rows[index] = [name, "12.50", ""]
    await _import(db_session, tenant.id, user.id, rows)
    expected_counts = {}
    for index, name in enumerate(names):
        lowered = name.lower()
        matches = await db_session.scalar(
            text(
                "SELECT count(*) FROM tenant_catalog WHERE tenant_id = :tenant "
                "AND lower(brand_name) = :name AND deleted_at IS NULL"
            ),
            {"tenant": tenant.id, "name": lowered},
        )
        expected_counts[lowered] = 1 if matches else 2
        rows[index] = [lowered, "19.75", ""]
    result = await _import(db_session, tenant.id, user.id, rows, "update")
    assert result.valid_rows == len(rows) and result.error_rows == 0
    records = (
        await db_session.execute(
            select(TenantCatalog.brand_name, TenantCatalog.base_price).where(
                TenantCatalog.tenant_id == tenant.id
            )
        )
    ).all()
    assert len(records) == len(rows) + sum(value - 1 for value in expected_counts.values())
    for name in expected_counts:
        assert (name, Decimal("19.75")) in records


async def test_batch_timeout_after_written_chunk_rolls_back_items_barcodes_and_audit(
    committed_catalog_import, maintenance_engine, monkeypatch
) -> None:
    factory, tenant_id, job_id = committed_catalog_import
    raw = _csv(_rows(IMPORT_BATCH_SIZE * 2))
    original = CatalogRepository.create_import_batch
    calls = 0

    async def interrupt_second(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            assert (
                await self.session.scalar(
                    text("SELECT count(*) FROM tenant_catalog WHERE tenant_id = :tenant"),
                    {"tenant": tenant_id},
                )
                == IMPORT_BATCH_SIZE
            )
            assert (
                await self.session.scalar(
                    text("SELECT count(*) FROM barcode WHERE tenant_id = :tenant"),
                    {"tenant": tenant_id},
                )
                == IMPORT_BATCH_SIZE
            )
            raise SoftTimeLimitExceeded()
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(CatalogRepository, "create_import_batch", interrupt_second)
    with pytest.raises(SoftTimeLimitExceeded):
        await _worker_import(factory, tenant_id, job_id, raw, "skip", monkeypatch)
    assert calls == 2
    async with factory() as db:
        assert (await db.get(CatalogImportJob, job_id)).status == "failed"
    async with maintenance_engine.connect() as connection:
        for table in ("tenant_catalog", "barcode"):
            assert (
                await connection.scalar(
                    text(f"SELECT count(*) FROM public.{table} WHERE tenant_id = :tenant"),
                    {"tenant": tenant_id},
                )
                == 0
            )
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM audit_log WHERE tenant_id = :tenant "
                    "AND table_name IN ('tenant_catalog', 'barcode')"
                ),
                {"tenant": tenant_id},
            )
            == 0
        )
