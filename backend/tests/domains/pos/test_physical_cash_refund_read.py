"""Saved physical refunds remain readable without private payout or cost access."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import AurumError, NotFoundError, PermissionDeniedError
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.inventory.unit_stock_v2 import SaleFormatV2, UnitPackagingV2
from app.domains.pos.models import Sale
from app.domains.pos.physical_cash_refund_read import (
    physical_cash_refund_lines,
    physical_cash_refund_snapshot,
)
from app.domains.pos.physical_cash_refund_schemas import PhysicalCashRefundReceiptV3
from app.domains.pos.physical_receipt_read import physical_receipt_snapshot
from app.domains.pos.receipt_pdf import render_receipt_pdf
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.pos.test_physical_cash_checkout import complete
from tests.domains.pos.test_physical_cash_refund import facts, refund
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


def viewer(actor_id: UUID, tenant_id: UUID, branch_id: UUID, permission: str) -> CurrentUser:
    return CurrentUser(
        user_id=actor_id,
        tenant_id=tenant_id,
        is_developer=False,
        is_administrator=False,
        permissions={permission},
        permission_scopes={permission: frozenset({branch_id})},
    )


@pytest.mark.parametrize(
    "sale_format,quantity,label",
    [("unit", 7, "табл."), ("blister", 2, "блист."), ("pack", 1, "уп.")],
)
async def test_refund_read_print_and_details_preserve_historical_allocated_money(
    db_session: AsyncSession, sale_format: SaleFormatV2, quantity: int, label: str
) -> None:
    from app.domains.pos.router import get_sale

    source = await checkout_source(db_session)
    item = await put_physical(
        db_session, source.sale.id, sale_format="pack", quantity=2, discount=Decimal("1.01")
    )
    original = await complete(db_session, await saved_request(db_session, source.sale))
    result = await refund(
        db_session, source.sale.id, item.id, source.sale.shift_id, quantity, sale_format=sale_format
    )
    context = runtime(db_session)
    service = POSService(POSRepository(db_session))
    before = await facts(db_session)
    receipt = await service.build_receipt(
        result.sale_id, viewer_id=context.actor_id, allowed_branch_ids={context.stock.branch_id}
    )
    saved = result.receipt.lines[0]
    line = receipt.items[0]
    assert receipt.is_refund is True
    assert receipt.original_receipt_number == original.receipt_number
    assert receipt.receipt_number == result.receipt.receipt_number
    assert receipt.total == result.total_amount == receipt.payments[0].amount
    assert receipt.datetime.utcoffset() == timedelta(hours=5)
    assert line.name == original.lines[0].name
    assert line.qty == 1
    assert line.unit_label == "возврат"
    assert line.unit_price == saved.gross_amount
    assert line.discount_amount == saved.discount_amount
    assert line.total_price == saved.net_amount
    assert line.unit_price * line.qty - line.discount_amount == line.total_price
    assert line.quantity_description == (
        f"Возвращено: {quantity} {label}; исходная цена: "
        f"{original.lines[0].original_retail_unit_price:.2f} TJS / уп."
    )
    assert render_receipt_pdf(receipt).startswith(b"%PDF-")
    user = viewer(
        context.actor_id, context.stock.tenant_id, context.stock.branch_id, "sales.view.own"
    )
    details = await get_sale(result.sale_id, user, service)
    assert details.items == []
    assert details.physical_items == receipt.items
    assert details.payments[0].amount == receipt.total
    assert await facts(db_session) == before
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET brand_name='Changed after refund',base_price=999 "
            "WHERE id=:id"
        ),
        {"id": context.stock.catalog_id},
    )
    assert await service.build_receipt(result.sale_id) == receipt


async def test_cashier_with_only_own_read_permission_can_print_own_saved_refund(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    from app.domains.pos.router import get_sale

    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    result = await refund(db_session, source.sale.id, item.id, source.sale.shift_id)
    context = runtime(db_session)
    await db_session.commit()
    readonly = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("sales.view.own",)
    )
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text(
                "UPDATE user_assignment SET role_id=(SELECT role_id FROM user_assignment "
                "WHERE user_id=:readonly AND tenant_id=:tenant AND branch_id=:branch) "
                "WHERE user_id=:actor AND tenant_id=:tenant AND branch_id=:branch"
            ),
            {
                "readonly": readonly,
                "actor": context.actor_id,
                "tenant": context.stock.tenant_id,
                "branch": context.stock.branch_id,
            },
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert (
        await db_session.scalar(
            text("SELECT tenant_actor_has_scoped_permission(:tenant,'pos.refund',:branch)"),
            {"tenant": context.stock.tenant_id, "branch": context.stock.branch_id},
        )
        is False
    )
    for table in (
        "pos_physical_cash_refund",
        "pos_physical_return_line",
        "pos_physical_return_slice",
    ):
        assert await db_session.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    service = POSService(POSRepository(db_session))
    receipt = await service.build_receipt(
        result.sale_id, viewer_id=context.actor_id, allowed_branch_ids={context.stock.branch_id}
    )
    assert receipt.total == result.total_amount
    details = await get_sale(
        result.sale_id,
        viewer(
            context.actor_id, context.stock.tenant_id, context.stock.branch_id, "sales.view.own"
        ),
        service,
    )
    assert details.physical_items == receipt.items


async def test_tenant_reader_can_print_refund_without_private_cost_journal_access(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    from app.domains.pos.router import get_sale

    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    result = await refund(db_session, source.sale.id, item.id, source.sale.shift_id)
    context = runtime(db_session)
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("sales.view.tenant",)
    )
    await set_receiving_context(db_session, context.stock, actor)
    for table in (
        "pos_physical_cash_refund",
        "pos_physical_return_line",
        "pos_physical_return_slice",
    ):
        assert await db_session.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    service = POSService(POSRepository(db_session))
    receipt = await service.build_receipt(
        result.sale_id,
        viewer_id=actor,
        allowed_branch_ids={context.stock.branch_id},
        allowed_view_branch_ids={context.stock.branch_id},
    )
    assert receipt.total == result.total_amount
    assert "cost" not in receipt.model_dump_json()
    details = await get_sale(
        result.sale_id,
        viewer(actor, context.stock.tenant_id, context.stock.branch_id, "sales.view.tenant"),
        service,
    )
    assert details.physical_items == receipt.items
    with pytest.raises(PermissionDeniedError):
        await service.build_receipt(
            result.sale_id, viewer_id=actor, allowed_branch_ids={context.stock.branch_id}
        )
    with pytest.raises(PermissionDeniedError):
        await service.build_receipt(
            result.sale_id,
            viewer_id=actor,
            allowed_branch_ids={uuid4()},
            allowed_view_branch_ids={context.stock.branch_id},
        )


@pytest.mark.parametrize("same_tenant", [False, True])
async def test_other_pharmacy_reader_cannot_fetch_refund_even_with_tenant_view(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    same_tenant: bool,
) -> None:
    from app.domains.pos.router import get_sale

    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    result = await refund(db_session, source.sale.id, item.id, source.sale.shift_id)
    context = runtime(db_session)
    await db_session.commit()
    async with AsyncSession(db_engine) as support, support.begin():
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        if same_tenant:
            branch = await FoundationService(FoundationRepository(support)).create_branch(
                tenant_id=context.stock.tenant_id, fields={"name": "Other pharmacy reader"}
            )
            foreign = replace(context.stock, branch_id=branch.id)
        else:
            foreign = await seed_ledger_context(support)
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, foreign, permissions=("sales.view.tenant",)
    )
    # Force RLS to fetch from the DB rather than reuse an identity-map object.
    db_session.expunge_all()
    await set_receiving_context(db_session, foreign, actor)
    service = POSService(POSRepository(db_session))
    user = viewer(actor, foreign.tenant_id, foreign.branch_id, "sales.view.tenant")
    assert user.has_tenant_scope("sales.view.tenant") is False
    denial = PermissionDeniedError if same_tenant else NotFoundError
    with pytest.raises(denial):
        await service.build_receipt(
            result.sale_id,
            viewer_id=actor,
            can_view_tenant=user.has_tenant_scope("sales.view.tenant"),
            allowed_branch_ids={foreign.branch_id},
            allowed_view_branch_ids={foreign.branch_id},
        )
    with pytest.raises(denial):
        await get_sale(
            result.sale_id,
            user,
            service,
        )


def saved_refund_sale() -> Sale:
    timestamp = datetime(2026, 10, 9, 8, 30, tzinfo=UTC)
    parent_sale_id = uuid4()
    payload = PhysicalCashRefundReceiptV3.model_validate(
        {
            "parent_sale_id": parent_sale_id,
            "parent_receipt_number": "R-1",
            "receipt_number": "R-2",
            "receipt_sequence": 2,
            "completed_at": timestamp,
            "is_test": False,
            "total_amount": "3.99",
            "lines": [
                {
                    "original_line_id": uuid4(),
                    "position": 1,
                    "name": "Saved medicine",
                    "packaging": UnitPackagingV2(
                        unit="tablet", units_per_pack=30, units_per_blister=10, dispense_step=1
                    ),
                    "original_sale_format": "pack",
                    "original_retail_unit_price": "60.00",
                    "sale_format": "unit",
                    "quantity": 2,
                    "base_units": 2,
                    "offset": 0,
                    "gross_amount": "4.00",
                    "discount_amount": "0.01",
                    "net_amount": "3.99",
                }
            ],
        }
    )
    return Sale(
        id=uuid4(),
        parent_sale_id=parent_sale_id,
        sale_type="return",
        status="completed",
        receipt_number="R-2",
        receipt_seq=2,
        completed_at=timestamp,
        is_test=False,
        currency="TJS",
        total_amount=Decimal("3.99"),
        receipt_snapshot=payload.model_dump(mode="json"),
    )


def test_pure_adapter_preserves_exact_discounted_amount_without_fractional_prices() -> None:
    sale = saved_refund_sale()
    receipt = physical_cash_refund_snapshot(sale)
    assert receipt is not None
    line = physical_cash_refund_lines(receipt)[0]
    assert line.qty == 1 and line.unit_label == "возврат"
    assert line.unit_price == Decimal("4.00")
    assert line.discount_amount == Decimal("0.01")
    assert line.total_price == Decimal("3.99")


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 4),
        ("schema_version", True),
        ("receipt_number", "Wrong"),
        ("receipt_sequence", 1),
        ("parent_sale_id", str(uuid4())),
        ("completed_at", "2026-10-09T09:30:00Z"),
        ("is_test", True),
        ("currency", "USD"),
        ("total_amount", "4.00"),
        ("lines", []),
    ],
)
def test_pure_adapter_rejects_corrupt_or_mismatched_snapshot(field: str, value: object) -> None:
    sale = saved_refund_sale()
    payload = deepcopy(sale.receipt_snapshot)
    assert payload is not None
    payload[field] = value
    sale.receipt_snapshot = payload
    with pytest.raises(AurumError):
        physical_cash_refund_snapshot(sale)


def test_unknown_versioned_document_cannot_fall_back_to_legacy_receipt() -> None:
    sale = saved_refund_sale()
    payload = deepcopy(sale.receipt_snapshot)
    assert payload is not None
    payload["document_kind"] = "receipt"
    sale.receipt_snapshot = payload
    assert physical_cash_refund_snapshot(sale) is None
    with pytest.raises(AurumError):
        physical_receipt_snapshot(sale)


@pytest.mark.parametrize(
    "field,value", [("status", "draft"), ("sale_type", "sale"), ("parent_sale_id", None)]
)
def test_pure_adapter_rejects_noncompleted_or_unlinked_return(field: str, value: object) -> None:
    sale = saved_refund_sale()
    setattr(sale, field, value)
    with pytest.raises(AurumError):
        physical_cash_refund_snapshot(sale)
