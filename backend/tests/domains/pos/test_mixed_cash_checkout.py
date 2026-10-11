"""Real app-role mixed cash checkout keeps both stock ledgers and payment atomic."""

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import Connection, ExecutionContext
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.inventory.repository import InventoryRepository
from app.domains.inventory.unit_stock_v2 import SaleFormatV2
from app.domains.pos.mixed_cash_checkout_models import MixedCashCheckout
from app.domains.pos.mixed_cash_checkout_repository import MixedCashCheckoutRepository
from app.domains.pos.mixed_cash_checkout_service import MixedCashCheckoutService
from app.domains.pos.mixed_documents import MixedCheckoutResultV4, MixedReceiptDocumentV4
from app.domains.pos.models import Sale
from app.domains.pos.physical_documents import PhysicalResultLineV3
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from tests.domains.incoming.test_unit_acceptance import (
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.inventory.test_unit_ledger_v2_dispensing import cart_line, dispense, remaining
from tests.domains.pos.test_mixed_physical_checkout_preparation import (
    legacy_with_stock,
    second_legacy,
)
from tests.domains.pos.test_mixed_physical_checkout_preparation import facts as mixed_facts
from tests.domains.pos.test_unit_physical_cart import physical_request, put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import (
    checkout_source,
    saved_request,
)
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def facts(session: AsyncSession) -> dict[str, list[object]]:
    result = await mixed_facts(session)
    result["pos_mixed_checkout"] = list(
        (
            await session.execute(
                text(
                    "SELECT to_jsonb(row) FROM pos_mixed_checkout row WHERE tenant_id=:tenant "
                    "ORDER BY to_jsonb(row)::text"
                ),
                {"tenant": runtime(session).stock.tenant_id},
            )
        ).scalars()
    )
    return result


async def complete(
    session: AsyncSession, request: CashConfirmationRequestV2
) -> MixedCheckoutResultV4:
    context = runtime(session)
    return await MixedCashCheckoutService(MixedCashCheckoutRepository(session)).complete(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        request=request,
    )


@pytest.mark.parametrize(
    "sale_format,quantity,units", [("pack", 1, 30), ("blister", 2, 20), ("unit", 5, 5)]
)
async def test_mixed_cash_posts_fractional_pack_and_physical_format_once(
    db_session: AsyncSession, sale_format: SaleFormatV2, quantity: int, units: int
) -> None:
    source = await checkout_source(db_session)
    legacy = await legacy_with_stock(db_session, source.sale)
    physical = await put_physical(
        db_session, source.sale.id, position=3, sale_format=sale_format, quantity=quantity
    )
    request = await saved_request(db_session, source.sale)
    result = await complete(db_session, request)
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert result.sale_id == source.sale.id
    assert result.operation_id == result.stock_command_id == request.operation_id
    assert tuple(line.id for line in result.lines) == (legacy.id, physical.id)
    assert tuple(line.position for line in result.lines) == (1, 2)
    assert tuple(line.inventory_kind for line in result.lines) == ("legacy_pack", "physical_unit")
    assert result.lines[0].quantity == Decimal("0.001")
    assert result.lines[0].gross_amount == Decimal("0.02")
    assert isinstance(result.lines[1], PhysicalResultLineV3)
    assert result.lines[1].quantity == quantity
    assert result.lines[1].base_units == units
    assert result.total_amount == request.expected_total_amount
    assert result.payments[0].amount == result.total_amount
    assert await remaining(db_session, source.stock) == 60 - units
    assert await db_session.scalar(
        text("SELECT qty_remaining FROM batch WHERE id=:id"), {"id": legacy.batch_id}
    ) == Decimal("0.000")
    movement = (
        await db_session.execute(
            text(
                "SELECT batch_id,qty_delta,operation_key FROM batch_movement "
                "WHERE source_table='sale' AND source_id=:id AND movement_type='sale'"
            ),
            {"id": source.sale.id},
        )
    ).one()
    assert movement.batch_id == legacy.batch_id
    assert movement.qty_delta == -legacy.qty
    assert movement.operation_key == f"pos:sale:{source.sale.id}:sale:{legacy.batch_id}"
    command = (
        await db_session.execute(
            text(
                "SELECT checkout_sale_id,mixed_checkout_sale_id FROM unit_stock_cart_command_v2 "
                "WHERE id=:id"
            ),
            {"id": request.operation_id},
        )
    ).one()
    assert command.checkout_sale_id is None
    assert command.mixed_checkout_sale_id == source.sale.id
    for table in ("sale_payment", "pos_mixed_checkout"):
        assert await db_session.scalar(
            text(f"SELECT count(*) FROM {table} WHERE sale_id=:id"), {"id": source.sale.id}
        ) == 1
    assert await db_session.scalar(
        text("SELECT count(*) FROM pos_physical_checkout WHERE sale_id=:id"),
        {"id": source.sale.id},
    ) == 0
    row = (
        await db_session.execute(
            text("SELECT status,total_amount,receipt_snapshot FROM sale WHERE id=:id"),
            {"id": source.sale.id},
        )
    ).one()
    assert row.status == "completed"
    assert row.total_amount == result.total_amount
    receipt = MixedReceiptDocumentV4.model_validate(row.receipt_snapshot)
    assert tuple(line.quantity for line in receipt.lines) == (Decimal("0.001"), quantity)
    before = await facts(db_session)
    assert await complete(db_session, request) == result
    assert await facts(db_session) == before


async def test_repeated_legacy_rows_create_one_aggregate_batch_movement(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    first = await legacy_with_stock(db_session, source.sale)
    second = await second_legacy(db_session, source.sale, first)
    await InventoryRepository(db_session).insert_movement(
        tenant_id=source.sale.tenant_id,
        batch_id=first.batch_id,
        movement_type="incoming",
        qty_delta=Decimal("0.001"),
    )
    physical = await put_physical(db_session, source.sale.id, position=3)
    result = await complete(db_session, await saved_request(db_session, source.sale))
    assert tuple(line.id for line in result.lines) == (first.id, second.id, physical.id)
    movements = (
        await db_session.execute(
            text(
                "SELECT qty_delta FROM batch_movement WHERE source_table='sale' "
                "AND source_id=:id AND movement_type='sale'"
            ),
            {"id": source.sale.id},
        )
    ).scalars().all()
    assert movements == [Decimal("-0.002")]
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


async def test_replay_retains_historical_names_prices_and_rejects_changed_tender(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    legacy = await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    original = await complete(db_session, request)
    await db_session.execute(
        text("UPDATE tenant_catalog SET brand_name='Later name',base_price=999.00 WHERE id=:id"),
        {"id": legacy.catalog_id},
    )
    await db_session.execute(
        text("UPDATE batch SET is_blocked=true WHERE id=:id"), {"id": legacy.batch_id}
    )
    before = await facts(db_session)
    assert await complete(db_session, request) == original
    with pytest.raises(ConflictError):
        await complete(
            db_session, request.model_copy(update={"cash_tendered": request.cash_tendered + 1})
        )
    assert await facts(db_session) == before


@pytest.mark.parametrize("mode", ["mixed", "legacy_only", "physical_only"])
async def test_mixed_confirmation_accepts_only_both_inventory_kinds(
    db_session: AsyncSession, mode: str
) -> None:
    source = await checkout_source(db_session)
    if mode != "physical_only":
        await legacy_with_stock(db_session, source.sale)
    if mode != "legacy_only":
        await put_physical(db_session, source.sale.id)
    request = await physical_request(db_session, source.sale.id)
    context = runtime(db_session)
    before = await facts(db_session)
    service = MixedCashCheckoutService(MixedCashCheckoutRepository(db_session))
    if mode == "mixed":
        saved = await service.save_intent(
            tenant_id=context.stock.tenant_id,
            actor_id=context.actor_id,
            allowed_branch_ids={context.stock.branch_id},
            request=request,
        )
        assert saved.confirmation.quote.total_amount == request.expected_total_amount
        after = await facts(db_session)
        assert len(after["pos_unit_cash_intent"]) == 1
        before.pop("pos_unit_cash_intent")
        after.pop("pos_unit_cash_intent")
        # Saving an intent may audit the intent; no stock, payment or completion occurs.
        before.pop("audit_log")
        after.pop("audit_log")
        assert after == before
    else:
        with pytest.raises((BusinessRuleError, ConflictError)):
            await service.save_intent(
                tenant_id=context.stock.tenant_id,
                actor_id=context.actor_id,
                allowed_branch_ids={context.stock.branch_id},
                request=request,
            )
        assert await facts(db_session) == before


@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize("scope", ["actor", "actual_actor", "tenant", "branch"])
async def test_checkout_and_replay_require_actual_cashier_and_allowed_scope(
    db_session: AsyncSession, completed: bool, scope: str
) -> None:
    source = await checkout_source(db_session)
    await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    if completed:
        await complete(db_session, request)
    context = runtime(db_session)
    before = await facts(db_session)
    if scope == "actual_actor":
        await set_receiving_context(db_session, context.stock, uuid4())
    with pytest.raises((PermissionDeniedError, NotFoundError)):
        await MixedCashCheckoutService(MixedCashCheckoutRepository(db_session)).complete(
            tenant_id=uuid4() if scope == "tenant" else context.stock.tenant_id,
            actor_id=uuid4() if scope == "actor" else context.actor_id,
            allowed_branch_ids={uuid4() if scope == "branch" else context.stock.branch_id},
            request=request,
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await facts(db_session) == before


@pytest.mark.parametrize(
    "failure",
    ["missing_intent", "stale_price", "legacy_shortage", "physical_shortage", "test_sale"],
)
async def test_rejected_checkout_preserves_both_ledgers_and_cash(
    db_session: AsyncSession, failure: str
) -> None:
    source = await checkout_source(db_session, quantity=1)
    legacy = await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id, sale_format="pack")
    request = await physical_request(db_session, source.sale.id)
    if failure != "missing_intent":
        request = await saved_request(db_session, source.sale)
    if failure == "stale_price":
        await db_session.execute(
            text("UPDATE tenant_catalog SET base_price=999.00 WHERE id=:id"),
            {"id": legacy.catalog_id},
        )
    elif failure == "legacy_shortage":
        await InventoryRepository(db_session).insert_movement(
            tenant_id=source.sale.tenant_id,
            batch_id=legacy.batch_id,
            movement_type="write_off",
            qty_delta=-legacy.qty,
        )
    elif failure == "physical_shortage":
        await dispense(db_session, source.stock, (cart_line(source.stock, "pack", 1),))
    elif failure == "test_sale":
        await db_session.execute(
            text("UPDATE sale SET is_test=true WHERE id=:id"), {"id": source.sale.id}
        )
        await db_session.refresh(source.sale)
    before = await facts(db_session)
    with pytest.raises((BusinessRuleError, ConflictError, NotFoundError)):
        await complete(db_session, request)
    assert await facts(db_session) == before


async def test_caller_rollback_restores_both_stocks_payment_and_receipt_sequence(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    before = await facts(db_session)
    async with db_session.begin_nested() as caller:
        await complete(db_session, request)
        assert await remaining(db_session, source.stock) == 59
        await caller.rollback()
    assert await facts(db_session) == before


async def test_final_completion_failure_rolls_back_both_postings_and_cash(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = await checkout_source(db_session)
    legacy = await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)

    async def refuse(
        repository: MixedCashCheckoutRepository,
        sale: Sale,
        receipt: MixedReceiptDocumentV4,
        completed_at: datetime,
    ) -> None:
        assert await remaining(repository.session, source.stock) == 59
        assert await repository.session.scalar(
            text("SELECT qty_remaining FROM batch WHERE id=:id"), {"id": legacy.batch_id}
        ) == Decimal("0.000")
        for table in ("sale_payment", "pos_mixed_checkout"):
            assert await repository.session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE sale_id=:id"), {"id": sale.id}
            ) == 1
        assert receipt.total_amount == request.expected_total_amount
        assert completed_at.tzinfo is not None
        raise BusinessRuleError("Simulated mixed final refusal")

    monkeypatch.setattr(MixedCashCheckoutRepository, "complete_sale", refuse)
    before = await facts(db_session)
    with pytest.raises(BusinessRuleError, match="mixed final refusal"):
        await complete(db_session, request)
    assert await facts(db_session) == before


@pytest.mark.parametrize(
    "forgery",
    [
        "version",
        "kind",
        "total",
        "legacy_quantity",
        "physical_units",
        "position",
        "receipt",
        "scope",
    ],
)
async def test_database_rejects_forged_mixed_marker_and_rolls_back_every_posting(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, forgery: str
) -> None:
    source = await checkout_source(db_session)
    await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    insert = MixedCashCheckoutRepository.insert

    async def forge(repository: MixedCashCheckoutRepository, marker: MixedCashCheckout) -> None:
        payload = deepcopy(marker.result_payload)
        lines = cast(list[dict[str, object]], payload["lines"])
        if forgery == "version":
            payload["schema_version"] = 3
        elif forgery == "kind":
            payload["document_kind"] = "physical_checkout"
        elif forgery == "total":
            payload["total_amount"] = "999.00"
        elif forgery == "legacy_quantity":
            lines[0]["quantity"] = "0.002"
        elif forgery == "physical_units":
            lines[1]["base_units"] = 2
        elif forgery == "position":
            lines[1]["position"] = 1
        elif forgery == "receipt":
            marker.receipt_payload = {**marker.receipt_payload, "receipt_number": "forged"}
        else:
            payload["branch_id"] = str(uuid4())
        marker.result_payload = payload
        await insert(repository, marker)

    monkeypatch.setattr(MixedCashCheckoutRepository, "insert", forge)
    before = await facts(db_session)
    with pytest.raises(DBAPIError):
        await complete(db_session, request)
    assert await facts(db_session) == before


@pytest.mark.parametrize("missing", ["marker", "payment"])
async def test_database_rejects_incomplete_mixed_financial_bundle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    source = await checkout_source(db_session)
    await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)

    async def skip_marker(
        repository: MixedCashCheckoutRepository, marker: MixedCashCheckout
    ) -> None:
        assert marker.sale_id == source.sale.id

    async def skip_payment(
        repository: MixedCashCheckoutRepository,
        *,
        tenant_id: UUID,
        sale_id: UUID,
        operation_id: UUID,
        request_hash: str,
        amount: Decimal,
    ) -> None:
        assert tenant_id == source.sale.tenant_id
        assert sale_id == source.sale.id
        assert operation_id == request.operation_id
        assert len(request_hash) == 64
        assert amount == request.expected_total_amount

    if missing == "marker":
        monkeypatch.setattr(MixedCashCheckoutRepository, "insert", skip_marker)
    else:
        monkeypatch.setattr(MixedCashCheckoutRepository, "insert_payment", skip_payment)
    before = await facts(db_session)
    with pytest.raises(DBAPIError):
        await complete(db_session, request)
    assert await facts(db_session) == before


async def test_real_foreign_tenant_cannot_read_or_replay_mixed_marker(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    source = await checkout_source(db_session)
    await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    original = await complete(db_session, request)
    async with AsyncSession(db_engine) as support:
        async with support.begin():
            await support.execute(text("SELECT set_config('app.support_session','true',true)"))
            foreign = await seed_ledger_context(support)
    foreign_actor = await seed_receiving_actor(db_engine, maintenance_engine, foreign)
    await set_receiving_context(db_session, foreign, foreign_actor)
    assert await db_session.scalar(
        text("SELECT count(*) FROM pos_mixed_checkout WHERE id=:id"), {"id": request.operation_id}
    ) == 0
    assert await MixedCashCheckoutRepository(db_session).get(
        original.tenant_id, request.operation_id
    ) is None
    with pytest.raises(NotFoundError):
        await MixedCashCheckoutService(MixedCashCheckoutRepository(db_session)).complete(
            tenant_id=foreign.tenant_id,
            actor_id=foreign_actor,
            allowed_branch_ids={foreign.branch_id},
            request=request,
        )
    context = runtime(db_session)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await complete(db_session, request) == original


@pytest.mark.parametrize("action", ["UPDATE", "DELETE"])
async def test_completed_mixed_marker_is_immutable(
    db_session: AsyncSession, action: str
) -> None:
    source = await checkout_source(db_session)
    await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    await complete(db_session, request)
    statement = (
        "UPDATE pos_mixed_checkout SET request_hash=repeat('a',64) WHERE id=:id"
        if action == "UPDATE"
        else "DELETE FROM pos_mixed_checkout WHERE id=:id"
    )
    before = await facts(db_session)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(text(statement), {"id": request.operation_id})
            await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert await facts(db_session) == before


@pytest.mark.parametrize("method", ["save_intent", "complete"])
@pytest.mark.parametrize("pending", ["dirty", "new", "deleted"])
async def test_pending_orm_state_is_rejected_before_implicit_flush(
    db_session: AsyncSession, method: str, pending: str
) -> None:
    source = await checkout_source(db_session)
    legacy = await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    context = runtime(db_session)
    service = MixedCashCheckoutService(MixedCashCheckoutRepository(db_session))
    before = await facts(db_session)
    original = source.sale.is_test
    unsaved = Sale(id=uuid4())
    if pending == "dirty":
        source.sale.is_test = not original
    elif pending == "new":
        db_session.add(unsaved)
    else:
        await db_session.delete(legacy)
    statements: list[str] = []

    def record_statement(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        execution_context: ExecutionContext,
        executemany: bool,
    ) -> None:
        statements.append(statement)

    engine = db_session.bind
    assert isinstance(engine, AsyncEngine)
    event.listen(engine.sync_engine, "before_cursor_execute", record_statement)
    try:
        with pytest.raises(ConflictError):
            if method == "complete":
                await complete(db_session, request)
            else:
                await service.save_intent(
                    tenant_id=context.stock.tenant_id,
                    actor_id=context.actor_id,
                    allowed_branch_ids={context.stock.branch_id},
                    request=request,
                )
        assert statements == []
        event.remove(engine.sync_engine, "before_cursor_execute", record_statement)
        with db_session.no_autoflush:
            assert await facts(db_session) == before
            assert await db_session.scalar(
                text("SELECT is_test FROM sale WHERE id=:id"), {"id": source.sale.id}
            ) == original
            assert await db_session.scalar(
                text("SELECT count(*) FROM sale_item WHERE id=:id"), {"id": legacy.id}
            ) == 1
    finally:
        if event.contains(engine.sync_engine, "before_cursor_execute", record_statement):
            event.remove(engine.sync_engine, "before_cursor_execute", record_statement)
        if pending == "new":
            db_session.expunge(unsaved)
        elif pending == "deleted":
            db_session.add(legacy)
        source.sale.is_test = original
        await db_session.refresh(source.sale)
