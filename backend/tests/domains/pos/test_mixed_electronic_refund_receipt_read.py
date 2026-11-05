"""Authorized mixed electronic refund reads preserve immutable retail history."""

from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import AurumError, NotFoundError, PermissionDeniedError
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.pos.mixed_electronic_refund_posting_schemas import MixedElectronicRefundReceiptV4
from app.domains.pos.mixed_electronic_refund_receipt_read import mixed_electronic_refund_snapshot
from app.domains.pos.models import Sale
from app.domains.pos.receipt_pdf import render_receipt_pdf
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.pos.test_mixed_cash_refund_read import saved_mixed_refund_sale
from tests.domains.pos.test_mixed_electronic_refund_posting import facts, post, reconciled
from tests.domains.pos.test_physical_cash_refund import close_shift
from tests.domains.pos.test_physical_cash_refund_read import viewer
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture
PRIVATE_TABLES = (
    "pos_mixed_electronic_refund_intent",
    "pos_mixed_electronic_refund_binding",
    "pos_mixed_electronic_refund_activation",
    "pos_mixed_electronic_refund_outcome",
    "pos_mixed_electronic_refund_posting",
    "pos_mixed_return_command",
    "pos_mixed_return_line",
    "pos_mixed_return_slice",
    "pos_unit_refund_payment_component",
)


def assert_public_payload(payload: str) -> None:
    for private in ("cost", "terminal_id", "document_number", "original_payment_id"):
        assert private not in payload


async def test_saved_mixed_electronic_refund_details_print_pdf_and_catalog_rename(
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
    assert receipt.datetime is not None and receipt.datetime.utcoffset() == timedelta(hours=5)
    assert {line.inventory_kind for line in posted.receipt.lines} == {
        "legacy_pack",
        "physical_unit",
    }
    assert [line.position for line in receipt.items] == [1, 2]
    assert [(part.method, part.amount) for part in receipt.payments] == [
        (part.method, part.amount) for part in posted.receipt.payments
    ]
    assert {part.method for part in receipt.payments} == {"cash", "card", "qr"}
    for line, saved in zip(receipt.items, posted.receipt.lines, strict=True):
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
    assert_public_payload(receipt.model_dump_json())
    assert render_receipt_pdf(receipt).startswith(b"%PDF-")
    details = await get_sale(
        posted.sale_id,
        viewer(
            context.actor_id, context.stock.tenant_id, context.stock.branch_id, "sales.view.own"
        ),
        service,
    )
    assert details.items == details.physical_items == details.prepared_cart_items == []
    assert details.mixed_items == receipt.items
    assert {part.payment_method: part.amount for part in details.payments} == {
        part.method: part.amount for part in receipt.payments
    }
    assert_public_payload(details.model_dump_json())
    assert await facts(db_session) == before
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET brand_name='Renamed after mixed return',base_price=999 "
            "WHERE tenant_id=:tenant"
        ),
        {"tenant": context.stock.tenant_id},
    )
    assert await service.build_receipt(posted.sale_id) == receipt


@pytest.mark.parametrize("permission", ["sales.view.own", "sales.view.tenant"])
async def test_sale_permission_reads_without_refund_grant_or_private_journal_queries(
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
    for table in PRIVATE_TABLES:
        assert await db_session.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    before = await facts(db_session)
    statements: list[str] = []

    def capture(
        _connection: object,
        _cursor: object,
        statement: str,
        _parameters: object,
        _execution_context: object,
        _executemany: bool,
    ) -> None:
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
    assert details.mixed_items == receipt.items
    assert_public_payload(receipt.model_dump_json())
    assert_public_payload(details.model_dump_json())
    assert all(not any(table in statement for table in PRIVATE_TABLES) for statement in statements)
    if permission.endswith("tenant"):
        with pytest.raises(PermissionDeniedError):
            await service.build_receipt(
                posted.sale_id, viewer_id=actor, allowed_branch_ids={context.stock.branch_id}
            )
    assert await facts(db_session) == before


@pytest.mark.parametrize("same_tenant", [False, True], ids=["other-tenant", "other-branch"])
async def test_foreign_scope_cannot_discover_saved_mixed_electronic_refund(
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
                tenant_id=context.stock.tenant_id, fields={"name": "Other mixed receipt pharmacy"}
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
            posted.sale_id,
            viewer_id=actor,
            allowed_branch_ids={foreign.branch_id},
            allowed_view_branch_ids={foreign.branch_id},
        )
    with pytest.raises(denial):
        await get_sale(
            posted.sale_id,
            viewer(actor, foreign.tenant_id, foreign.branch_id, "sales.view.tenant"),
            service,
        )


async def test_closed_shift_readonly_tenant_keeps_mixed_electronic_receipt_recovery(
    db_session: AsyncSession, db_engine: AsyncEngine
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


def saved_sale() -> Sale:
    sale = saved_mixed_refund_sale()
    assert sale.receipt_snapshot is not None
    values = deepcopy(sale.receipt_snapshot)
    values.pop("payment_method")
    values["document_kind"] = "mixed_electronic_refund_receipt"
    values["payments"] = [
        {
            "id": UUID(int=100 + index),
            "original_payment_id": UUID(int=200 + index),
            "method": method,
            "amount": amount,
            "terminal_id": None if method == "cash" else "synthetic-terminal",
            "document_number": None if method == "cash" else "synthetic-document",
        }
        for index, (method, amount) in enumerate(
            [("cash", "1.00"), ("card", "2.00"), ("qr", "1.00")]
        )
    ]
    receipt = MixedElectronicRefundReceiptV4.model_validate(values)
    sale.receipt_snapshot = receipt.model_dump(mode="json")
    return sale


def test_pure_saved_mixed_electronic_adapter_keeps_order_fractional_quantity_and_money() -> None:
    sale = saved_sale()
    receipt = mixed_electronic_refund_snapshot(sale)
    assert receipt is not None
    assert receipt.model_dump(mode="json") == sale.receipt_snapshot
    lines = POSService.get_mixed_receipt_items(sale)
    assert [line.position for line in lines] == [1, 2]
    assert lines[0].total_price == Decimal("0.01")
    assert lines[1].unit_price == Decimal("4.00")
    assert lines[1].discount_amount == Decimal("0.01")
    assert lines[1].total_price == Decimal("3.99")
    assert all(line.qty == 1 for line in lines)
    assert "0.001 уп." in (lines[0].quantity_description or "")
    assert "2 табл." in (lines[1].quantity_description or "")
    assert [(part.method, part.amount) for part in receipt.payments] == [
        ("cash", Decimal("1.00")),
        ("card", Decimal("2.00")),
        ("qr", Decimal("1.00")),
    ]
    assert POSService.get_receipt_item_names(sale) == {}
    assert POSService.get_physical_receipt_items(sale) == []


@pytest.mark.parametrize(
    "field,value",
    [
        pytest.param("schema_version", 3, id="version-three"),
        pytest.param("schema_version", True, id="boolean-version"),
        pytest.param("receipt_number", "Wrong", id="receipt-number-mismatch"),
        pytest.param("receipt_sequence", 1, id="sequence-mismatch"),
        pytest.param("parent_sale_id", str(UUID(int=999)), id="parent-mismatch"),
        pytest.param("completed_at", "2026-10-19T12:00:00Z", id="completion-mismatch"),
        pytest.param("is_test", True, id="test-mode"),
        pytest.param("currency", "USD", id="currency"),
        pytest.param("total_amount", "4.01", id="total-mismatch"),
        pytest.param("physical_cost_amount", "0.01", id="private-cost"),
        pytest.param("lines", [], id="empty-lines"),
        pytest.param("payments", [], id="empty-payments"),
    ],
)
def test_pure_corrupt_mixed_electronic_snapshot_never_falls_back(field: str, value: object) -> None:
    sale = saved_sale()
    payload = deepcopy(sale.receipt_snapshot)
    assert payload is not None
    payload[field] = value
    sale.receipt_snapshot = payload
    with pytest.raises(AurumError):
        mixed_electronic_refund_snapshot(sale)
    for getter in (
        POSService.get_mixed_receipt_items,
        POSService.get_receipt_item_names,
        POSService.get_physical_receipt_items,
    ):
        with pytest.raises(AurumError):
            getter(sale)


@pytest.mark.parametrize("field", ["receipt_number", "parent_receipt_number", "line-name"])
@pytest.mark.parametrize(
    "invalid", ["", "bad\nname", "bad\x00name"], ids=["empty", "newline", "nul"]
)
def test_pure_saved_printable_text_is_required(field: str, invalid: str) -> None:
    sale = saved_sale()
    payload = deepcopy(sale.receipt_snapshot)
    assert payload is not None
    if field == "line-name":
        payload["lines"][0]["name"] = invalid
    else:
        payload[field] = invalid
    sale.receipt_snapshot = payload
    with pytest.raises(AurumError):
        POSService.get_mixed_receipt_items(sale)


@pytest.mark.parametrize(
    "field,value",
    [
        pytest.param("status", "draft", id="draft"),
        pytest.param("sale_type", "sale", id="not-return"),
        pytest.param("parent_sale_id", None, id="unlinked"),
    ],
)
def test_pure_mixed_electronic_snapshot_requires_completed_linked_return(
    field: str, value: object
) -> None:
    sale = saved_sale()
    setattr(sale, field, value)
    with pytest.raises(AurumError):
        mixed_electronic_refund_snapshot(sale)


def test_pure_unknown_versioned_kind_does_not_become_legacy_receipt() -> None:
    sale = saved_sale()
    payload = deepcopy(sale.receipt_snapshot)
    assert payload is not None
    payload["document_kind"] = "unknown_refund_receipt"
    sale.receipt_snapshot = payload
    assert mixed_electronic_refund_snapshot(sale) is None
    with pytest.raises(AurumError):
        POSService.get_receipt_item_names(sale)
