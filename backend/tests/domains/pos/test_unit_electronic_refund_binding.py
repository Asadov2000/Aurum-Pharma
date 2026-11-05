"""Real binding locks fence competing returns without moving money or stock."""

import asyncio
from typing import Literal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import AurumError, ConflictError, NotFoundError
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.physical_return_journal_repository import PhysicalReturnJournalRepository
from app.domains.pos.physical_return_journal_service import PhysicalReturnJournalService
from app.domains.pos.unit_electronic_refund_binding_repository import (
    UnitElectronicRefundBindingRepository,
)
from app.domains.pos.unit_electronic_refund_binding_schemas import UnitElectronicRefundBindingRead
from app.domains.pos.unit_electronic_refund_binding_service import (
    UnitElectronicRefundBindingService,
)
from app.domains.pos.unit_electronic_refund_intent_repository import (
    UnitElectronicRefundIntentRepository,
)
from app.domains.pos.unit_electronic_refund_intent_schemas import SaveElectronicRefundIntentRequest
from app.domains.pos.unit_electronic_refund_intent_service import UnitElectronicRefundIntentService
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.pos.test_unit_electronic_refund_preview import facts
from tests.domains.pos.test_unit_electronic_refund_revalidation import persist_review
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def binding(
    session: AsyncSession,
    request: SaveElectronicRefundIntentRequest,
    action: Literal["bind", "cancel", "read"],
    actor: UUID | None = None,
) -> UnitElectronicRefundBindingRead:
    context = runtime(session)
    service = UnitElectronicRefundBindingService(UnitElectronicRefundBindingRepository(session))
    return await {"bind": service.bind, "cancel": service.cancel, "read": service.read}[action](
        tenant_id=context.stock.tenant_id,
        actor_id=actor or context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        parent_sale_id=request.request.parent_sale_id,
        operation_id=request.operation_id,
    )


async def test_binding_retries_and_cancellation_preserve_financial_facts(
    db_session: AsyncSession,
) -> None:
    request, saved = await persist_review(db_session)
    before = await facts(db_session)
    bound = await binding(db_session, request, "bind")
    assert bound.state == "bound"
    assert bound.operation_id == request.operation_id
    assert bound.review_revision_hash == saved.preview.revision_hash
    assert bound.bound_at is not None and bound.bound_at.tzinfo is not None
    assert await binding(db_session, request, "bind") == bound
    assert await binding(db_session, request, "read") == bound
    cancelled = await binding(db_session, request, "cancel")
    assert cancelled.state == "cancelled"
    assert cancelled.bound_at == bound.bound_at
    assert cancelled.cancelled_at is not None and cancelled.cancelled_at >= bound.bound_at
    assert await binding(db_session, request, "cancel") == cancelled
    assert await binding(db_session, request, "read") == cancelled
    with pytest.raises(ConflictError):
        await binding(db_session, request, "bind")
    assert await facts(db_session) == before
    for table in (
        "pos_unit_electronic_refund_binding",
        "pos_unit_electronic_refund_binding_cancel",
    ):
        assert await db_session.scalar(text(f"SELECT count(*) FROM {table}")) == 1


async def test_early_cancel_never_resurrects_but_allows_a_fresh_review(
    db_session: AsyncSession,
) -> None:
    request, _ = await persist_review(db_session)
    cancelled = await binding(db_session, request, "cancel")
    assert cancelled.state == "cancelled" and cancelled.bound_at is None
    with pytest.raises(ConflictError):
        await binding(db_session, request, "bind")
    fresh = request.model_copy(update={"operation_id": uuid4()})
    context = runtime(db_session)
    await UnitElectronicRefundIntentService(UnitElectronicRefundIntentRepository(db_session)).save(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        request=fresh,
    )
    assert (await binding(db_session, fresh, "bind")).state == "bound"


@pytest.mark.parametrize(
    "table", ["pos_unit_electronic_refund_binding", "pos_unit_electronic_refund_binding_cancel"]
)
@pytest.mark.parametrize("verb", ["UPDATE", "DELETE"])
async def test_binding_history_rejects_raw_mutations(
    db_session: AsyncSession, table: str, verb: str
) -> None:
    request, _ = await persist_review(db_session)
    await binding(db_session, request, "bind")
    saved = await binding(db_session, request, "cancel")
    statement = (
        f"UPDATE {table} SET created_at=created_at WHERE id=:id"
        if verb == "UPDATE"
        else f"DELETE FROM {table} WHERE id=:id"
    )
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(text(statement), {"id": request.operation_id})
    assert await binding(db_session, request, "read") == saved


async def test_active_binding_fences_physical_return_until_cancelled(
    db_session: AsyncSession,
) -> None:
    request, _ = await persist_review(db_session)
    context = runtime(db_session)
    await binding(db_session, request, "bind")
    item = request.request.selection.items[0]
    selections = (PhysicalReturnSelection(item.line_id, item.sale_format, item.quantity),)
    service = PhysicalReturnJournalService(PhysicalReturnJournalRepository(db_session))
    before = await facts(db_session)
    with pytest.raises((AurumError, DBAPIError)):
        async with db_session.begin_nested():
            await service.receive(
                tenant_id=context.stock.tenant_id,
                actor_id=context.actor_id,
                parent_sale_id=request.request.parent_sale_id,
                selections=selections,
                operation_id=uuid4(),
            )
    assert await facts(db_session) == before
    await binding(db_session, request, "cancel")
    received = await service.receive(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        parent_sale_id=request.request.parent_sale_id,
        selections=selections,
        operation_id=uuid4(),
    )
    assert received.parent_sale_id == request.request.parent_sale_id


async def test_two_authors_compete_for_one_parent_and_loser_can_bind_after_cancel(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    first, _ = await persist_review(db_session)
    context = runtime(db_session)
    await db_session.commit()
    second_actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, context.stock, second_actor)
    second = first.model_copy(update={"operation_id": uuid4()})
    await UnitElectronicRefundIntentService(UnitElectronicRefundIntentRepository(db_session)).save(
        tenant_id=context.stock.tenant_id,
        actor_id=second_actor,
        allowed_branch_ids={context.stock.branch_id},
        request=second,
    )
    await db_session.commit()
    start = asyncio.Event()

    async def contender(request: SaveElectronicRefundIntentRequest, actor: UUID) -> bool:
        async with context.factory() as session:
            session.info["receiving_runtime"] = context
            await set_receiving_context(session, context.stock, actor)
            await start.wait()
            try:
                await binding(session, request, "bind", actor)
            except ConflictError:
                await session.rollback()
                return False
            await session.commit()
            return True

    tasks = [
        asyncio.create_task(contender(first, context.actor_id)),
        asyncio.create_task(contender(second, second_actor)),
    ]
    start.set()
    try:
        outcomes = await asyncio.wait_for(asyncio.gather(*tasks), timeout=20)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert sum(outcomes) == 1
    winner, winner_actor, loser, loser_actor = (
        (first, context.actor_id, second, second_actor)
        if outcomes[0]
        else (second, second_actor, first, context.actor_id)
    )
    await set_receiving_context(db_session, context.stock, winner_actor)
    await binding(db_session, winner, "cancel", winner_actor)
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, loser_actor)
    assert (await binding(db_session, loser, "bind", loser_actor)).state == "bound"


async def test_other_refund_author_cannot_read_binding_history(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    request, _ = await persist_review(db_session)
    context = runtime(db_session)
    await binding(db_session, request, "bind")
    await binding(db_session, request, "cancel")
    await db_session.commit()
    other = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, context.stock, other)
    for table in (
        "pos_unit_electronic_refund_binding",
        "pos_unit_electronic_refund_binding_cancel",
    ):
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE id=:id"), {"id": request.operation_id}
            )
            == 0
        )
    with pytest.raises(NotFoundError):
        await binding(db_session, request, "read", other)


async def test_readonly_recovery_succeeds_and_new_binding_or_cancel_is_denied(
    db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    request, _ = await persist_review(db_session)
    context = runtime(db_session)
    unbound = request.model_copy(update={"operation_id": uuid4()})
    await UnitElectronicRefundIntentService(UnitElectronicRefundIntentRepository(db_session)).save(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        request=unbound,
    )
    saved = await binding(db_session, request, "bind")
    await db_session.commit()
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:id"),
            {"id": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await binding(db_session, request, "read") == saved
    assert await binding(db_session, request, "bind") == saved
    actions: tuple[tuple[SaveElectronicRefundIntentRequest, Literal["cancel", "bind"]], ...] = (
        (request, "cancel"),
        (unbound, "bind"),
    )
    for candidate, action in actions:
        with pytest.raises(DBAPIError):
            async with db_session.begin_nested():
                await binding(db_session, candidate, action)


@pytest.mark.parametrize("first_action", ["bind", "cancel"])
async def test_cancel_wins_a_delayed_bind_response_or_late_first_bind(
    db_session: AsyncSession, first_action: Literal["bind", "cancel"]
) -> None:
    request, _ = await persist_review(db_session)
    context = runtime(db_session)
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    await binding(db_session, request, first_action)
    holder_pid = await db_session.scalar(text("SELECT pg_backend_pid()"))
    waiting_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    second_action: Literal["bind", "cancel"] = "cancel" if first_action == "bind" else "bind"

    async def competitor() -> None:
        async with context.factory() as session:
            session.info["receiving_runtime"] = context
            await set_receiving_context(session, context.stock, context.actor_id)
            pid = await session.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(pid, int)
            waiting_pid.set_result(pid)
            if second_action == "bind":
                with pytest.raises(ConflictError):
                    await binding(session, request, "bind")
            else:
                assert (await binding(session, request, "cancel")).state == "cancelled"
            await session.commit()

    waiting = asyncio.create_task(competitor())
    try:
        pid = await asyncio.wait_for(waiting_pid, timeout=5)
        async with asyncio.timeout(10):
            while not await db_session.scalar(
                text("SELECT :holder = ANY(pg_blocking_pids(:waiting))"),
                {"holder": holder_pid, "waiting": pid},
            ):
                if waiting.done():
                    await waiting
                    pytest.fail("Competing command bypassed the operation transaction lock")
                await asyncio.sleep(0)
        await db_session.commit()
        await asyncio.wait_for(waiting, timeout=10)
    finally:
        if not waiting.done():
            waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    cancelled = await binding(db_session, request, "read")
    assert cancelled.state == "cancelled"
    with pytest.raises(ConflictError):
        await binding(db_session, request, "bind")


@pytest.mark.parametrize("target", ["return_sale", "legacy_attempt"])
async def test_binding_fences_direct_return_or_legacy_attempt_inserts(
    db_session: AsyncSession, target: str
) -> None:
    request, _ = await persist_review(db_session)
    await binding(db_session, request, "bind")
    if target == "return_sale":
        statement = (
            "INSERT INTO sale(id,tenant_id,branch_id,register_id,shift_id,sale_type,"
            "parent_sale_id,status,cashier_user_id) "
            "SELECT :id,tenant_id,branch_id,register_id,shift_id,'return',id,'draft',"
            "cashier_user_id "
            "FROM sale WHERE id=:parent"
        )
    else:
        statement = (
            "INSERT INTO pos_refund_attempt(id,tenant_id,parent_sale_id,register_id,"
            "requested_by_user_id,operation_id,operation_hash,items_json,external_allocations_json,"
            "intent_version,total_amount,external_amount,created_by) "
            "SELECT :id,tenant_id,id,register_id,cashier_user_id,:operation,repeat('a',64),"
            "'[]'::jsonb,'[]'::jsonb,1,0.99,0.99,cashier_user_id FROM sale WHERE id=:parent"
        )
    before = await facts(db_session)
    with pytest.raises(DBAPIError, match="unit_electronic_refund_binding_parent_frozen"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(statement),
                {"id": uuid4(), "operation": uuid4(), "parent": request.request.parent_sale_id},
            )
    assert await facts(db_session) == before


async def test_wrong_tenant_context_cannot_discover_binding_or_cancellation(
    db_session: AsyncSession,
) -> None:
    request, _ = await persist_review(db_session)
    context = runtime(db_session)
    await binding(db_session, request, "bind")
    await binding(db_session, request, "cancel")
    await db_session.execute(
        text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(uuid4())}
    )
    for table in (
        "pos_unit_electronic_refund_binding",
        "pos_unit_electronic_refund_binding_cancel",
    ):
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE id=:id"), {"id": request.operation_id}
            )
            == 0
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert (await binding(db_session, request, "read")).state == "cancelled"
