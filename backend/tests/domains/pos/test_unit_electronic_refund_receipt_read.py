"""Saved electronic return receipts use authorized immutable public sale history."""

from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import AurumError, NotFoundError, PermissionDeniedError
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.pos.models import Sale
from app.domains.pos.receipt_pdf import render_receipt_pdf
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.pos.unit_electronic_refund_receipt_read import electronic_refund_snapshot
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.pos.test_physical_cash_refund import close_shift
from tests.domains.pos.test_physical_cash_refund_read import viewer
from tests.domains.pos.test_unit_electronic_refund_posting import post, reconciled
from tests.domains.pos.test_unit_electronic_refund_posting_contracts import receipt as saved_receipt
from tests.domains.pos.test_unit_electronic_refund_preview import facts
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def test_saved_electronic_refund_details_print_and_catalog_rename(
    db_session: AsyncSession,
) -> None:
    from app.domains.pos.router import get_sale

    request, shift_id = await reconciled(db_session)
    posted = await post(db_session, request, shift_id)
    context = runtime(db_session)
    service = POSService(POSRepository(db_session))
    before = await facts(db_session)
    receipt = await service.build_receipt(
        posted.sale_id, viewer_id=context.actor_id, allowed_branch_ids={context.stock.branch_id}
    )
    assert receipt.is_refund is True
    assert receipt.original_receipt_number == posted.receipt.parent_receipt_number
    assert receipt.receipt_number == posted.receipt.receipt_number
    assert receipt.total == posted.total_amount
    assert [(p.method, p.amount) for p in receipt.payments] == [
        (p.method, p.amount) for p in posted.receipt.payments
    ]
    assert {p.method for p in receipt.payments} == {"cash", "card", "qr"}
    saved = posted.receipt.lines[0]
    line = receipt.items[0]
    assert line.name == saved.name
    assert line.qty == 1 and line.unit_label == "возврат"
    assert (line.unit_price, line.discount_amount, line.total_price) == (
        saved.gross_amount,
        saved.discount_amount,
        saved.net_amount,
    )
    assert line.qty * line.unit_price - line.discount_amount == line.total_price
    assert line.quantity_description is not None
    assert "Возвращено:" in line.quantity_description
    assert render_receipt_pdf(receipt).startswith(b"%PDF-")
    details = await get_sale(
        posted.sale_id,
        viewer(
            context.actor_id, context.stock.tenant_id, context.stock.branch_id, "sales.view.own"
        ),
        service,
    )
    assert details.items == []
    assert details.physical_items == receipt.items
    assert {p.payment_method: p.amount for p in details.payments} == {
        p.method: p.amount for p in receipt.payments
    }
    assert await facts(db_session) == before
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET brand_name='Renamed after refund',base_price=999 "
            "WHERE id=:id"
        ),
        {"id": context.stock.catalog_id},
    )
    assert await service.build_receipt(posted.sale_id) == receipt


@pytest.mark.parametrize("permission", ["sales.view.own", "sales.view.tenant"])
async def test_sale_reader_needs_no_refund_permission_or_private_journal_reads(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    permission: str,
) -> None:
    from app.domains.pos.router import get_sale

    request, shift_id = await reconciled(db_session)
    posted = await post(db_session, request, shift_id)
    context = runtime(db_session)
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=(permission,)
    )
    if permission == "sales.view.own":
        async with db_engine.begin() as support:
            await support.execute(text("SELECT set_config('app.support_session','true',true)"))
            await support.execute(
                text(
                    "UPDATE user_assignment SET role_id=(SELECT role_id FROM user_assignment "
                    "WHERE user_id=:reader AND tenant_id=:tenant AND branch_id=:branch) "
                    "WHERE user_id=:author AND tenant_id=:tenant AND branch_id=:branch"
                ),
                {
                    "reader": actor,
                    "author": context.actor_id,
                    "tenant": context.stock.tenant_id,
                    "branch": context.stock.branch_id,
                },
            )
        actor = context.actor_id
    db_session.expunge_all()
    await set_receiving_context(db_session, context.stock, actor)
    assert (
        await db_session.scalar(
            text("SELECT tenant_actor_has_scoped_permission(:tenant,'pos.refund',:branch)"),
            {"tenant": context.stock.tenant_id, "branch": context.stock.branch_id},
        )
        is False
    )
    for table in (
        "pos_unit_electronic_refund_posting",
        "pos_unit_electronic_refund_outcome",
        "pos_physical_return_line",
        "pos_physical_return_slice",
    ):
        assert await db_session.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    statements: list[str] = []

    def capture(connection, cursor, statement, parameters, execution_context, executemany):
        statements.append(statement.lower())

    event.listen(db_engine.sync_engine, "before_cursor_execute", capture)
    try:
        service = POSService(POSRepository(db_session))
        receipt = await service.build_receipt(
            posted.sale_id,
            viewer_id=actor,
            allowed_branch_ids={context.stock.branch_id},
            allowed_view_branch_ids=(
                {context.stock.branch_id} if permission.endswith("tenant") else set()
            ),
        )
        details = await get_sale(
            posted.sale_id,
            viewer(actor, context.stock.tenant_id, context.stock.branch_id, permission),
            service,
        )
    finally:
        event.remove(db_engine.sync_engine, "before_cursor_execute", capture)
    assert receipt.total == posted.total_amount
    assert details.physical_items == receipt.items
    assert "cost" not in receipt.model_dump_json()
    for statement in statements:
        assert not any(
            table in statement
            for table in (
                "pos_unit_electronic_refund_posting",
                "pos_unit_electronic_refund_outcome",
                "pos_physical_return_line",
                "pos_physical_return_slice",
            )
        )
    if permission.endswith("tenant"):
        with pytest.raises(PermissionDeniedError):
            await service.build_receipt(
                posted.sale_id, viewer_id=actor, allowed_branch_ids={context.stock.branch_id}
            )


@pytest.mark.parametrize("same_tenant", [False, True])
async def test_other_branch_or_tenant_cannot_discover_saved_electronic_refund(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    same_tenant: bool,
) -> None:
    from app.domains.pos.router import get_sale

    request, shift_id = await reconciled(db_session)
    posted = await post(db_session, request, shift_id)
    context = runtime(db_session)
    await db_session.commit()
    async with AsyncSession(db_engine) as support, support.begin():
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        if same_tenant:
            branch = await FoundationService(FoundationRepository(support)).create_branch(
                tenant_id=context.stock.tenant_id,
                fields={"name": "Electronic receipt other branch"},
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
    denied = PermissionDeniedError if same_tenant else NotFoundError
    with pytest.raises(denied):
        await service.build_receipt(
            posted.sale_id,
            viewer_id=actor,
            allowed_branch_ids={foreign.branch_id},
            allowed_view_branch_ids={foreign.branch_id},
        )
    with pytest.raises(denied):
        await get_sale(
            posted.sale_id,
            viewer(actor, foreign.tenant_id, foreign.branch_id, "sales.view.tenant"),
            service,
        )


def saved_sale() -> Sale:
    receipt = saved_receipt()
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


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 4),
        ("schema_version", True),
        ("receipt_number", "Wrong"),
        ("receipt_sequence", 1),
        pytest.param("parent_sale_id", str(uuid4()), id="different-parent-sale"),
        ("completed_at", "2026-10-19T12:00:00Z"),
        ("is_test", True),
        ("currency", "USD"),
        ("total_amount", "4.00"),
        ("payments", []),
    ],
)
def test_pure_corrupt_saved_electronic_receipt_fails_closed(field: str, value: object) -> None:
    sale = saved_sale()
    payload = deepcopy(sale.receipt_snapshot)
    assert payload is not None
    payload[field] = value
    sale.receipt_snapshot = payload
    with pytest.raises(AurumError):
        electronic_refund_snapshot(sale)
    with pytest.raises(AurumError):
        POSService.get_physical_receipt_items(sale)
    with pytest.raises(AurumError):
        POSService.get_receipt_item_names(sale)


@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "draft"),
        ("sale_type", "sale"),
        ("parent_sale_id", None),
    ],
)
def test_pure_electronic_snapshot_requires_completed_linked_return(
    field: str, value: object
) -> None:
    sale = saved_sale()
    setattr(sale, field, value)
    with pytest.raises(AurumError):
        electronic_refund_snapshot(sale)


async def test_closed_shift_readonly_tenant_preserves_receipt_recovery(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
) -> None:
    request, shift_id = await reconciled(db_session)
    posted = await post(db_session, request, shift_id)
    service = POSService(POSRepository(db_session))
    expected = await service.build_receipt(posted.sale_id)
    await close_shift(db_session, shift_id)
    context = runtime(db_session)
    await db_session.commit()
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:id"),
            {"id": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await facts(db_session)
    recovered = await service.build_receipt(
        posted.sale_id,
        viewer_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
    )
    assert recovered == expected
    assert render_receipt_pdf(recovered).startswith(b"%PDF-")
    assert await facts(db_session) == before


def test_pure_saved_electronic_adapter_preserves_exact_money_and_names() -> None:
    sale = saved_sale()
    receipt = electronic_refund_snapshot(sale)
    assert receipt is not None
    assert receipt.model_dump(mode="json") == sale.receipt_snapshot
    assert receipt is not None
    assert POSService.get_receipt_item_names(sale) == {1: receipt.lines[0].name}
    line = POSService.get_physical_receipt_items(sale)[0]
    assert line.qty == 1
    assert line.unit_price - line.discount_amount == line.total_price == receipt.total_amount
    assert receipt.payments[0].method == "card"
    assert receipt.payments[0].amount == receipt.total_amount
