"""Immutable mixed refund details and print views keep retail facts and RLS."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import AurumError, NotFoundError, PermissionDeniedError
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.inventory.unit_stock_v2 import SaleFormatV2
from app.domains.pos.mixed_cash_refund_read import (
    mixed_cash_refund_lines,
    mixed_cash_refund_snapshot,
)
from app.domains.pos.mixed_cash_refund_schemas import MixedCashRefundReceiptV4
from app.domains.pos.mixed_return_allocation import LegacyReturnSelection
from app.domains.pos.models import Sale
from app.domains.pos.physical_receipt_read import physical_receipt_snapshot
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.receipt_pdf import render_receipt_pdf
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.pos.test_mixed_cash_refund import facts, refund
from tests.domains.pos.test_mixed_cash_refund_contracts import receipt_payload
from tests.domains.pos.test_mixed_return_journal import mixed_source
from tests.domains.pos.test_physical_cash_refund_read import saved_refund_sale, viewer
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


@pytest.mark.parametrize(
    "sale_format,label", [("unit", "табл."), ("blister", "блист."), ("pack", "уп.")]
)
@pytest.mark.parametrize("fractional", [False, True])
async def test_saved_mixed_refund_details_print_pdf_preserve_exact_allocations(
    db_session: AsyncSession, sale_format: SaleFormatV2, label: str, fractional: bool
) -> None:
    from app.domains.pos.router import get_sale

    source = await mixed_source(db_session, fractional_legacy=fractional)
    result = await refund(
        db_session,
        source,
        (
            PhysicalReturnSelection(source.physical_id, sale_format, 1),
            LegacyReturnSelection(source.legacy_id, Decimal("0.001")),
        ),
    )
    context = runtime(db_session)
    service = POSService(POSRepository(db_session))
    before = await facts(db_session)
    receipt = await service.build_receipt(
        result.sale_id, viewer_id=context.actor_id, allowed_branch_ids={context.stock.branch_id}
    )
    assert receipt.is_refund is True
    assert receipt.original_receipt_number == source.original.receipt_number
    assert receipt.receipt_number == result.receipt.receipt_number
    assert receipt.total == result.total_amount == receipt.payments[0].amount
    assert receipt.payments[0].method == "cash"
    assert receipt.datetime is not None and receipt.datetime.utcoffset() == timedelta(hours=5)
    assert [line.position for line in receipt.items] == [1, 2]
    assert [line.name for line in receipt.items] == [line.name for line in source.original.lines]
    for line, saved in zip(receipt.items, result.receipt.lines, strict=True):
        assert line.qty == 1 and line.unit_label == "возврат"
        assert line.unit_price == saved.gross_amount
        assert line.discount_amount == saved.discount_amount
        assert line.total_price == saved.net_amount
        assert line.unit_price * line.qty - line.discount_amount == line.total_price
    assert receipt.items[0].quantity_description == (
        f"Возвращено: 0.001 уп.; исходная цена: "
        f"{source.original.lines[0].original_retail_unit_price:.2f} TJS / уп."
    )
    assert receipt.items[1].quantity_description == (
        f"Возвращено: 1 {label}; исходная цена: "
        f"{source.original.lines[1].original_retail_unit_price:.2f} TJS / уп."
    )
    if fractional:
        assert receipt.items[0].total_price == Decimal("0.00")
    assert "cost" not in receipt.model_dump_json()
    assert render_receipt_pdf(receipt).startswith(b"%PDF-")
    details = await get_sale(
        result.sale_id,
        viewer(
            context.actor_id, context.stock.tenant_id, context.stock.branch_id, "sales.view.own"
        ),
        service,
    )
    assert details.items == details.physical_items == details.prepared_cart_items == []
    assert details.mixed_items == receipt.items
    assert details.payments[0].amount == receipt.total
    assert await facts(db_session) == before
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET brand_name='Changed after mixed refund',base_price=999 "
            "WHERE tenant_id=:tenant"
        ),
        {"tenant": context.stock.tenant_id},
    )
    assert await service.build_receipt(result.sale_id) == receipt


async def test_tenant_reader_prints_saved_mixed_refund_without_private_journal_access(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    from app.domains.pos.router import get_sale

    result = await refund(db_session, await mixed_source(db_session))
    context = runtime(db_session)
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("sales.view.tenant",)
    )
    db_session.expunge_all()
    await set_receiving_context(db_session, context.stock, actor)
    for table in ("pos_mixed_cash_refund", "pos_mixed_return_command", "pos_mixed_return_line"):
        assert await db_session.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    before = await facts(db_session)
    service = POSService(POSRepository(db_session))
    receipt = await service.build_receipt(
        result.sale_id,
        viewer_id=actor,
        allowed_branch_ids={context.stock.branch_id},
        allowed_view_branch_ids={context.stock.branch_id},
    )
    assert receipt.total == result.total_amount
    details = await get_sale(
        result.sale_id,
        viewer(actor, context.stock.tenant_id, context.stock.branch_id, "sales.view.tenant"),
        service,
    )
    assert details.mixed_items == receipt.items
    assert "cost" not in details.model_dump_json()
    with pytest.raises(PermissionDeniedError):
        await service.build_receipt(
            result.sale_id, viewer_id=actor, allowed_branch_ids={context.stock.branch_id}
        )
    assert await facts(db_session) == before


@pytest.mark.parametrize("index", [0, 1])
async def test_single_kind_subset_still_uses_mixed_refund_details(
    db_session: AsyncSession, index: int
) -> None:
    from app.domains.pos.router import get_sale

    source = await mixed_source(db_session)
    result = await refund(db_session, source, (source.selections()[index],))
    context = runtime(db_session)
    before = await facts(db_session)
    service = POSService(POSRepository(db_session))
    receipt = await service.build_receipt(
        result.sale_id, viewer_id=context.actor_id, allowed_branch_ids={context.stock.branch_id}
    )
    details = await get_sale(
        result.sale_id,
        viewer(
            context.actor_id, context.stock.tenant_id, context.stock.branch_id, "sales.view.own"
        ),
        service,
    )
    assert len(receipt.items) == 1
    assert receipt.items[0].name == source.original.lines[index].name
    assert details.mixed_items == receipt.items
    assert details.items == details.physical_items == []
    assert await facts(db_session) == before


@pytest.mark.parametrize("same_tenant", [False, True])
async def test_foreign_pharmacy_reader_cannot_fetch_mixed_refund(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    same_tenant: bool,
) -> None:
    from app.domains.pos.router import get_sale

    result = await refund(db_session, await mixed_source(db_session))
    context = runtime(db_session)
    await db_session.commit()
    async with AsyncSession(db_engine) as support, support.begin():
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        if same_tenant:
            branch = await FoundationService(FoundationRepository(support)).create_branch(
                tenant_id=context.stock.tenant_id, fields={"name": "Other mixed refund pharmacy"}
            )
            foreign = replace(context.stock, branch_id=branch.id)
        else:
            foreign = await seed_ledger_context(support)
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, foreign, permissions=("sales.view.tenant",)
    )
    db_session.expunge_all()
    await set_receiving_context(db_session, foreign, actor)
    service = POSService(POSRepository(db_session))
    denial = PermissionDeniedError if same_tenant else NotFoundError
    with pytest.raises(denial):
        await service.build_receipt(
            result.sale_id,
            viewer_id=actor,
            allowed_branch_ids={foreign.branch_id},
            allowed_view_branch_ids={foreign.branch_id},
        )
    with pytest.raises(denial):
        await get_sale(
            result.sale_id,
            viewer(actor, foreign.tenant_id, foreign.branch_id, "sales.view.tenant"),
            service,
        )


def saved_mixed_refund_sale() -> Sale:
    physical = saved_refund_sale()
    assert physical.receipt_snapshot is not None
    payload = receipt_payload()
    legacy_lines = payload["lines"]
    assert isinstance(legacy_lines, list)
    physical_line = deepcopy(physical.receipt_snapshot["lines"][0])
    physical_line.update(inventory_kind="physical_unit", position=2, original_position=4)
    receipt = MixedCashRefundReceiptV4.model_validate(
        receipt_payload(total_amount="4.00", lines=[legacy_lines[0], physical_line])
    )
    return Sale(
        id=uuid4(),
        parent_sale_id=receipt.parent_sale_id,
        sale_type="return",
        status="completed",
        receipt_number=receipt.receipt_number,
        receipt_seq=receipt.receipt_sequence,
        completed_at=receipt.completed_at,
        is_test=False,
        currency="TJS",
        total_amount=receipt.total_amount,
        receipt_snapshot=receipt.model_dump(mode="json"),
    )


def test_adapter_keeps_original_order_and_exact_discount_without_division() -> None:
    sale = saved_mixed_refund_sale()
    snapshot = mixed_cash_refund_snapshot(sale)
    assert snapshot is not None
    lines = mixed_cash_refund_lines(snapshot)
    assert [line.position for line in lines] == [1, 2]
    assert lines[0].total_price == Decimal("0.01")
    assert lines[1].unit_price == Decimal("4.00")
    assert lines[1].discount_amount == Decimal("0.01")
    assert lines[1].total_price == Decimal("3.99")
    assert all(line.qty == 1 for line in lines)
    assert "0.001 уп." in (lines[0].quantity_description or "")
    assert "2 табл." in (lines[1].quantity_description or "")


@pytest.mark.parametrize("quantity", ["10", "10.000", "0.010"])
def test_legacy_print_description_keeps_integer_trailing_zeros(quantity: str) -> None:
    sale = saved_mixed_refund_sale()
    payload = deepcopy(sale.receipt_snapshot)
    assert payload is not None
    payload["lines"][0]["quantity"] = quantity
    sale.receipt_snapshot = payload
    snapshot = mixed_cash_refund_snapshot(sale)
    assert snapshot is not None
    expected = "0.01" if quantity == "0.010" else "10"
    assert f"Возвращено: {expected} уп." in (
        mixed_cash_refund_lines(snapshot)[0].quantity_description or ""
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 3),
        ("schema_version", True),
        ("receipt_number", "Wrong"),
        ("receipt_sequence", 1),
        ("parent_sale_id", str(UUID(int=999))),
        ("completed_at", "2026-10-11T12:00:00Z"),
        ("is_test", True),
        ("currency", "USD"),
        ("total_amount", "4.01"),
        ("physical_cost_amount", "0.01"),
        ("lines", []),
    ],
)
def test_corrupt_or_mismatched_snapshot_never_falls_back(field: str, value: object) -> None:
    sale = saved_mixed_refund_sale()
    payload = deepcopy(sale.receipt_snapshot)
    assert payload is not None
    payload[field] = value
    sale.receipt_snapshot = payload
    with pytest.raises(AurumError):
        mixed_cash_refund_snapshot(sale)


@pytest.mark.parametrize(
    "name", ["", {"brand_name": "Unexpected object"}, 5, "Bad\nname", "Bad\x00name"]
)
def test_malformed_saved_names_fail_closed(name: object) -> None:
    sale = saved_mixed_refund_sale()
    payload = deepcopy(sale.receipt_snapshot)
    assert payload is not None
    payload["lines"][0]["name"] = name
    sale.receipt_snapshot = payload
    with pytest.raises(AurumError):
        POSService.get_mixed_receipt_items(sale)


def test_unknown_versioned_kind_cannot_fall_back_to_legacy() -> None:
    sale = saved_mixed_refund_sale()
    payload = deepcopy(sale.receipt_snapshot)
    assert payload is not None
    payload["document_kind"] = "receipt"
    sale.receipt_snapshot = payload
    assert mixed_cash_refund_snapshot(sale) is None
    with pytest.raises(AurumError):
        physical_receipt_snapshot(sale)


@pytest.mark.parametrize(
    "field,value", [("status", "draft"), ("sale_type", "sale"), ("parent_sale_id", None)]
)
def test_noncompleted_or_unlinked_refund_cannot_print(field: str, value: object) -> None:
    sale = saved_mixed_refund_sale()
    setattr(sale, field, value)
    with pytest.raises(AurumError):
        mixed_cash_refund_snapshot(sale)
