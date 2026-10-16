"""Durable payment plans preserve intent without accepting money or moving stock."""

import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from app.domains.pos.unit_payment_intent_repository import UnitPaymentIntentRepository
from app.domains.pos.unit_payment_intent_schemas import (
    SavedUnitPaymentIntent,
    SaveUnitPaymentIntentRequest,
)
from app.domains.pos.unit_payment_intent_service import UnitPaymentIntentService
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.pos.test_payment_configuration import _set_payment_settings
from tests.domains.pos.test_unit_payment_plan import plan_request, snapshot
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def intent_request(
    session: AsyncSession, *, mixed: bool = False
) -> SaveUnitPaymentIntentRequest:
    return SaveUnitPaymentIntentRequest(
        operation_id=uuid4(), request=await plan_request(session, mixed=mixed)
    )


async def save(
    session: AsyncSession, request: SaveUnitPaymentIntentRequest
) -> SavedUnitPaymentIntent:
    context = runtime(session)
    return await UnitPaymentIntentService(UnitPaymentIntentRepository(session)).save(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        request=request,
    )


async def read(
    session: AsyncSession, request: SaveUnitPaymentIntentRequest
) -> SavedUnitPaymentIntent:
    context = runtime(session)
    return await UnitPaymentIntentService(UnitPaymentIntentRepository(session)).read(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        sale_id=request.request.sale_id,
        operation_id=request.operation_id,
    )


@pytest.mark.parametrize("mixed", [False, True])
async def test_saved_plan_replay_and_read_have_no_financial_effect(
    db_session: AsyncSession, mixed: bool
) -> None:
    request = await intent_request(db_session, mixed=mixed)
    before = await snapshot(db_session)
    original = await save(db_session, request)
    assert original.state == "saved_draft"
    assert original.operation_id == request.operation_id
    assert original.plan.quote.total_amount == Decimal("2.01" if mixed else "1.99")
    assert await save(db_session, request) == original
    assert await read(db_session, request) == original
    assert SavedUnitPaymentIntent.model_validate_json(original.model_dump_json()) == original
    assert "purchase" not in original.model_dump_json() and "cost" not in original.model_dump_json()
    assert await snapshot(db_session) == before
    count = await db_session.scalar(
        text("SELECT count(*) FROM pos_unit_payment_plan_intent WHERE id=:id"),
        {"id": request.operation_id},
    )
    assert count == 1
    audit_count = await db_session.scalar(
        text(
            "SELECT count(*) FROM audit_log WHERE table_name='pos_unit_payment_plan_intent' "
            "AND record_id=:id"
        ),
        {"id": request.operation_id},
    )
    assert audit_count == 1
    audit_snapshot = await db_session.scalar(
        text(
            "SELECT new_values FROM audit_log "
            "WHERE table_name='pos_unit_payment_plan_intent' AND record_id=:id"
        ),
        {"id": request.operation_id},
    )
    assert isinstance(audit_snapshot, dict)
    assert "request_payload" not in audit_snapshot and "quote_payload" not in audit_snapshot
    rows = await db_session.execute(
        text("SELECT created_by,created_at FROM pos_unit_payment_plan_intent WHERE id=:id"),
        {"id": request.operation_id},
    )
    row = rows.one()
    assert row.created_by == runtime(db_session).actor_id
    assert row.created_at == original.saved_at


async def test_replay_uses_snapshot_after_price_and_settings_change(
    db_session: AsyncSession,
) -> None:
    request = await intent_request(db_session)
    original = await save(db_session, request)
    context = runtime(db_session)
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET unit_sale_preparation="
            "jsonb_set(unit_sale_preparation,'{unit_price}','\"2.00\"') WHERE id=:id"
        ),
        {"id": context.stock.catalog_id},
    )
    await _set_payment_settings(
        db_session, tenant_id=context.stock.tenant_id, methods=["cash"], mixed=False
    )
    assert await read(db_session, request) == original
    assert await save(db_session, request) == original
    stale = SaveUnitPaymentIntentRequest(operation_id=uuid4(), request=request.request)
    with pytest.raises(ConflictError):
        await save(db_session, stale)
    changed = SaveUnitPaymentIntentRequest.model_validate(
        {
            **request.model_dump(),
            "request": {
                **request.request.model_dump(),
                "payments": [{"method": "qr", "amount": request.request.expected_total_amount}],
            },
        }
    )
    with pytest.raises(ConflictError):
        await save(db_session, changed)
    other_sale = request.model_copy(
        update={"request": request.request.model_copy(update={"sale_id": uuid4()})}
    )
    with pytest.raises(ConflictError):
        await save(db_session, other_sale)


async def test_concurrent_exact_replays_save_one_intent(db_session: AsyncSession) -> None:
    request = await intent_request(db_session)
    context = runtime(db_session)
    await db_session.commit()

    async def confirm() -> SavedUnitPaymentIntent:
        async with context.factory() as session:
            await set_receiving_context(session, context.stock, context.actor_id)
            session.info["receiving_runtime"] = context
            result = await save(session, request)
            await session.commit()
            return result

    first, second = await asyncio.wait_for(asyncio.gather(confirm(), confirm()), timeout=10)
    assert first == second
    await set_receiving_context(db_session, context.stock, context.actor_id)
    count = await db_session.scalar(
        text("SELECT count(*) FROM pos_unit_payment_plan_intent WHERE id=:id"),
        {"id": request.operation_id},
    )
    assert count == 1


async def test_caller_rollback_removes_uncommitted_intent(db_session: AsyncSession) -> None:
    request = await intent_request(db_session)
    context = runtime(db_session)
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    await save(db_session, request)
    await db_session.rollback()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    count = await db_session.scalar(
        text("SELECT count(*) FROM pos_unit_payment_plan_intent WHERE id=:id"),
        {"id": request.operation_id},
    )
    assert count == 0


async def test_saved_intent_is_private_to_real_cashier_tenant_and_branch(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    context = runtime(db_session)
    other = await seed_receiving_actor(db_engine, maintenance_engine, context.stock)
    request = await intent_request(db_session)
    await save(db_session, request)
    service = UnitPaymentIntentService(UnitPaymentIntentRepository(db_session))
    with pytest.raises(PermissionDeniedError):
        await service.read(
            tenant_id=context.stock.tenant_id,
            actor_id=context.actor_id,
            allowed_branch_ids={uuid4()},
            sale_id=request.request.sale_id,
            operation_id=request.operation_id,
        )
    for actor, tenant in ((other, context.stock.tenant_id), (context.actor_id, uuid4())):
        await set_receiving_context(db_session, context.stock, actor)
        await db_session.execute(
            text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(tenant)}
        )
        count = await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_payment_plan_intent WHERE id=:id"),
            {"id": request.operation_id},
        )
        assert count == 0
        if actor == other:
            with pytest.raises(NotFoundError):
                await service.read(
                    tenant_id=tenant,
                    actor_id=actor,
                    allowed_branch_ids={context.stock.branch_id},
                    sale_id=request.request.sale_id,
                    operation_id=request.operation_id,
                )
        with pytest.raises(PermissionDeniedError):
            await read(db_session, request)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE pos_unit_payment_plan_intent SET change_amount=999 WHERE id=:id",
        "DELETE FROM pos_unit_payment_plan_intent WHERE id=:id",
    ],
)
async def test_saved_intent_cannot_be_mutated(db_session: AsyncSession, statement: str) -> None:
    request = await intent_request(db_session)
    original = await save(db_session, request)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(text(statement), {"id": request.operation_id})
    assert await read(db_session, request) == original


@pytest.mark.parametrize(
    "change",
    [
        "jsonb_set(quote_payload,'{lines,0,net_amount}','\"9.99\"')",
        "quote_payload || jsonb_build_object('unexpected',true)",
        "jsonb_set(request_payload,'{payments,0,amount}','\"0.01\"')",
        "999::numeric",
    ],
)
async def test_database_rejects_corrupt_intent_snapshot(
    db_session: AsyncSession, change: str
) -> None:
    request = await intent_request(db_session)
    await save(db_session, request)
    columns = (
        "tenant_id,id,branch_id,sale_id,register_id,shift_id,cashier_user_id,"
        "request_hash,plan_hash,request_payload,quote_payload,change_amount,created_by"
    )
    valid_id = uuid4()
    copied_id = await db_session.scalar(
        text(
            f"INSERT INTO pos_unit_payment_plan_intent({columns}) "
            "SELECT tenant_id,:new_id,branch_id,sale_id,register_id,shift_id,cashier_user_id,"
            "request_hash,plan_hash,request_payload,quote_payload,change_amount,created_by "
            "FROM pos_unit_payment_plan_intent WHERE id=:id RETURNING id"
        ),
        {"id": request.operation_id, "new_id": valid_id},
    )
    assert copied_id == valid_id
    quote_payload = change if "quote_payload" in change else "quote_payload"
    request_payload = change if "request_payload" in change else "request_payload"
    change_amount = change if change == "999::numeric" else "change_amount"
    sql = (
        f"INSERT INTO pos_unit_payment_plan_intent({columns}) "
        "SELECT tenant_id,:new_id,branch_id,sale_id,register_id,shift_id,cashier_user_id,"
        f"request_hash,plan_hash,{request_payload},{quote_payload},{change_amount},created_by "
        "FROM pos_unit_payment_plan_intent WHERE id=:id"
    )
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(text(sql), {"id": request.operation_id, "new_id": uuid4()})


async def test_reordered_split_replays_but_changed_cash_conflicts(db_session: AsyncSession) -> None:
    request = await intent_request(db_session)
    request = SaveUnitPaymentIntentRequest.model_validate(
        {
            **request.model_dump(),
            "request": {
                **request.request.model_dump(),
                "payments": [
                    {"method": "cash", "amount": "0.99"},
                    {"method": "card", "amount": "1.00"},
                ],
                "cash_tendered": "5.00",
            },
        }
    )
    original = await save(db_session, request)
    reversed_request = request.model_copy(
        update={
            "request": request.request.model_copy(
                update={"payments": tuple(reversed(request.request.payments))}
            )
        }
    )
    assert await save(db_session, reversed_request) == original
    changed = request.model_copy(
        update={"request": request.request.model_copy(update={"cash_tendered": Decimal("6.00")})}
    )
    with pytest.raises(ConflictError):
        await save(db_session, changed)
