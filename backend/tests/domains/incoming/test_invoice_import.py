"""Supplier invoices remain reviewable and exact-file retries never create stock twice."""

from __future__ import annotations

import importlib.util
import io
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from openpyxl import Workbook
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.core.errors import BusinessRuleError, ConflictError, PermissionDeniedError
from app.domains.catalog.models import TenantCatalog
from app.domains.foundation.models import Branch, Tenant
from app.domains.incoming.draft_service import IncomingDraftService
from app.domains.incoming.invoice_parser import parse_invoice, suggested_mapping, validate_mapping
from app.domains.incoming.invoice_service import IncomingInvoiceService
from app.domains.incoming.repository import IncomingRepository
from app.domains.incoming.router import _draft_read
from app.domains.incoming.schemas import IncomingDraftFields
from app.domains.incoming.service import IncomingService
from app.domains.suppliers.models import Supplier

type Scaffold = Callable[[], Awaitable[tuple[Tenant, Branch, TenantCatalog, Supplier]]]


def _csv() -> bytes:
    expiry = (date.today() + timedelta(days=365)).isoformat()
    return (
        "Номи ассортимент;Фаъолият;Микдор;Нарх;Сумма\n"
        f"Парацетамол;{expiry};2;5,50;11,00\n"
        f"Ибупрофен;{expiry};3;7,20;21,60\n"
    ).encode()


def test_tajik_headers_map_purchase_price_without_guessing_sale_price() -> None:
    table = parse_invoice(_csv(), "invoice.csv")
    mapping = suggested_mapping(table.headers)
    assert mapping["source_name"] == 0
    assert mapping["expires_at"] == 1
    assert mapping["qty"] == 2
    assert mapping["purchase_price"] == 3
    assert mapping["sale_price"] is None
    assert len(table.rows) == 2


def test_xlsx_and_malformed_mapping_are_bounded() -> None:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["Товар", "Количество"])
    sheet.append(["Препарат", 2])
    output = io.BytesIO()
    book.save(output)
    table = parse_invoice(output.getvalue(), "invoice.xlsx")
    assert table.rows == [["Препарат", "2"]]
    mapping = suggested_mapping(table.headers)
    mapping["qty"] = mapping["source_name"]
    with pytest.raises(BusinessRuleError) as error:
        validate_mapping(mapping, len(table.headers))
    assert error.value.details["reason"] == "invoice_mapping_invalid"


async def test_import_is_atomic_and_same_file_retry_survives_row_commit(
    db_session: AsyncSession, scaffold: Scaffold
) -> None:
    tenant, branch, catalog, supplier = await scaffold()
    incoming = IncomingService(IncomingRepository(db_session))
    document = await incoming.create_document(
        tenant_id=tenant.id,
        fields={"branch_id": branch.id, "supplier_id": supplier.id, "document_date": date.today()},
    )
    importer = IncomingInvoiceService(incoming)
    raw = _csv()
    mapping = suggested_mapping(parse_invoice(raw, "invoice.csv").headers)
    first_operation = uuid4()
    first = await importer.import_rows(
        document.id,
        raw,
        "invoice.csv",
        mapping=mapping,
        operation_id=first_operation,
        actor_id=uuid4(),
        allowed_branch_ids={branch.id},
    )
    assert first == {"imported_count": 2, "existing_count": 0}
    rows = await incoming.repo.list_draft_rows(document.id)
    assert {row.fields["source_name"] for row in rows} == {"Парацетамол", "Ибупрофен"}
    assert all(row.fields["sale_price"] == "" and row.fields["catalog_id"] == "" for row in rows)
    assert all("_invoice_file_hash" in row.fields for row in rows)
    read = _draft_read(rows[0], catalog_name=None, show_cost=False).model_dump()
    assert read["fields"]["purchase_price"] is None
    assert read["fields"]["source_name"] in {"Парацетамол", "Ибупрофен"}
    assert not any(key.startswith("_invoice_") for key in read["fields"])

    changed_mapping = {**mapping, "sale_price": mapping["purchase_price"], "purchase_price": None}
    with pytest.raises(ConflictError) as changed:
        await importer.import_rows(
            document.id,
            raw,
            "invoice.csv",
            mapping=changed_mapping,
            operation_id=uuid4(),
            actor_id=uuid4(),
            allowed_branch_ids={branch.id},
        )
    assert changed.value.details["reason"] == "invoice_import_conflict"
    with pytest.raises(ConflictError) as changed_file:
        await importer.import_rows(
            document.id,
            raw.replace(b";2;", b";4;"),
            "invoice.csv",
            mapping=mapping,
            operation_id=first_operation,
            actor_id=uuid4(),
            allowed_branch_ids={branch.id},
        )
    assert changed_file.value.details["reason"] == "invoice_import_conflict"

    # Save a reviewed row and commit it; import retries must still find the
    # durable draft marker rather than silently creating a second pending row.
    selected = rows[0]
    fields = {**selected.fields, "catalog_id": str(catalog.id), "sale_price": "9"}
    saved = await IncomingDraftService(incoming).save(
        document.id,
        selected.id,
        expected_revision=1,
        fields=IncomingDraftFields.model_validate(
            {key: value for key, value in fields.items() if not key.startswith("_invoice_")}
        ),
        actor_id=uuid4(),
    )
    await IncomingDraftService(incoming).commit(
        document.id, selected.id, expected_revision=saved.revision, actor_id=uuid4()
    )
    replay = await importer.import_rows(
        document.id,
        raw,
        "invoice.csv",
        mapping=mapping,
        operation_id=uuid4(),
        actor_id=uuid4(),
        allowed_branch_ids={branch.id},
    )
    assert replay == {"imported_count": 0, "existing_count": 2}
    assert len(await incoming.repo.list_draft_rows(document.id)) == 1


async def test_import_requires_branch_access_and_rejects_bad_row_without_writes(
    db_session: AsyncSession, scaffold: Scaffold
) -> None:
    tenant, branch, _, supplier = await scaffold()
    _, other_branch, _, _ = await scaffold()
    incoming = IncomingService(IncomingRepository(db_session))
    document = await incoming.create_document(
        tenant_id=tenant.id,
        fields={"branch_id": branch.id, "supplier_id": supplier.id, "document_date": date.today()},
    )
    importer = IncomingInvoiceService(incoming)
    raw = _csv()
    mapping = suggested_mapping(parse_invoice(raw, "invoice.csv").headers)
    with pytest.raises(PermissionDeniedError):
        await importer.import_rows(
            document.id,
            raw,
            "invoice.csv",
            mapping=mapping,
            operation_id=uuid4(),
            actor_id=uuid4(),
            allowed_branch_ids={other_branch.id},
        )
    assert await incoming.repo.list_draft_rows(document.id) == []
    with pytest.raises(PermissionDeniedError):
        await importer.preview(
            document.id, raw, "invoice.csv", allowed_branch_ids={other_branch.id}
        )
    invalid = b"Name;Qty\nValid;2\n;3\n"
    map_invalid: dict[str, int | None] = dict.fromkeys(mapping)
    map_invalid["source_name"] = 0
    map_invalid["qty"] = 1
    with pytest.raises(BusinessRuleError) as error:
        await importer.import_rows(
            document.id,
            invalid,
            "invoice.csv",
            mapping=map_invalid,
            operation_id=uuid4(),
            actor_id=uuid4(),
            allowed_branch_ids={branch.id},
        )
    assert error.value.details == {"reason": "invoice_row_invalid", "rows": [3], "total": 1}
    assert await incoming.repo.list_draft_rows(document.id) == []


async def test_invoice_fields_migration_roundtrip_on_disposable_database(
    maintenance_engine: AsyncEngine,
) -> None:
    path = (
        Path(__file__).resolve().parents[3]
        / "alembic"
        / "versions"
        / "0152_incoming_invoice_draft_provenance.py"
    )
    spec = importlib.util.spec_from_file_location("incoming_invoice_migration_rehearsal", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def rehearse(connection: Connection) -> None:
        database = str(connection.scalar(text("SELECT current_database()")))
        assert database.endswith("_test")
        connection.execute(text("SET LOCAL ROLE aurum_schema_owner"))
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
            old_definition = str(
                connection.scalar(
                    text(
                        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conrelid='public.incoming_draft_row'::regclass "
                        "AND conname='ck_incoming_draft_fields'"
                    )
                )
            )
            assert "_invoice_file_hash" not in old_definition
            module.upgrade()
            new_definition = str(
                connection.scalar(
                    text(
                        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                        "WHERE conrelid='public.incoming_draft_row'::regclass "
                        "AND conname='ck_incoming_draft_fields'"
                    )
                )
            )
            assert "_invoice_file_hash" in new_definition

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
