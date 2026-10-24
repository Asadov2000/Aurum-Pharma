"""Real binding locks fence competing returns without moving money or stock."""

import asyncio
from typing import Literal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import AurumError, ConflictError, NotFoundError
from app.domains.pos.mixed_electronic_refund_binding_repository import (
    MixedElectronicRefundBindingRepository,
)
from app.domains.pos.mixed_electronic_refund_binding_schemas import (
    MixedElectronicRefundBindingRead,
)
from app.domains.pos.mixed_electronic_refund_binding_service import (
    MixedElectronicRefundBindingService,
)
from app.domains.pos.mixed_electronic_refund_intent_repository import (
    MixedElectronicRefundIntentRepository,
)
from app.domains.pos.mixed_electronic_refund_intent_schemas import (
    SavedMixedElectronicRefundIntent,
    SaveMixedElectronicRefundIntentRequest,
)
from app.domains.pos.mixed_electronic_refund_intent_service import (
    MixedElectronicRefundIntentService,
)
from app.domains.pos.mixed_return_journal_repository import MixedReturnJournalRepository
from app.domains.pos.mixed_return_journal_service import MixedReturnJournalService
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import (
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.pos.test_mixed_electronic_refund_intent import save_review
from tests.domains.pos.test_mixed_electronic_refund_preview import financial_facts
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def facts(session: AsyncSession) -> dict[str, list[object]]:
    result = await financial_facts(session)
    preparation_tables = {
        "pos_mixed_electronic_refund_binding",
        "pos_mixed_electronic_refund_binding_cancel",
        "pos_mixed_electronic_refund_activation",
    }
    result["audit_log"] = [
        row
        for row in result["audit_log"]
        if not isinstance(row, dict) or row.get("table_name") not in preparation_tables
    ]
    return result


async def persist_review(
    session: AsyncSession,
) -> tuple[SaveMixedElectronicRefundIntentRequest, SavedMixedElectronicRefundIntent]:
    request = await save_review(session)
    context = runtime(session)
    saved = await MixedElectronicRefundIntentService(
        MixedElectronicRefundIntentRepository(session)
    ).save(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        request=request,
    )
    return request, saved


async def binding(
    session: AsyncSession,
    request: SaveMixedElectronicRefundIntentRequest,
    action: Literal["bind", "cancel", "read"],
    actor: UUID | None = None,
) -> MixedElectronicRefundBindingRead:
    context = runtime(session)
    service = MixedElectronicRefundBindingService(MixedElectronicRefundBindingRepository(session))
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
        "pos_mixed_electronic_refund_binding",
        "pos_mixed_electronic_refund_binding_cancel",
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
    await MixedElectronicRefundIntentService(
        MixedElectronicRefundIntentRepository(db_session)
    ).save(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        request=fresh,
    )
    assert (await binding(db_session, fresh, "bind")).state == "bound"


@pytest.mark.parametrize(
    "table", ["pos_mixed_electronic_refund_binding", "pos_mixed_electronic_refund_binding_cancel"]
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


async def test_active_binding_fences_mixed_return_until_cancelled(
    db_session: AsyncSession,
) -> None:
    request, _ = await persist_review(db_session)
    context = runtime(db_session)
    await binding(db_session, request, "bind")
    item = next(
        item for item in request.request.selection.items if item.inventory_kind == "physical_unit"
    )
    selections = (PhysicalReturnSelection(item.line_id, item.sale_format, item.quantity),)
    service = MixedReturnJournalService(MixedReturnJournalRepository(db_session))
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
    await MixedElectronicRefundIntentService(
        MixedElectronicRefundIntentRepository(db_session)
    ).save(
        tenant_id=context.stock.tenant_id,
        actor_id=second_actor,
        allowed_branch_ids={context.stock.branch_id},
        request=second,
    )
    await db_session.commit()
    start = asyncio.Event()

    async def contender(request: SaveMixedElectronicRefundIntentRequest, actor: UUID) -> bool:
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
        "pos_mixed_electronic_refund_binding",
        "pos_mixed_electronic_refund_binding_cancel",
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
    await MixedElectronicRefundIntentService(
        MixedElectronicRefundIntentRepository(db_session)
    ).save(
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
    actions: tuple[
        tuple[SaveMixedElectronicRefundIntentRequest, Literal["cancel", "bind"]], ...
    ] = (
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
    with pytest.raises(DBAPIError, match="mixed_electronic_refund_binding_parent_frozen"):
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
        "pos_mixed_electronic_refund_binding",
        "pos_mixed_electronic_refund_binding_cancel",
    ):
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE id=:id"), {"id": request.operation_id}
            )
            == 0
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert (await binding(db_session, request, "read")).state == "cancelled"


async def raw_binding(
    session: AsyncSession, operation_id: UUID, action: Literal["bind", "cancel"] = "bind"
) -> None:
    """Reach the trigger directly, without service locks or revalidation."""
    table = (
        "pos_mixed_electronic_refund_binding"
        if action == "bind"
        else "pos_mixed_electronic_refund_binding_cancel"
    )
    assert await session.scalar(text("SELECT session_user")) == "aurum_app"
    assert await session.scalar(text("SHOW transaction_isolation")) == "read committed"
    inserted = await session.scalar(
        text(
            f"INSERT INTO {table} "
            "(tenant_id,id,parent_sale_id,branch_id,actor_id,created_by) "
            "SELECT tenant_id,id,parent_sale_id,branch_id,actor_id,actor_id "
            "FROM pos_mixed_electronic_refund_intent WHERE id=:id RETURNING id"
        ),
        {"id": operation_id},
    )
    assert inserted == operation_id


@pytest.mark.parametrize("holder_is_original_author", [True, False])
async def test_raw_binding_guards_serialize_distinct_authors_on_one_original_sale(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    holder_is_original_author: bool,
) -> None:
    first, _ = await persist_review(db_session)
    context = runtime(db_session)
    await db_session.commit()
    other = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, context.stock, other)
    second = first.model_copy(update={"operation_id": uuid4()})
    await MixedElectronicRefundIntentService(
        MixedElectronicRefundIntentRepository(db_session)
    ).save(
        tenant_id=context.stock.tenant_id,
        actor_id=other,
        allowed_branch_ids={context.stock.branch_id},
        request=second,
    )
    await db_session.commit()
    holder, holder_actor, loser, loser_actor = (
        (first, context.actor_id, second, other)
        if holder_is_original_author
        else (second, other, first, context.actor_id)
    )
    assert holder.operation_id != loser.operation_id and holder_actor != loser_actor
    await set_receiving_context(db_session, context.stock, holder_actor)
    before = await facts(db_session)
    await raw_binding(db_session, holder.operation_id)
    holder_pid = await db_session.scalar(text("SELECT pg_backend_pid()"))
    assert isinstance(holder_pid, int)
    waiting_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def contender() -> None:
        async with context.factory() as session:
            await set_receiving_context(session, context.stock, loser_actor)
            pid = await session.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(pid, int) and pid != holder_pid
            waiting_pid.set_result(pid)
            with pytest.raises(
                DBAPIError, match="mixed_electronic_refund_binding_already_active"
            ) as denied:
                await raw_binding(session, loser.operation_id)
            assert getattr(denied.value.orig, "sqlstate", None) == "23505"
            await session.rollback()

    waiting = asyncio.create_task(contender())
    try:
        pid = await asyncio.wait_for(waiting_pid, timeout=5)
        async with asyncio.timeout(10):
            while not await db_session.scalar(
                text("SELECT :holder = ANY(pg_blocking_pids(:waiting))"),
                {"holder": holder_pid, "waiting": pid},
            ):
                if waiting.done():
                    await waiting
                    pytest.fail("Raw competing INSERT bypassed the database guard lock")
                await asyncio.sleep(0)
        await db_session.commit()
        await asyncio.wait_for(waiting, timeout=10)
    finally:
        if not waiting.done():
            waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
    visible_counts = []
    for actor in (holder_actor, loser_actor):
        await set_receiving_context(db_session, context.stock, actor)
        visible_counts.append(
            await db_session.scalar(
                text(
                    "SELECT count(*) FROM pos_mixed_electronic_refund_binding b "
                    "WHERE b.tenant_id=:tenant AND b.parent_sale_id=:parent "
                    "AND NOT EXISTS(SELECT 1 FROM pos_mixed_electronic_refund_binding_cancel c "
                    "WHERE c.tenant_id=b.tenant_id AND c.id=b.id)"
                ),
                {"tenant": context.stock.tenant_id, "parent": first.request.parent_sale_id},
            )
        )
    assert visible_counts == [1, 0]
    await set_receiving_context(db_session, context.stock, holder_actor)
    assert await facts(db_session) == before
