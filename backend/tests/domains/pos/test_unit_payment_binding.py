"""Payment-plan activation freezes a draft without accepting external money."""

import asyncio
from typing import Literal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.pos.models import Sale
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.pos.unit_payment_binding_repository import UnitPaymentBindingRepository
from app.domains.pos.unit_payment_binding_schemas import UnitPaymentBindingRead
from app.domains.pos.unit_payment_binding_service import UnitPaymentBindingService
from app.domains.pos.unit_payment_intent_schemas import SaveUnitPaymentIntentRequest
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.pos.test_payment_configuration import _set_payment_settings
from tests.domains.pos.test_unit_payment_intent import intent_request, save
from tests.domains.pos.test_unit_payment_plan import snapshot
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def binding(
    session: AsyncSession,
    request: SaveUnitPaymentIntentRequest,
    action: Literal["bind", "cancel", "read"],
) -> UnitPaymentBindingRead:
    context = runtime(session)
    service = UnitPaymentBindingService(UnitPaymentBindingRepository(session))
    method = {"bind": service.bind, "cancel": service.cancel, "read": service.read}[action]
    return await method(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        sale_id=request.request.sale_id,
        operation_id=request.operation_id,
    )


@pytest.mark.parametrize("mixed", [False, True])
async def test_bind_replay_and_cancel_have_no_payment_or_stock_effect(
    db_session: AsyncSession, mixed: bool
) -> None:
    request = await intent_request(db_session, mixed=mixed)
    original = await save(db_session, request)
    before = await snapshot(db_session)
    bound = await binding(db_session, request, "bind")
    assert bound.state == "bound"
    assert bound.plan_hash == original.plan.plan_hash
    assert bound.bound_at is not None and bound.cancelled_at is None
    assert await binding(db_session, request, "bind") == bound
    assert await binding(db_session, request, "read") == bound
    with pytest.raises(ConflictError):
        await put_physical(db_session, request.request.sale_id, position=2)
    cancelled = await binding(db_session, request, "cancel")
    assert cancelled.state == "cancelled" and cancelled.cancelled_at is not None
    assert await binding(db_session, request, "cancel") == cancelled
    assert await binding(db_session, request, "read") == cancelled
    with pytest.raises(ConflictError):
        await binding(db_session, request, "bind")
    assert await snapshot(db_session) == before
    await put_physical(db_session, request.request.sale_id, position=2)


async def test_cancellation_before_binding_blocks_late_activation_and_allows_new_plan(
    db_session: AsyncSession,
) -> None:
    request = await intent_request(db_session)
    await save(db_session, request)
    cancelled = await binding(db_session, request, "cancel")
    assert cancelled.state == "cancelled" and cancelled.bound_at is None
    with pytest.raises(ConflictError):
        await binding(db_session, request, "bind")
    fresh = SaveUnitPaymentIntentRequest(operation_id=uuid4(), request=request.request)
    await save(db_session, fresh)
    assert (await binding(db_session, fresh, "bind")).state == "bound"


async def test_competing_saved_plans_cannot_bind_same_sale(db_session: AsyncSession) -> None:
    first = await intent_request(db_session)
    second = SaveUnitPaymentIntentRequest(operation_id=uuid4(), request=first.request)
    await save(db_session, first)
    await save(db_session, second)
    await binding(db_session, first, "bind")
    with pytest.raises(ConflictError):
        await binding(db_session, second, "bind")
    await binding(db_session, first, "cancel")
    assert (await binding(db_session, second, "bind")).state == "bound"


@pytest.mark.parametrize("damage", ["price", "settings"])
async def test_stale_plan_cannot_bind_but_can_be_cancelled(
    db_session: AsyncSession, damage: str
) -> None:
    request = await intent_request(db_session)
    await save(db_session, request)
    context = runtime(db_session)
    if damage == "price":
        await db_session.execute(
            text(
                "UPDATE tenant_catalog SET unit_sale_preparation="
                "jsonb_set(unit_sale_preparation,'{unit_price}','\"2.00\"') WHERE id=:id"
            ),
            {"id": context.stock.catalog_id},
        )
    else:
        await _set_payment_settings(
            db_session, tenant_id=context.stock.tenant_id, methods=["cash"], mixed=False
        )
    expected = ConflictError if damage == "price" else BusinessRuleError
    with pytest.raises(expected):
        await binding(db_session, request, "bind")
    assert (await binding(db_session, request, "cancel")).state == "cancelled"


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE sale SET total_amount=999 WHERE id=:sale",
        "DELETE FROM sale_item WHERE sale_id=:sale",
        "UPDATE pos_unit_cart_item SET quantity=3 WHERE sale_id=:sale",
        "DELETE FROM pos_unit_cart_item WHERE sale_id=:sale",
        "INSERT INTO sale_payment(id,tenant_id,sale_id,payment_method,amount,currency) "
        "VALUES(:id,:tenant,:sale,'card',1.99,'TJS')",
    ],
)
async def test_active_binding_fences_direct_sql_mutation(
    db_session: AsyncSession, statement: str
) -> None:
    request = await intent_request(db_session, mixed=True)
    await save(db_session, request)
    await binding(db_session, request, "bind")
    before = await snapshot(db_session)
    with pytest.raises(DBAPIError, match="unit_payment_binding_draft_frozen"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(statement),
                {
                    "sale": request.request.sale_id,
                    "tenant": runtime(db_session).stock.tenant_id,
                    "id": uuid4(),
                },
            )
    assert await snapshot(db_session) == before


@pytest.mark.parametrize("first_action", ["bind", "cancel"])
async def test_concurrent_binding_and_cancellation_end_cancelled(
    db_session: AsyncSession, first_action: Literal["bind", "cancel"]
) -> None:
    request = await intent_request(db_session)
    await save(db_session, request)
    context = runtime(db_session)
    await db_session.commit()
    locked, competing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def first() -> UnitPaymentBindingRead:
        async with context.factory() as session:
            await set_receiving_context(session, context.stock, context.actor_id)
            session.info["receiving_runtime"] = context
            result = await binding(session, request, first_action)
            locked.set()
            await release.wait()
            await session.commit()
            return result

    async def second() -> None:
        await locked.wait()
        async with context.factory() as session:
            await set_receiving_context(session, context.stock, context.actor_id)
            session.info["receiving_runtime"] = context
            competing.set()
            if first_action == "cancel":
                with pytest.raises(ConflictError):
                    await binding(session, request, "bind")
            else:
                assert (await binding(session, request, "cancel")).state == "cancelled"
            await session.commit()

    first_task, second_task = asyncio.create_task(first()), asyncio.create_task(second())
    try:
        await asyncio.wait_for(competing.wait(), timeout=5)
    finally:
        release.set()
    await asyncio.wait_for(asyncio.gather(first_task, second_task), timeout=10)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert (await binding(db_session, request, "read")).state == "cancelled"


async def test_binding_and_cancellation_rows_are_immutable(db_session: AsyncSession) -> None:
    request = await intent_request(db_session)
    await save(db_session, request)
    await binding(db_session, request, "bind")
    await binding(db_session, request, "cancel")
    for table in ("pos_unit_payment_plan_binding", "pos_unit_payment_plan_binding_cancel"):
        for statement in (
            f"UPDATE {table} SET created_by=created_by WHERE id=:id",
            f"DELETE FROM {table} WHERE id=:id",
        ):
            with pytest.raises(DBAPIError):
                async with db_session.begin_nested():
                    await db_session.execute(text(statement), {"id": request.operation_id})
    assert (await binding(db_session, request, "read")).state == "cancelled"


async def test_bound_plan_cannot_start_external_payment_attempt(db_session: AsyncSession) -> None:
    request = await intent_request(db_session)
    await save(db_session, request)
    await binding(db_session, request, "bind")
    context = runtime(db_session)
    before = await snapshot(db_session)
    with pytest.raises(BusinessRuleError):
        await POSService(POSRepository(db_session)).create_payment_attempt(
            tenant_id=context.stock.tenant_id,
            sale_id=request.request.sale_id,
            actor_id=context.actor_id,
            operation_id=uuid4(),
            payment_method="card",
            amount=request.request.expected_total_amount,
            currency="TJS",
        )
    assert await snapshot(db_session) == before


async def test_binding_facts_are_private_to_actual_actor_and_tenant(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    context = runtime(db_session)
    other = await seed_receiving_actor(db_engine, maintenance_engine, context.stock)
    request = await intent_request(db_session)
    await save(db_session, request)
    await binding(db_session, request, "bind")
    await binding(db_session, request, "cancel")
    await set_receiving_context(db_session, context.stock, other)
    service = UnitPaymentBindingService(UnitPaymentBindingRepository(db_session))
    with pytest.raises(NotFoundError):
        await service.read(
            tenant_id=context.stock.tenant_id,
            actor_id=other,
            allowed_branch_ids={context.stock.branch_id},
            sale_id=request.request.sale_id,
            operation_id=request.operation_id,
        )
    for changed_tenant in (False, True):
        if changed_tenant:
            await set_receiving_context(db_session, context.stock, context.actor_id)
            await db_session.execute(
                text("SELECT set_config('app.tenant_id',:tenant,true)"),
                {"tenant": str(uuid4())},
            )
        for table in ("pos_unit_payment_plan_binding", "pos_unit_payment_plan_binding_cancel"):
            count = await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE id=:id"),
                {"id": request.operation_id},
            )
            assert count == 0
        with pytest.raises(PermissionDeniedError):
            await binding(db_session, request, "cancel")


async def test_moving_unit_item_cannot_escape_old_parent_binding(db_session: AsyncSession) -> None:
    request = await intent_request(db_session)
    await save(db_session, request)
    context = runtime(db_session)
    source = await db_session.get(Sale, request.request.sale_id)
    assert source is not None
    target = await POSService(POSRepository(db_session)).create_sale(
        tenant_id=context.stock.tenant_id,
        register_id=source.register_id,
        cashier_user_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
    )
    await binding(db_session, request, "bind")
    move = text("UPDATE pos_unit_cart_item SET sale_id=:target WHERE sale_id=:source")
    values = {"target": target.id, "source": source.id}
    with pytest.raises(DBAPIError, match="unit_payment_binding_draft_frozen"):
        async with db_session.begin_nested():
            await db_session.execute(move, values)


async def test_sql_cancel_waiting_before_bind_preserves_timestamp_order(
    db_session: AsyncSession,
) -> None:
    request = await intent_request(db_session)
    await save(db_session, request)
    context = runtime(db_session)
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    await UnitPaymentBindingRepository(db_session).lock_operation(
        context.stock.tenant_id, request.operation_id
    )
    holder_pid = await db_session.scalar(text("SELECT pg_backend_pid()"))
    cancelling_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def cancel_with_sql() -> None:
        async with context.factory() as session:
            await set_receiving_context(session, context.stock, context.actor_id)
            pid = await session.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(pid, int)
            cancelling_pid.set_result(pid)
            await session.execute(
                text(
                    "INSERT INTO pos_unit_payment_plan_binding_cancel "
                    "(tenant_id,id,sale_id,branch_id,cashier_user_id,created_by) "
                    "VALUES(:tenant,:operation,:sale,:branch,:actor,:actor)"
                ),
                {
                    "tenant": context.stock.tenant_id,
                    "operation": request.operation_id,
                    "sale": request.request.sale_id,
                    "branch": context.stock.branch_id,
                    "actor": context.actor_id,
                },
            )
            await session.commit()

    cancelling = asyncio.create_task(cancel_with_sql())
    try:
        pid = await asyncio.wait_for(cancelling_pid, timeout=5)
        # Observe the actual blocked INSERT, not an assumed scheduling delay.
        async with asyncio.timeout(10):
            while not await db_session.scalar(
                text("SELECT :holder = ANY(pg_blocking_pids(:waiting))"),
                {"holder": holder_pid, "waiting": pid},
            ):
                if cancelling.done():
                    await cancelling
                    pytest.fail("Cancellation unexpectedly bypassed the operation lock")
                await asyncio.sleep(0)
        assert not cancelling.done()
        bound = await binding(db_session, request, "bind")
        await db_session.commit()
        await asyncio.wait_for(cancelling, timeout=10)
    finally:
        if not cancelling.done():
            cancelling.cancel()
        await asyncio.gather(cancelling, return_exceptions=True)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    recovered = await binding(db_session, request, "read")
    assert recovered.state == "cancelled"
    assert recovered.bound_at == bound.bound_at
    assert recovered.cancelled_at is not None and recovered.bound_at is not None
    assert recovered.cancelled_at >= recovered.bound_at


@pytest.mark.parametrize("bound", [False, True])
async def test_payment_fence_hides_binding_state_from_wrong_tenant_context(
    db_session: AsyncSession, bound: bool
) -> None:
    request = await intent_request(db_session)
    await save(db_session, request)
    if bound:
        await binding(db_session, request, "bind")
    context = runtime(db_session)
    before = await snapshot(db_session)
    await db_session.execute(
        text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(uuid4())}
    )
    try:
        with pytest.raises(DBAPIError, match="unit_payment_binding_context_denied"):
            async with db_session.begin_nested():
                await db_session.execute(
                    text(
                        "INSERT INTO sale_payment "
                        "(id,tenant_id,sale_id,payment_method,amount,currency) "
                        "VALUES(:id,:tenant,:sale,'card',1.99,'TJS')"
                    ),
                    {
                        "id": uuid4(),
                        "tenant": context.stock.tenant_id,
                        "sale": request.request.sale_id,
                    },
                )
    finally:
        await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await snapshot(db_session) == before
