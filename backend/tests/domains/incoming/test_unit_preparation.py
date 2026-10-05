"""Opt-in incoming packaging stays a snapshot and never activates unit stock."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.domains.catalog.models import TenantCatalog
from app.domains.catalog.repository import CatalogRepository
from app.domains.catalog.service import CatalogService
from app.domains.catalog.unit_sales import MAX_BASE_UNITS
from app.domains.foundation.models import Branch, Tenant
from app.domains.incoming.models import IncomingDocument
from app.domains.incoming.repository import IncomingRepository
from app.domains.incoming.schemas import IncomingItemBase, IncomingItemUpdate
from app.domains.incoming.service import IncomingService
from app.domains.inventory.models import Batch
from app.domains.inventory.repository import InventoryRepository
from app.domains.inventory.unit_ledger_models import UnitStockLot
from app.domains.suppliers.models import Supplier

Scaffold = Callable[[], Awaitable[tuple[Tenant, Branch, TenantCatalog, Supplier]]]


def _preparation(factor: int = 20) -> dict[str, object]:
    return {
        "version": 1,
        "state": "prepared",
        "unit": "tablet",
        "units_per_pack": factor,
        "dispense_step": 2,
        "unit_price": "1.25",
    }


def _snapshot(factor: int = 20) -> dict[str, object]:
    return {
        "version": 1,
        "unit": "tablet",
        "units_per_pack": factor,
        "dispense_step": 2,
    }


def _fields(catalog_id: UUID, qty: str = "2") -> dict[str, object]:
    return {
        "catalog_id": catalog_id,
        "expires_at": date.today() + timedelta(days=180),
        "qty": Decimal(qty),
        "purchase_price": Decimal("3.00"),
        "sale_price": Decimal("5.00"),
    }


async def _draft(
    session: AsyncSession, scaffold: Scaffold
) -> tuple[IncomingService, IncomingDocument, TenantCatalog]:
    tenant, branch, catalog, supplier = await scaffold()
    catalog.unit_sale_preparation = _preparation()
    await session.flush()
    service = IncomingService(IncomingRepository(session))
    document = await service.create_document(
        tenant_id=tenant.id,
        fields={
            "branch_id": branch.id,
            "supplier_id": supplier.id,
            "document_date": date.today(),
        },
    )
    return service, document, catalog


@pytest.mark.parametrize("value", [None, "true", "false", 1, 0])
def test_prepare_flag_rejects_null_and_coercion(value: object) -> None:
    with pytest.raises(ValidationError):
        IncomingItemBase.model_validate(
            {**_fields(uuid4()), "operation_id": uuid4(), "prepare_unit_sale": value}
        )
    with pytest.raises(ValidationError):
        IncomingItemUpdate.model_validate({"prepare_unit_sale": value})


def test_prepare_flag_defaults_and_server_owned_snapshot() -> None:
    request = IncomingItemBase.model_validate({**_fields(uuid4()), "operation_id": uuid4()})
    assert request.prepare_unit_sale is False
    assert "prepare_unit_sale" not in IncomingItemUpdate().model_dump(exclude_unset=True)
    for schema, fields in (
        (IncomingItemBase, {**_fields(uuid4()), "operation_id": uuid4()}),
        (IncomingItemUpdate, {}),
    ):
        with pytest.raises(ValidationError):
            schema.model_validate({**fields, "unit_packaging_preparation": _snapshot()})


@pytest.mark.parametrize("blister", [None, 10])
async def test_accept_copies_original_packaging_without_creating_unit_stock(
    db_session: AsyncSession, scaffold: Scaffold, blister: int | None
) -> None:
    service, document, catalog = await _draft(db_session, scaffold)
    if blister is not None:
        catalog.unit_sale_preparation = {**_preparation(), "units_per_blister": blister}
        await db_session.flush()
    row = await service.add_item(
        document.id, fields={**_fields(catalog.id), "prepare_unit_sale": True}
    )
    assert row.unit_packaging_preparation == _snapshot()
    catalog.unit_sale_preparation = None
    await db_session.flush()

    await service.accept(document.id)
    await db_session.execute(
        text("SET CONSTRAINTS trg_validate_incoming_unit_packaging_preparation IMMEDIATE")
    )
    original_batch_id = row.created_batch_id
    await service.accept(document.id)
    batches = list(
        (await db_session.scalars(select(Batch).where(Batch.tenant_id == document.tenant_id))).all()
    )
    assert len(batches) == 1
    batch = batches[0]
    assert batch.id == original_batch_id == row.created_batch_id
    assert batch.unit_packaging_preparation == _snapshot()
    assert batch.qty_initial == batch.qty_remaining == Decimal("2")
    assert (
        list(
            (
                await db_session.scalars(
                    select(UnitStockLot).where(UnitStockLot.tenant_id == document.tenant_id)
                )
            ).all()
        )
        == []
    )
    with pytest.raises(BusinessRuleError):
        await service.update_item(document.id, row.id, fields={"prepare_unit_sale": False})


@pytest.mark.parametrize("explicit_false", [False, True])
async def test_legacy_fractional_incoming_does_not_capture_catalog_preparation(
    db_session: AsyncSession, scaffold: Scaffold, explicit_false: bool
) -> None:
    service, document, catalog = await _draft(db_session, scaffold)
    fields = _fields(catalog.id, "1.500")
    if explicit_false:
        fields["prepare_unit_sale"] = False
    row = await service.add_item(document.id, fields=fields)
    assert row.unit_packaging_preparation is None
    await service.accept(document.id)
    batch = await db_session.get(Batch, row.created_batch_id)
    assert batch is not None
    assert batch.unit_packaging_preparation is None
    assert batch.qty_remaining == Decimal("1.500")


@pytest.mark.parametrize("qty", ["1.5", "0", "-1", str(MAX_BASE_UNITS // 20 + 1)])
async def test_prepared_incoming_rejects_invalid_pack_quantities(
    db_session: AsyncSession, scaffold: Scaffold, qty: str
) -> None:
    service, document, catalog = await _draft(db_session, scaffold)
    with pytest.raises(BusinessRuleError):
        await service.add_item(
            document.id, fields={**_fields(catalog.id, qty), "prepare_unit_sale": True}
        )
    assert await service.list_items(document.id) == []
    assert document.total_amount == Decimal("0")


async def test_preparation_requires_explicit_catalog_configuration(
    db_session: AsyncSession, scaffold: Scaffold
) -> None:
    service, document, catalog = await _draft(db_session, scaffold)
    catalog.unit_sale_preparation = None
    await db_session.flush()
    with pytest.raises(BusinessRuleError):
        await service.add_item(
            document.id, fields={**_fields(catalog.id), "prepare_unit_sale": True}
        )
    assert await service.list_items(document.id) == []


async def test_prepared_retry_uses_original_snapshot_and_payload(
    db_session: AsyncSession, scaffold: Scaffold
) -> None:
    service, document, catalog = await _draft(db_session, scaffold)
    operation_id = uuid4()
    fields = {**_fields(catalog.id), "prepare_unit_sale": True}
    row = await service.add_item(document.id, fields=fields, operation_id=operation_id)
    catalog.unit_sale_preparation = _preparation(40)
    await db_session.flush()
    repeated = await service.add_item(document.id, fields=fields, operation_id=operation_id)
    assert repeated.id == row.id
    assert repeated.unit_packaging_preparation == _snapshot()
    await service.accept(document.id)
    accepted_retry = await service.add_item(document.id, fields=fields, operation_id=operation_id)
    assert accepted_retry.id == row.id
    for changed in (
        _fields(catalog.id),
        {**fields, "prepare_unit_sale": False},
        {**fields, "qty": Decimal("3")},
        {**fields, "sale_price": Decimal("7")},
    ):
        with pytest.raises(ConflictError):
            await service.add_item(document.id, fields=changed, operation_id=operation_id)


async def test_quantity_update_preserves_snapshot_and_disabling_allows_fraction(
    db_session: AsyncSession, scaffold: Scaffold
) -> None:
    service, document, catalog = await _draft(db_session, scaffold)
    row = await service.add_item(
        document.id, fields={**_fields(catalog.id), "prepare_unit_sale": True}
    )
    catalog.unit_sale_preparation = _preparation(40)
    await db_session.flush()
    await service.update_item(document.id, row.id, fields={"qty": Decimal("3")})
    assert row.unit_packaging_preparation == _snapshot()
    with pytest.raises(BusinessRuleError):
        await service.update_item(document.id, row.id, fields={"qty": Decimal("1.5")})
    assert row.qty == Decimal("3")
    await service.update_item(
        document.id, row.id, fields={"qty": Decimal("1.5"), "prepare_unit_sale": False}
    )
    assert row.unit_packaging_preparation is None
    assert row.qty == Decimal("1.5")


async def test_explicit_repreparation_captures_current_catalog_packaging(
    db_session: AsyncSession, scaffold: Scaffold
) -> None:
    service, document, catalog = await _draft(db_session, scaffold)
    row = await service.add_item(
        document.id, fields={**_fields(catalog.id), "prepare_unit_sale": True}
    )
    catalog.unit_sale_preparation = _preparation(40)
    await db_session.flush()
    await service.update_item(document.id, row.id, fields={"prepare_unit_sale": True})
    assert row.unit_packaging_preparation == _snapshot(40)
    await service.accept(document.id)
    batch = await db_session.get(Batch, row.created_batch_id)
    assert batch is not None
    assert batch.unit_packaging_preparation == _snapshot(40)


@pytest.mark.parametrize("recapture", [False, True])
async def test_catalog_replacement_clears_or_explicitly_recaptures_snapshot(
    db_session: AsyncSession, scaffold: Scaffold, recapture: bool
) -> None:
    service, document, catalog = await _draft(db_session, scaffold)
    row = await service.add_item(
        document.id, fields={**_fields(catalog.id), "prepare_unit_sale": True}
    )
    replacement = await CatalogService(CatalogRepository(db_session)).create_item(
        tenant_id=document.tenant_id,
        fields={"brand_name": "Replacement", "unit_sale_preparation": _preparation(40)},
    )
    fields: dict[str, object] = {"catalog_id": replacement.id}
    if recapture:
        fields["prepare_unit_sale"] = True
    await service.update_item(document.id, row.id, fields=fields)
    assert row.catalog_id == replacement.id
    assert row.unit_packaging_preparation == (_snapshot(40) if recapture else None)


async def test_preparation_obeys_tenant_and_branch_scope(
    db_session: AsyncSession, scaffold: Scaffold
) -> None:
    service, document, catalog = await _draft(db_session, scaffold)
    _, _, foreign_catalog, _ = await scaffold()
    foreign_catalog.unit_sale_preparation = _preparation()
    await db_session.flush()
    with pytest.raises(NotFoundError):
        await service.add_item(
            document.id, fields={**_fields(foreign_catalog.id), "prepare_unit_sale": True}
        )
    operation_id = uuid4()
    fields = {**_fields(catalog.id), "prepare_unit_sale": True}
    row = await service.add_item(document.id, fields=fields, operation_id=operation_id)
    denied = {uuid4()}
    with pytest.raises(PermissionDeniedError):
        await service.add_item(
            document.id, fields=fields, operation_id=operation_id, allowed_branch_ids=denied
        )
    with pytest.raises(PermissionDeniedError):
        await service.update_item(
            document.id, row.id, fields={"prepare_unit_sale": False}, allowed_branch_ids=denied
        )
    with pytest.raises(PermissionDeniedError):
        await service.accept(document.id, allowed_branch_ids=denied)
    assert row.unit_packaging_preparation == _snapshot()
    assert document.status == "draft"


async def test_acceptance_rollback_removes_snapshot_batch_and_allows_retry(
    db_session: AsyncSession, scaffold: Scaffold
) -> None:
    service, document, catalog = await _draft(db_session, scaffold)
    row = await service.add_item(
        document.id, fields={**_fields(catalog.id), "prepare_unit_sale": True}
    )
    document_id, row_id = document.id, row.id
    savepoint = await db_session.begin_nested()
    await service.accept(document_id)
    assert row.created_batch_id is not None
    await savepoint.rollback()
    await db_session.refresh(document)
    await db_session.refresh(row)
    assert document.status == "draft"
    assert row.created_batch_id is None
    assert row.unit_packaging_preparation == _snapshot()
    assert list((await db_session.scalars(select(Batch))).all()) == []
    await service.accept(document_id)
    await db_session.refresh(row)
    assert row.id == row_id
    batch = await db_session.get(Batch, row.created_batch_id)
    assert batch is not None
    assert batch.unit_packaging_preparation == _snapshot()


async def test_database_rejects_malformed_and_mutated_packaging(
    db_session: AsyncSession, scaffold: Scaffold
) -> None:
    service, document, catalog = await _draft(db_session, scaffold)
    row = await service.add_item(
        document.id, fields={**_fields(catalog.id), "prepare_unit_sale": True}
    )
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "UPDATE incoming_item "
                    "SET unit_packaging_preparation = '{}'::jsonb WHERE id = :id"
                ),
                {"id": row.id},
            )
    await service.accept(document.id)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE incoming_item SET unit_packaging_preparation = NULL WHERE id = :id"),
                {"id": row.id},
            )
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE batch SET unit_packaging_preparation = NULL WHERE id = :id"),
                {"id": row.created_batch_id},
            )
    batch = await db_session.get(Batch, row.created_batch_id)
    assert batch is not None
    await db_session.refresh(batch)
    assert batch.unit_packaging_preparation == _snapshot()


@pytest.mark.parametrize(
    ("snapshot", "qty"),
    [
        pytest.param([], "2", id="array"),
        pytest.param(None, "2", id="json-null"),
        pytest.param("prepared", "2", id="string"),
        pytest.param({**_snapshot(), "version": True}, "2", id="boolean-version"),
        pytest.param({**_snapshot(), "version": "1"}, "2", id="string-version"),
        pytest.param({**_snapshot(), "version": 2}, "2", id="unsupported-version"),
        pytest.param({**_snapshot(), "unit": "pack"}, "2", id="unsupported-unit"),
        pytest.param({**_snapshot(), "unit_price": "1.25"}, "2", id="price-extra-key"),
        pytest.param({**_snapshot(), "state": "prepared"}, "2", id="state-extra-key"),
        pytest.param({**_snapshot(), "units_per_pack": "20"}, "2", id="string-factor"),
        pytest.param({**_snapshot(), "units_per_pack": True}, "2", id="boolean-factor"),
        pytest.param({**_snapshot(), "units_per_pack": 20.5}, "2", id="fractional-factor"),
        pytest.param({**_snapshot(), "units_per_pack": 1}, "2", id="factor-below-minimum"),
        pytest.param({**_snapshot(), "units_per_pack": 10001}, "2", id="factor-over-limit"),
        pytest.param({**_snapshot(), "dispense_step": "2"}, "2", id="string-step"),
        pytest.param({**_snapshot(), "dispense_step": None}, "2", id="null-step"),
        pytest.param({**_snapshot(), "dispense_step": 0}, "2", id="zero-step"),
        pytest.param({**_snapshot(), "dispense_step": 3}, "2", id="nondividing-step"),
        pytest.param({**_snapshot(), "dispense_step": 10001}, "2", id="step-over-limit"),
        pytest.param(_snapshot(), "1.5", id="fractional-packs"),
        pytest.param(_snapshot(), str(MAX_BASE_UNITS // 20 + 1), id="base-quantity-overflow"),
    ],
)
async def test_database_rejects_invalid_draft_snapshot_and_quantity(
    db_session: AsyncSession, scaffold: Scaffold, snapshot: object, qty: str
) -> None:
    service, document, catalog = await _draft(db_session, scaffold)
    row = await service.add_item(
        document.id, fields={**_fields(catalog.id), "prepare_unit_sale": True}
    )
    with pytest.raises(DBAPIError, match="ck_ii_unit_packaging_preparation"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "UPDATE incoming_item "
                    "SET unit_packaging_preparation = CAST(:snapshot AS jsonb), "
                    "qty = :qty WHERE id = :id"
                ),
                {"snapshot": json.dumps(snapshot), "qty": Decimal(qty), "id": row.id},
            )
    await db_session.refresh(row)
    assert row.unit_packaging_preparation == _snapshot()
    assert row.qty == Decimal("2")


async def test_database_rejects_adding_packaging_to_legacy_batch(
    db_session: AsyncSession, scaffold: Scaffold
) -> None:
    service, document, catalog = await _draft(db_session, scaffold)
    row = await service.add_item(document.id, fields=_fields(catalog.id))
    await service.accept(document.id)
    await db_session.execute(
        text("SET CONSTRAINTS trg_validate_incoming_unit_packaging_preparation IMMEDIATE")
    )
    with pytest.raises(DBAPIError, match="Batch unit packaging preparation is immutable"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "UPDATE batch SET unit_packaging_preparation = CAST(:snapshot AS jsonb) "
                    "WHERE id = :id"
                ),
                {"snapshot": json.dumps(_snapshot()), "id": row.created_batch_id},
            )
    batch = await db_session.get(Batch, row.created_batch_id)
    assert batch is not None
    await db_session.refresh(batch)
    assert batch.unit_packaging_preparation is None
    assert batch.qty_remaining == Decimal("2")


async def test_database_rejects_acceptance_with_missing_batch_snapshot(
    db_session: AsyncSession, scaffold: Scaffold, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, document, catalog = await _draft(db_session, scaffold)
    row = await service.add_item(
        document.id, fields={**_fields(catalog.id), "prepare_unit_sale": True}
    )
    document_id = document.id
    original_create_batch = InventoryRepository.create_batch

    async def create_without_snapshot(repository: InventoryRepository, **fields: object) -> Batch:
        fields.pop("unit_packaging_preparation", None)
        return await original_create_batch(repository, **fields)

    monkeypatch.setattr(InventoryRepository, "create_batch", create_without_snapshot)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await service.accept(document_id)
            await db_session.execute(
                text("SET CONSTRAINTS trg_validate_incoming_unit_packaging_preparation IMMEDIATE")
            )
    await db_session.refresh(document)
    await db_session.refresh(row)
    assert document.status == "draft"
    assert row.created_batch_id is None
    assert row.unit_packaging_preparation == _snapshot()
    assert list((await db_session.scalars(select(Batch))).all()) == []
