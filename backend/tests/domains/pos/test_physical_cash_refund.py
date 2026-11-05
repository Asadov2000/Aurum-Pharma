"""Physical cash refunds atomically pay cash and retain returned units in quarantine."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.inventory.unit_stock_v2 import SaleFormatV2
from app.domains.pos.physical_cash_refund_repository import PhysicalCashRefundRepository
from app.domains.pos.physical_cash_refund_schemas import PhysicalCashRefundResultV3
from app.domains.pos.physical_cash_refund_service import PhysicalCashRefundService
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from tests.domains.incoming.test_unit_acceptance import (
    ReceivingRuntime,
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.pos.test_physical_cash_checkout import complete
from tests.domains.pos.test_physical_cash_checkout import facts as checkout_facts
from tests.domains.pos.test_physical_return_journal import receive
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture
REFUND_TABLES = (
    "pos_physical_return_command",
    "pos_physical_return_line",
    "pos_physical_return_slice",
    "pos_physical_cash_refund",
)


async def refund(
    session: AsyncSession,
    sale_id: UUID,
    line_id: UUID,
    shift_id: UUID,
    quantity: int = 1,
    *,
    sale_format: SaleFormatV2 = "unit",
    operation_id: UUID | None = None,
    actor_id: UUID | None = None,
    tenant_id: UUID | None = None,
) -> PhysicalCashRefundResultV3:
    context = runtime(session)
    return await PhysicalCashRefundService(PhysicalCashRefundRepository(session)).refund(
        tenant_id=tenant_id or context.stock.tenant_id,
        actor_id=actor_id or context.actor_id,
        operation_id=operation_id or uuid4(),
        parent_sale_id=sale_id,
        shift_id=shift_id,
        selections=(PhysicalReturnSelection(line_id, sale_format, quantity),),
    )


async def facts(session: AsyncSession) -> dict[str, list[object]]:
    result = await checkout_facts(session)
    del result["audit_log"]
    for table in REFUND_TABLES:
        result[table] = list(
            (
                await session.execute(
                    text(
                        f"SELECT to_jsonb(row) FROM {table} row WHERE tenant_id=:tenant "
                        "ORDER BY to_jsonb(row)::text"
                    ),
                    {"tenant": runtime(session).stock.tenant_id},
                )
            ).scalars()
        )
    return result


async def close_shift(session: AsyncSession, shift_id: UUID) -> None:
    context = runtime(session)
    await POSService(POSRepository(session)).close_shift(
        shift_id=shift_id,
        closing_cash_actual=Decimal("0.00"),
        closed_by_user_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
    )


async def test_inactive_register_denies_new_payout_but_preserves_replay(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    sale_id, line_id, shift_id = source.sale.id, item.id, source.sale.shift_id
    operation_id = uuid4()
    result = await refund(db_session, sale_id, line_id, shift_id, operation_id=operation_id)
    await db_session.execute(
        text("UPDATE register SET is_active=false WHERE id=:id"),
        {"id": source.sale.register_id},
    )
    before = await facts(db_session)
    with pytest.raises(BusinessRuleError):
        await refund(db_session, sale_id, line_id, shift_id)
    assert await facts(db_session) == before
    assert await refund(db_session, sale_id, line_id, shift_id, operation_id=operation_id) == result


@pytest.mark.parametrize(
    "sale_format,quantity,units", [("unit", 2, 2), ("blister", 1, 10), ("pack", 1, 30)]
)
async def test_refund_posts_linked_cash_receipt_without_changing_original_or_stock(
    db_session: AsyncSession, sale_format: SaleFormatV2, quantity: int, units: int
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack", quantity=2)
    await complete(db_session, await saved_request(db_session, source.sale))
    before = await facts(db_session)
    original = await db_session.scalar(
        text("SELECT to_jsonb(sale) FROM sale WHERE id=:id"), {"id": source.sale.id}
    )
    result = await refund(
        db_session, source.sale.id, item.id, source.sale.shift_id, quantity, sale_format=sale_format
    )
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert result.parent_sale_id == source.sale.id
    assert result.sale_id != result.parent_sale_id
    assert result.shift_id == source.sale.shift_id
    assert result.receipt.lines[0].original_line_id == item.id
    assert result.receipt.lines[0].sale_format == sale_format
    assert result.receipt.lines[0].quantity == quantity
    assert result.receipt.lines[0].base_units == units
    assert result.total_amount == result.receipt.lines[0].net_amount
    assert (
        await db_session.scalar(
            text("SELECT to_jsonb(sale) FROM sale WHERE id=:id"), {"id": source.sale.id}
        )
        == original
    )
    row = (
        await db_session.execute(
            text(
                "SELECT parent_sale_id,sale_type,status,total_amount,receipt_snapshot "
                "FROM sale WHERE id=:id"
            ),
            {"id": result.sale_id},
        )
    ).one()
    assert row.parent_sale_id == source.sale.id
    assert row.sale_type == "return"
    assert row.status == "completed"
    assert row.total_amount == result.total_amount
    assert row.receipt_snapshot == result.receipt.model_dump(mode="json")
    payment = (
        await db_session.execute(
            text("SELECT sale_id,payment_method,amount FROM sale_payment WHERE id=:id"),
            {"id": result.payment_id},
        )
    ).one()
    assert payment.sale_id == result.sale_id
    assert payment.payment_method == "cash"
    assert payment.amount == result.total_amount
    after = await facts(db_session)
    for table, rows in before.items():
        if table.startswith("unit_stock_") or table == "pos_physical_checkout":
            assert after[table] == rows
    assert len(after["sale"]) == len(before["sale"]) + 1
    assert len(after["sale_payment"]) == len(before["sale_payment"]) + 1
    assert len(after["pos_physical_cash_refund"]) == 1


async def test_partial_refunds_telescope_historical_net_discount_cost_and_quantity(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(
        db_session, source.sale.id, sale_format="pack", quantity=2, discount=Decimal("0.01")
    )
    original = await complete(db_session, await saved_request(db_session, source.sale))
    parts = [
        await refund(db_session, source.sale.id, item.id, source.sale.shift_id, quantity)
        for quantity in (1, 10, 19, 30)
    ]
    assert [part.receipt.lines[0].offset for part in parts] == [0, 1, 11, 30]
    assert sum((part.total_amount for part in parts), Decimal()) == original.total_amount
    assert sum((part.total_cost_amount for part in parts), Decimal()) == Decimal("20.00")
    assert sum((part.receipt.lines[0].discount_amount for part in parts), Decimal()) == Decimal(
        "0.01"
    )
    assert (
        sum((part.receipt.lines[0].gross_amount for part in parts), Decimal())
        == original.lines[0].gross_amount
    )
    before = await facts(db_session)
    with pytest.raises(BusinessRuleError):
        await refund(db_session, source.sale.id, item.id, source.sale.shift_id)
    assert await facts(db_session) == before


async def test_refund_uses_sale_history_after_catalog_name_and_price_change(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    original = await complete(db_session, await saved_request(db_session, source.sale))
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET brand_name='Changed current name',base_price=999.00 "
            "WHERE id=:id"
        ),
        {"id": runtime(db_session).stock.catalog_id},
    )
    result = await refund(
        db_session, source.sale.id, item.id, source.sale.shift_id, sale_format="pack"
    )
    line = result.receipt.lines[0]
    assert line.name == original.lines[0].name
    assert line.packaging == original.lines[0].packaging
    assert line.original_retail_unit_price == original.lines[0].original_retail_unit_price
    assert result.total_amount == original.total_amount


async def test_replay_after_shift_closure_is_exact_but_changed_payload_conflicts(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    operation_id = uuid4()
    result = await refund(
        db_session, source.sale.id, item.id, source.sale.shift_id, operation_id=operation_id
    )
    await close_shift(db_session, source.sale.shift_id)
    before = await facts(db_session)
    assert (
        await refund(
            db_session, source.sale.id, item.id, source.sale.shift_id, operation_id=operation_id
        )
        == result
    )
    with pytest.raises(ConflictError):
        await refund(
            db_session, source.sale.id, item.id, source.sale.shift_id, 2, operation_id=operation_id
        )
    assert await facts(db_session) == before


async def test_closed_shift_and_wrong_register_cannot_create_cash_refund(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    other = await checkout_source(db_session)
    before = await facts(db_session)
    with pytest.raises((BusinessRuleError, PermissionDeniedError, NotFoundError)):
        await refund(db_session, source.sale.id, item.id, other.sale.shift_id)
    assert await facts(db_session) == before
    await close_shift(db_session, source.sale.shift_id)
    before = await facts(db_session)
    with pytest.raises(BusinessRuleError):
        await refund(db_session, source.sale.id, item.id, source.sale.shift_id)
    assert await facts(db_session) == before


async def test_journal_only_operation_cannot_be_replayed_as_cash_refund(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    operation_id = uuid4()
    await receive(db_session, source.sale.id, item.id, operation_id=operation_id)
    before = await facts(db_session)
    with pytest.raises(ConflictError):
        await refund(
            db_session, source.sale.id, item.id, source.sale.shift_id, operation_id=operation_id
        )
    assert await facts(db_session) == before


async def test_caller_rollback_removes_cash_return_receipt_and_quarantine(
    db_session: AsyncSession,
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    sale_id, line_id, shift_id = source.sale.id, item.id, source.sale.shift_id
    operation_id = uuid4()
    before = await facts(db_session)
    async with db_session.begin_nested() as caller:
        await refund(db_session, sale_id, line_id, shift_id, operation_id=operation_id)
        await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        await caller.rollback()
    assert await facts(db_session) == before
    retry = await refund(db_session, sale_id, line_id, shift_id, operation_id=operation_id)
    assert retry.receipt.lines[0].offset == 0


async def test_late_persistence_failure_rolls_back_payment_receipt_and_quarantine(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    sale_id, line_id, shift_id = source.sale.id, item.id, source.sale.shift_id
    operation_id = uuid4()

    async def refuse_completion(repository: PhysicalCashRefundRepository) -> None:
        assert (
            await repository.session.scalar(
                text("SELECT count(*) FROM sale WHERE sale_type='return' AND status='completed'")
            )
            == 1
        )
        assert await repository.session.scalar(text("SELECT count(*) FROM sale_payment")) == 2
        assert (
            await repository.session.scalar(text("SELECT count(*) FROM pos_physical_cash_refund"))
            == 1
        )
        assert (
            await repository.session.scalar(text("SELECT count(*) FROM pos_physical_return_line"))
            == 1
        )
        raise BusinessRuleError("Simulated refund final persistence refusal")

    before = await facts(db_session)
    with monkeypatch.context() as patch:
        patch.setattr(PhysicalCashRefundRepository, "validate_completion", refuse_completion)
        with pytest.raises(BusinessRuleError, match="final persistence refusal"):
            await refund(db_session, sale_id, line_id, shift_id, operation_id=operation_id)
    assert await facts(db_session) == before
    retry = await refund(db_session, sale_id, line_id, shift_id, operation_id=operation_id)
    assert retry.receipt.lines[0].offset == 0


@pytest.mark.parametrize("permissions", [("pos.sell",), ("pos.refund",)])
async def test_foreign_shift_owner_or_missing_refund_permission_cannot_payout(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    permissions: tuple[str, ...],
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    sale_id, line_id, shift_id = source.sale.id, item.id, source.sale.shift_id
    context = runtime(db_session)
    before = await facts(db_session)
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=permissions
    )
    await set_receiving_context(db_session, context.stock, actor)
    with pytest.raises((PermissionDeniedError, NotFoundError, BusinessRuleError)):
        await refund(db_session, sale_id, line_id, shift_id, actor_id=actor)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await facts(db_session) == before
    with pytest.raises((PermissionDeniedError, NotFoundError)):
        await refund(db_session, sale_id, line_id, shift_id, tenant_id=uuid4())
    assert await facts(db_session) == before


async def test_refund_only_actor_uses_own_new_shift_without_sell_permission(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    original = await complete(db_session, await saved_request(db_session, source.sale))
    sale_id, line_id, shift_id = source.sale.id, item.id, source.sale.shift_id
    await close_shift(db_session, shift_id)
    context = runtime(db_session)
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, context.stock, actor)
    shift = await POSService(POSRepository(db_session)).open_shift(
        tenant_id=context.stock.tenant_id,
        register_id=original.register_id,
        opened_by_user_id=actor,
        opening_cash=Decimal("100.00"),
        allowed_branch_ids={context.stock.branch_id},
    )
    result = await refund(db_session, sale_id, line_id, shift.id, actor_id=actor)
    assert result.actor_id == actor
    assert result.shift_id == shift.id
    assert result.register_id == original.register_id


async def test_real_foreign_tenant_cannot_read_or_replay_cash_refund(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    sale_id, line_id, shift_id = source.sale.id, item.id, source.sale.shift_id
    operation_id = uuid4()
    result = await refund(db_session, sale_id, line_id, shift_id, operation_id=operation_id)
    context = runtime(db_session)
    await db_session.commit()
    async with AsyncSession(db_engine) as support, support.begin():
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        foreign = await seed_ledger_context(support)
    actor = await seed_receiving_actor(db_engine, maintenance_engine, foreign)
    await set_receiving_context(db_session, foreign, actor)
    for table in REFUND_TABLES:
        assert await db_session.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM sale WHERE id=:id"), {"id": result.sale_id}
        )
        == 0
    )
    with pytest.raises((PermissionDeniedError, NotFoundError)):
        await refund(
            db_session,
            sale_id,
            line_id,
            shift_id,
            actor_id=actor,
            tenant_id=foreign.tenant_id,
            operation_id=operation_id,
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await refund(db_session, sale_id, line_id, shift_id, operation_id=operation_id) == result


async def test_cashier_assigned_to_other_branch_cannot_read_refund_or_payout(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    sale_id, line_id, shift_id = source.sale.id, item.id, source.sale.shift_id
    context = runtime(db_session)
    result = await refund(db_session, sale_id, line_id, shift_id)
    before = await facts(db_session)
    await db_session.commit()
    async with AsyncSession(db_engine) as support, support.begin():
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        branch = await FoundationService(FoundationRepository(support)).create_branch(
            tenant_id=context.stock.tenant_id, fields={"name": "Other pharmacy"}
        )
        other_branch_id = branch.id
    foreign = replace(context.stock, branch_id=other_branch_id)
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, foreign, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, foreign, actor)
    for table in REFUND_TABLES:
        assert await db_session.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    assert (
        await PhysicalCashRefundRepository(db_session).get(
            context.stock.tenant_id, result.operation_id
        )
        is None
    )
    with pytest.raises((PermissionDeniedError, NotFoundError)):
        await refund(db_session, sale_id, line_id, shift_id, actor_id=actor)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await facts(db_session) == before


async def test_revoked_assignment_denies_completed_refund_replay_and_new_payout(
    db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="pack")
    await complete(db_session, await saved_request(db_session, source.sale))
    sale_id, line_id, shift_id = source.sale.id, item.id, source.sale.shift_id
    operation_id = uuid4()
    await refund(db_session, sale_id, line_id, shift_id, operation_id=operation_id)
    context = runtime(db_session)
    await db_session.commit()
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text(
                "UPDATE user_assignment SET is_active=false "
                "WHERE user_id=:actor AND tenant_id=:tenant"
            ),
            {"actor": context.actor_id, "tenant": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    for identity in (operation_id, uuid4()):
        with pytest.raises((PermissionDeniedError, NotFoundError)):
            await refund(db_session, sale_id, line_id, shift_id, operation_id=identity)


async def concurrent_refund(
    factory: async_sessionmaker[AsyncSession],
    context: ReceivingRuntime,
    sale_id: UUID,
    line_id: UUID,
    shift_id: UUID,
    operation_id: UUID,
) -> PhysicalCashRefundResultV3 | BusinessRuleError:
    try:
        async with factory.begin() as session:
            session.info["receiving_runtime"] = context
            await set_receiving_context(session, context.stock, context.actor_id)
            return await refund(session, sale_id, line_id, shift_id, operation_id=operation_id)
    except BusinessRuleError as error:
        return error


@pytest.mark.parametrize("same_operation", [False, True])
async def test_concurrent_refunds_never_pay_the_same_last_unit_twice(
    db_session: AsyncSession, same_operation: bool
) -> None:
    source = await checkout_source(db_session)
    item = await put_physical(db_session, source.sale.id, sale_format="unit")
    await complete(db_session, await saved_request(db_session, source.sale))
    sale_id, line_id, shift_id = source.sale.id, item.id, source.sale.shift_id
    context = runtime(db_session)
    await db_session.commit()
    operation_id = uuid4()
    results = await asyncio.wait_for(
        asyncio.gather(
            *(
                concurrent_refund(
                    context.factory,
                    context,
                    sale_id,
                    line_id,
                    shift_id,
                    operation_id if same_operation else uuid4(),
                )
                for _ in range(3)
            )
        ),
        timeout=15,
    )
    winners = [result for result in results if isinstance(result, PhysicalCashRefundResultV3)]
    assert len(winners) == (3 if same_operation else 1)
    assert all(result == winners[0] for result in winners)
    assert sum(isinstance(result, BusinessRuleError) for result in results) == (
        0 if same_operation else 2
    )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    final = await facts(db_session)
    assert len(final["pos_physical_cash_refund"]) == 1
    assert len(final["sale"]) == 2
    assert len(final["sale_payment"]) == 2
