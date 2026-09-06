"""XLSX import: parse_xlsx unit cases + service-pipeline parity with CSV.

XLSX fixtures are generated in-memory with openpyxl (no binaries in the repo).
"""

from __future__ import annotations

from io import BytesIO
from typing import Any
from uuid import UUID

import pytest
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.domains.catalog import import_parser
from app.domains.catalog.import_parser import (
    XLS_UNSUPPORTED_MESSAGE,
    parse_import,
    parse_xlsx,
)
from app.domains.catalog.models import TenantCatalog
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService

HEADER = [
    "brand_name",
    "inn",
    "manufacturer",
    "dosage",
    "pack_size",
    "dispensing_type",
    "base_price",
    "barcode",
]


def _xlsx(header: list[Any], rows: list[list[Any]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(header)
    for r in rows:
        ws.append(r)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# parser unit
# --------------------------------------------------------------------------- #


def test_parse_xlsx_happy_path() -> None:
    raw = _xlsx(
        HEADER,
        [
            ["Aspirin", "asa", "Bayer", "500mg", "10 tab", "otc", 12.50, 1234567890123],
            ["Paracetamol", "para", "GSK", "500mg", "20 tab", "otc", 8.75, 2222222222222],
        ],
    )
    rows, errors = parse_xlsx(raw)
    assert errors == []
    assert len(rows) == 2
    assert rows[0]["brand_name"] == "Aspirin"
    # A numeric barcode must stay digits, not collapse to 1.23E+12.
    assert rows[0]["barcode"] == "1234567890123"
    assert str(rows[0]["base_price"]) == "12.5"


def test_parse_xlsx_headers_case_insensitive() -> None:
    raw = _xlsx(["Brand_Name", "Base_Price", "Dispensing_Type"], [["Aspirin", "10.00", "otc"]])
    rows, errors = parse_xlsx(raw)
    assert errors == []
    assert rows[0]["brand_name"] == "Aspirin"


def test_parse_xlsx_missing_brand_name_column() -> None:
    raw = _xlsx(["inn", "base_price"], [["asa", "10.00"]])
    with pytest.raises(ValueError, match="brand_name"):
        parse_xlsx(raw)


def test_parse_xlsx_bad_price_is_row_error() -> None:
    raw = _xlsx(["brand_name", "base_price"], [["Aspirin", "not-a-number"]])
    rows, errors = parse_xlsx(raw)
    assert rows == []
    assert errors[0]["row"] == 2
    assert any("base_price" in m for m in errors[0]["messages"])


def test_parse_xlsx_uncached_formula_in_brand_name_is_row_error() -> None:
    # With data_only=True a formula cell that has no cached value reads as None;
    # an empty brand_name cell models exactly that case.
    raw = _xlsx(["brand_name", "base_price"], [[None, "10.00"]])
    rows, errors = parse_xlsx(raw)
    assert rows == []
    assert "brand_name is required" in errors[0]["messages"]


def test_parse_xlsx_extra_columns_ignored() -> None:
    raw = _xlsx(["brand_name", "junk", "base_price"], [["Aspirin", "whatever", "10.00"]])
    rows, errors = parse_xlsx(raw)
    assert errors == []
    assert "junk" not in rows[0]


def test_parse_xlsx_skips_blank_rows() -> None:
    raw = _xlsx(
        ["brand_name", "base_price"],
        [["Aspirin", "10.00"], [None, None], ["", ""]],
    )
    rows, errors = parse_xlsx(raw)
    assert len(rows) == 1
    assert errors == []


def test_parse_xlsx_rejects_archive_over_uncompressed_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _xlsx(["brand_name"], [["Aspirin"]])
    monkeypatch.setattr(import_parser, "MAX_XLSX_UNCOMPRESSED_BYTES", 1)

    with pytest.raises(ValueError, match="safety limit"):
        parse_xlsx(raw)


def test_parse_xlsx_rejects_too_many_archive_members(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _xlsx(["brand_name"], [["Aspirin"]])
    monkeypatch.setattr(import_parser, "MAX_XLSX_ARCHIVE_MEMBERS", 1)

    with pytest.raises(ValueError, match="too many internal files"):
        parse_xlsx(raw)


def test_parse_import_xls_rejected_with_russian_message() -> None:
    with pytest.raises(ValueError) as exc_info:
        parse_import(b"whatever", "PRICE.XLS")
    assert str(exc_info.value) == XLS_UNSUPPORTED_MESSAGE


def test_parse_import_routes_xlsx_and_csv() -> None:
    rows_xlsx, _ = parse_import(_xlsx(["brand_name"], [["Aspirin"]]), "data.xlsx")
    assert rows_xlsx[0]["brand_name"] == "Aspirin"
    rows_csv, _ = parse_import(b"brand_name\nAspirin\n", "data.csv")
    assert rows_csv[0]["brand_name"] == "Aspirin"


# --------------------------------------------------------------------------- #
# service pipeline parity (XLSX flows through the same pipeline as CSV)
# --------------------------------------------------------------------------- #


async def _count_items(
    db: AsyncSession, tenant_id: UUID, *, import_job_id: UUID | None = None
) -> int:
    stmt = (
        select(func.count())
        .select_from(TenantCatalog)
        .where(TenantCatalog.tenant_id == tenant_id, TenantCatalog.deleted_at.is_(None))
    )
    if import_job_id is not None:
        stmt = stmt.where(TenantCatalog.import_job_id == import_job_id)
    return int((await db.execute(stmt)).scalar_one())


async def test_xlsx_preview_and_process_creates_items(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    service = CatalogService(CatalogRepository(db_session))
    raw = _xlsx(
        HEADER,
        [
            ["Xlsx-A", "a", "M1", "1mg", "1", "otc", 5.00, 7777777777777],
            ["Xlsx-B", "b", "M2", "2mg", "2", "otc", 6.00, ""],
            ["", "c", "M3", "3mg", "3", "otc", 7.00, ""],  # missing brand_name → error
        ],
    )

    job = await service.create_import_job(
        tenant_id=tenant.id,
        user_id=user.id,
        source_filename="price.xlsx",
        source_path="aurum/test/price.xlsx",
    )
    previewed = await service.preview_import(job_id=job.id, raw=raw)
    assert previewed.status == "validating"
    assert previewed.total_rows == 3
    assert previewed.valid_rows == 2
    assert previewed.error_rows == 1

    await service.repo.update_job(
        job, status="importing", duplicate_strategy="skip", started_at=utc_now()
    )
    result = await service.process_import(job_id=job.id, raw=raw)
    assert result.status == "success"
    assert await _count_items(db_session, tenant.id, import_job_id=job.id) == 2


async def test_xlsx_duplicate_skip_strategy(
    db_session: AsyncSession, make_tenant, make_user
) -> None:
    tenant = await make_tenant()
    user = await make_user(home_tenant_id=tenant.id)
    service = CatalogService(CatalogRepository(db_session))

    await service.create_item(
        tenant_id=tenant.id,
        fields={"brand_name": "DupX", "manufacturer": "MD", "dosage": "9mg", "pack_size": "9"},
    )
    raw = _xlsx(
        HEADER,
        [
            ["DupX", "x", "MD", "9mg", "9", "otc", 1.00, ""],  # duplicate → skipped
            ["FreshX", "y", "MF", "1mg", "1", "otc", 2.00, ""],  # new
        ],
    )
    job = await service.create_import_job(
        tenant_id=tenant.id,
        user_id=user.id,
        source_filename="dup.xlsx",
        source_path="aurum/test/dup.xlsx",
    )
    await service.repo.update_job(
        job, status="importing", duplicate_strategy="skip", started_at=utc_now()
    )
    await service.process_import(job_id=job.id, raw=raw)

    # Only the non-duplicate row is tagged with this import job.
    assert await _count_items(db_session, tenant.id, import_job_id=job.id) == 1
