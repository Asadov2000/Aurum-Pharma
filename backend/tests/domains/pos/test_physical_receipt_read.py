"""Read and print saved physical sales without private checkout write permissions."""

from copy import deepcopy
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.deps import CurrentUser
from app.core.errors import AurumError, NotFoundError, PermissionDeniedError
from app.domains.inventory.unit_stock_v2 import SaleFormatV2
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3, build_physical_receipt
from app.domains.pos.physical_receipt_read import physical_receipt_lines, physical_receipt_snapshot
from app.domains.pos.receipt_pdf import _write_receipt_summary, render_receipt_pdf
from app.domains.pos.repository import POSRepository
from app.domains.pos.router import get_sale
from app.domains.pos.service import POSService
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import (
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.pos.test_physical_cash_checkout import complete, facts
from tests.domains.pos.test_physical_documents import line, result_payload
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


def test_three_formats_keep_order_discount_and_original_prices() -> None:
    rows = [
        line(
            id=uuid4(),
            catalog_id=uuid4(),
            position=position,
            sale_format=sale_format,
            base_units=base_units,
        )
        for position, (sale_format, base_units) in enumerate(
            (("pack", 60), ("blister", 20), ("unit", 2)), 1
        )
    ]
    result = PhysicalCheckoutResultV3.model_validate(
        result_payload(
            lines=rows,
            total_amount="70.50",
            payments=[{"id": uuid4(), "method": "cash", "amount": "70.50"}],
        )
    )
    receipt = build_physical_receipt(result)
    # A saved historical training document remains valid for display even though
    # current physical cash posting refuses to create another one.
    assert result.is_test is True and receipt.is_test is True
    items = physical_receipt_lines(receipt)
    assert [item.position for item in items] == [1, 2, 3]
    assert [item.unit_label for item in items] == ["уп.", "блист.", "табл."]
    assert [str(item.qty) for item in items] == ["2", "2", "2"]
    assert sum(item.total_price for item in items) == result.total_amount
    assert str(sum(item.discount_amount for item in items)) == "4.50"


@pytest.mark.parametrize(
    "sale_format,quantity,label,description",
    [
        ("pack", 1, "уп.", "В упаковке: 30 табл."),
        ("blister", 2, "блист.", "В блистере: 10 табл."),
        ("unit", 5, "табл.", None),
    ],
)
async def test_read_physical_receipt_keeps_format_and_historical_retail(
    db_session: AsyncSession,
    sale_format: SaleFormatV2,
    quantity: int,
    label: str,
    description: str | None,
) -> None:
    source = await checkout_source(db_session)
    await put_physical(db_session, source.sale.id, sale_format=sale_format, quantity=quantity)
    result = await complete(db_session, await saved_request(db_session, source.sale))
    context = runtime(db_session)
    service = POSService(POSRepository(db_session))
    before = await facts(db_session)
    receipt = await service.build_receipt(
        source.sale.id, viewer_id=context.actor_id, allowed_branch_ids={context.stock.branch_id}
    )
    assert receipt.items[0].qty == quantity
    assert receipt.items[0].unit_label == label
    assert receipt.items[0].quantity_description == description
    assert receipt.items[0].unit_price == result.lines[0].original_retail_unit_price
    assert receipt.items[0].total_price == result.lines[0].net_amount
    assert receipt.total == result.total_amount == receipt.payments[0].amount
    assert receipt.datetime is not None
    assert receipt.datetime.utcoffset() == timedelta(hours=5)
    assert receipt.paid_total is None and receipt.change is None
    assert render_receipt_pdf(receipt).startswith(b"%PDF-")
    summary: list[str] = []

    def record(
        txt: str, *, h: float = 5, size: int = 9, bold: bool = False, align: str = "L"
    ) -> None:
        summary.append(txt)

    _write_receipt_summary(receipt, record)
    assert not any(value.startswith(("Принято:", "Сдача:")) for value in summary)
    assert await facts(db_session) == before
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET brand_name='Changed after sale',base_price=99 WHERE id=:id"
        ),
        {"id": context.stock.catalog_id},
    )
    repeated = await service.build_receipt(source.sale.id)
    assert repeated == receipt
    user = CurrentUser(
        user_id=context.actor_id,
        tenant_id=context.stock.tenant_id,
        is_developer=False,
        is_administrator=False,
        permissions={"sales.view.own"},
        permission_scopes={"sales.view.own": frozenset({context.stock.branch_id})},
    )
    details = await get_sale(source.sale.id, user, service)
    assert details.items == []
    assert details.physical_items == receipt.items
    assert details.payments[0].amount == receipt.total


async def test_physical_receipt_read_only_viewer_and_scope(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    context = runtime(db_session)
    reader = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("sales.view.tenant",)
    )
    source = await checkout_source(db_session)
    await put_physical(db_session, source.sale.id, sale_format="blister", quantity=2)
    await complete(db_session, await saved_request(db_session, source.sale))
    service = POSService(POSRepository(db_session))
    original = await service.build_receipt(source.sale.id)
    await set_receiving_context(db_session, context.stock, reader)
    assert await db_session.scalar(text("SELECT count(*) FROM pos_physical_checkout")) == 0
    receipt = await service.build_receipt(
        source.sale.id,
        viewer_id=reader,
        allowed_branch_ids={context.stock.branch_id},
        allowed_view_branch_ids={context.stock.branch_id},
    )
    assert receipt.items == original.items
    with pytest.raises(PermissionDeniedError):
        await service.build_receipt(
            source.sale.id, viewer_id=reader, allowed_branch_ids={context.stock.branch_id}
        )
    with pytest.raises(PermissionDeniedError):
        await service.get_receipt_pdf(
            source.sale.id,
            viewer_id=reader,
            allowed_branch_ids={uuid4()},
            allowed_view_branch_ids={context.stock.branch_id},
        )
    async with db_engine.begin() as connection:
        await connection.execute(text("SELECT set_config('app.support_session','true',true)"))
        foreign = await seed_ledger_context(AsyncSession(bind=connection))
    foreign_actor = await seed_receiving_actor(db_engine, maintenance_engine, foreign)
    # A real request has its own identity map. Force an actual RLS-protected fetch.
    db_session.expunge(source.sale)
    await set_receiving_context(db_session, foreign, foreign_actor)
    with pytest.raises(NotFoundError):
        await service.build_receipt(source.sale.id, viewer_id=foreign_actor, can_view_tenant=True)
    await set_receiving_context(db_session, context.stock, context.actor_id)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 4),
        ("schema_version", True),
        ("document_kind", "receipt"),
        ("receipt_number", "Other receipt"),
        ("receipt_sequence", 999999),
        ("is_test", True),
    ],
)
async def test_physical_reader_rejects_unknown_or_mismatched_snapshot(
    db_session: AsyncSession, field: str, value: object
) -> None:
    source = await checkout_source(db_session)
    await put_physical(db_session, source.sale.id, sale_format="unit", quantity=1)
    await complete(db_session, await saved_request(db_session, source.sale))
    original = source.sale.receipt_snapshot
    assert original is not None
    modified = deepcopy(original)
    modified[field] = value
    source.sale.receipt_snapshot = modified
    try:
        with pytest.raises(AurumError):
            physical_receipt_snapshot(source.sale)
    finally:
        source.sale.receipt_snapshot = original
