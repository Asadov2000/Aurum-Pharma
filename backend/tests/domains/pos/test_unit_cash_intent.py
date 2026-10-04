"""Cashier intent persistence with actual runtime-role isolation, never payments."""

import asyncio
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import ConflictError, PermissionDeniedError
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from app.domains.pos.unit_cash_intent_models import UnitCashIntent
from app.domains.pos.unit_cash_intent_repository import UnitCashIntentRepository
from app.domains.pos.unit_cash_intent_service import SavedCashIntentV2, UnitCashIntentService
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.pos.test_unit_cart_quote import quote
from tests.domains.pos.test_unit_cash_confirmation import confirmation_request
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def save(session: AsyncSession, request: CashConfirmationRequestV2) -> SavedCashIntentV2:
    context = runtime(session)
    return await UnitCashIntentService(UnitCashIntentRepository(session)).save(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        request=request,
    )


async def test_saved_intent_is_repeatable_and_has_no_financial_effect(
    db_session: AsyncSession,
) -> None:
    request = await confirmation_request(db_session)
    context = runtime(db_session)
    original = await save(db_session, request)
    assert original.state == "saved_draft"
    assert original.confirmation.quote.total_amount == Decimal("1.02")
    assert await save(db_session, request) == original
    assert SavedCashIntentV2.model_validate_json(original.model_dump_json()) == original
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_cash_intent WHERE tenant_id=:tenant"),
            {"tenant": context.stock.tenant_id},
        )
        == 1
    )
    for table in ("sale_payment", "pos_payment_attempt"):
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE sale_id=:sale"),
                {"sale": request.sale_id},
            )
            == 0
        )
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_sale_line WHERE tenant_id=:tenant"),
            {"tenant": context.stock.tenant_id},
        )
        == 0
    )
    assert await db_session.scalar(
        text("SELECT total_amount FROM sale WHERE id=:sale"), {"sale": request.sale_id}
    ) == Decimal("0.02")
    assert (
        await db_session.scalar(
            text(
                "SELECT count(*) FROM audit_log WHERE table_name='pos_unit_cash_intent' "
                "AND record_id=:operation"
            ),
            {"operation": request.operation_id},
        )
        == 1
    )


async def test_replay_retains_original_but_new_confirmation_must_be_current(
    db_session: AsyncSession,
) -> None:
    request = await confirmation_request(db_session, cash_tendered=Decimal("3.00"))
    original = await save(db_session, request)
    await db_session.execute(
        text("UPDATE pos_unit_cart_item SET quantity=2 WHERE sale_id=:sale"),
        {"sale": request.sale_id},
    )
    assert await save(db_session, request) == original
    stale = CashConfirmationRequestV2.model_validate(
        {**request.model_dump(), "operation_id": uuid4()}
    )
    with pytest.raises(ConflictError, match="Cart changed"):
        await save(db_session, stale)
    current = await quote(db_session, request.sale_id)
    fresh = CashConfirmationRequestV2.model_validate(
        {
            **stale.model_dump(),
            "expected_revision_hash": current.revision_hash,
            "expected_total_amount": current.total_amount,
        }
    )
    updated = await save(db_session, fresh)
    assert updated.confirmation.change_amount == Decimal("0.98")
    changed = CashConfirmationRequestV2.model_validate(
        {**request.model_dump(), "cash_tendered": "4.00"}
    )
    with pytest.raises(ConflictError, match="different request"):
        await save(db_session, changed)


async def test_original_is_private_to_current_cashier_tenant_and_allowed_branch(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    context = runtime(db_session)
    other = await seed_receiving_actor(db_engine, maintenance_engine, context.stock)
    request = await confirmation_request(db_session)
    await save(db_session, request)
    service = UnitCashIntentService(UnitCashIntentRepository(db_session))
    with pytest.raises(PermissionDeniedError):
        await service.save(
            tenant_id=context.stock.tenant_id,
            actor_id=context.actor_id,
            allowed_branch_ids={uuid4()},
            request=request,
        )
    await set_receiving_context(db_session, context.stock, other)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_cash_intent WHERE id=:id"),
            {"id": request.operation_id},
        )
        == 0
    )
    with pytest.raises(PermissionDeniedError):
        await service.save(
            tenant_id=context.stock.tenant_id,
            actor_id=other,
            allowed_branch_ids={context.stock.branch_id},
            request=request,
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    await db_session.execute(
        text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(uuid4())}
    )
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_cash_intent WHERE id=:id"),
            {"id": request.operation_id},
        )
        == 0
    )
    with pytest.raises(PermissionDeniedError):
        await save(db_session, request)


@pytest.mark.parametrize(
    "change",
    [
        "jsonb_set(quote_payload,'{lines,0,quantity}','\"0.002\"')",
        "jsonb_set(quote_payload,'{lines,1,quantity}','2')",
        "jsonb_set(quote_payload,'{lines,1,net_amount}','\"0.99\"')",
        "jsonb_set(quote_payload,'{lines}','[]')",
        "quote_payload || jsonb_build_object('extra',true)",
    ],
)
async def test_database_rejects_forged_quote_rows(db_session: AsyncSession, change: str) -> None:
    request = await confirmation_request(db_session)
    await save(db_session, request)
    columns = (
        "tenant_id,id,branch_id,sale_id,register_id,shift_id,cashier_user_id,request_hash,"
        "revision_hash,expected_total_amount,cash_tendered,change_amount,currency,"
        "quote_payload,created_by"
    )
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    f"INSERT INTO pos_unit_cash_intent({columns}) "
                    "SELECT tenant_id,:new_id,branch_id,sale_id,register_id,shift_id,"
                    "cashier_user_id,request_hash,revision_hash,expected_total_amount,"
                    f"cash_tendered,change_amount,currency,{change},created_by "
                    "FROM pos_unit_cash_intent WHERE id=:id"
                ),
                {"id": request.operation_id, "new_id": uuid4()},
            )


@pytest.mark.parametrize(
    "action",
    ["UPDATE pos_unit_cash_intent SET cash_tendered=3", "DELETE FROM pos_unit_cash_intent"],
)
async def test_database_blocks_intent_mutation(db_session: AsyncSession, action: str) -> None:
    request = await confirmation_request(db_session)
    await save(db_session, request)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(text(action + " WHERE id=:id"), {"id": request.operation_id})


async def test_caller_rollback_discards_saved_intent(db_session: AsyncSession) -> None:
    request = await confirmation_request(db_session)
    await db_session.commit()
    context = runtime(db_session)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    await save(db_session, request)
    await db_session.rollback()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_cash_intent WHERE id=:id"),
            {"id": request.operation_id},
        )
        == 0
    )


async def test_concurrent_repeats_save_exactly_one_intent(db_session: AsyncSession) -> None:
    request = await confirmation_request(db_session)
    context = runtime(db_session)
    await db_session.commit()

    async def confirm() -> SavedCashIntentV2:
        async with context.factory() as session:
            await set_receiving_context(session, context.stock, context.actor_id)
            session.info["receiving_runtime"] = context
            result = await save(session, request)
            await session.commit()
            return result

    first, second = await asyncio.wait_for(asyncio.gather(confirm(), confirm()), timeout=10)
    assert first == second
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_cash_intent WHERE id=:id"),
            {"id": request.operation_id},
        )
        == 1
    )


async def test_repeat_during_cart_change_recovers_the_original(db_session: AsyncSession) -> None:
    request = await confirmation_request(db_session)
    context = runtime(db_session)
    await db_session.commit()
    saved, read_started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def original() -> SavedCashIntentV2:
        async with context.factory() as session:
            await set_receiving_context(session, context.stock, context.actor_id)
            session.info["receiving_runtime"] = context
            result = await save(session, request)
            saved.set()
            await release.wait()
            await session.execute(
                text("UPDATE pos_unit_cart_item SET quantity=2 WHERE sale_id=:sale"),
                {"sale": request.sale_id},
            )
            await session.commit()
            return result

    class ObservedRepository(UnitCashIntentRepository):
        async def get(self, tenant_id: UUID, operation_id: UUID) -> UnitCashIntent | None:
            result = await super().get(tenant_id, operation_id)
            if result is None:
                read_started.set()
            return result

    async def repeated() -> SavedCashIntentV2:
        await saved.wait()
        async with context.factory() as session:
            await set_receiving_context(session, context.stock, context.actor_id)
            result = await UnitCashIntentService(ObservedRepository(session)).save(
                tenant_id=context.stock.tenant_id,
                actor_id=context.actor_id,
                allowed_branch_ids={context.stock.branch_id},
                request=request,
            )
            await session.commit()
            return result

    first, second = asyncio.create_task(original()), asyncio.create_task(repeated())
    try:
        await asyncio.wait_for(read_started.wait(), timeout=5)
        assert not second.done()
    finally:
        release.set()
    initial, replayed = await asyncio.wait_for(asyncio.gather(first, second), timeout=10)
    assert replayed == initial
    assert replayed.confirmation.quote.total_amount == Decimal("1.02")
