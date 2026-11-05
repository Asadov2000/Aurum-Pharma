"""Durable activation closes cancellation while preserving money and stock facts."""

import asyncio
from typing import Literal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import AurumError, ConflictError, NotFoundError
from app.domains.pos.unit_electronic_refund_activation_repository import (
    UnitElectronicRefundActivationRepository,
)
from app.domains.pos.unit_electronic_refund_activation_schemas import (
    SavedElectronicRefundActivation,
)
from app.domains.pos.unit_electronic_refund_activation_service import (
    UnitElectronicRefundActivationService,
)
from app.domains.pos.unit_electronic_refund_intent_schemas import SaveElectronicRefundIntentRequest
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.pos.test_physical_cash_refund import close_shift
from tests.domains.pos.test_unit_electronic_refund_binding import binding
from tests.domains.pos.test_unit_electronic_refund_preview import facts
from tests.domains.pos.test_unit_electronic_refund_revalidation import persist_review
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def activate(
    session: AsyncSession,
    request: SaveElectronicRefundIntentRequest,
    action: Literal["start", "read"] = "start",
    *,
    shift_id: UUID | None = None,
    actor_id: UUID | None = None,
) -> SavedElectronicRefundActivation:
    context = runtime(session)
    service = UnitElectronicRefundActivationService(
        UnitElectronicRefundActivationRepository(session)
    )
    if action == "read":
        return await service.read(
            tenant_id=context.stock.tenant_id,
            actor_id=actor_id or context.actor_id,
            allowed_branch_ids={context.stock.branch_id},
            parent_sale_id=request.request.parent_sale_id,
            operation_id=request.operation_id,
        )
    if shift_id is None:
        shift_id = await session.scalar(
            text("SELECT shift_id FROM sale WHERE id=:id"),
            {"id": request.request.parent_sale_id},
        )
        assert isinstance(shift_id, UUID)
    return await service.start(
        tenant_id=context.stock.tenant_id,
        actor_id=actor_id or context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        parent_sale_id=request.request.parent_sale_id,
        operation_id=request.operation_id,
        shift_id=shift_id,
    )


async def test_activation_replay_read_and_raw_cancel_guard_preserve_financial_facts(
    db_session: AsyncSession,
) -> None:
    request, review = await persist_review(db_session)
    await binding(db_session, request, "bind")
    before = await facts(db_session)
    saved = await activate(db_session, request)
    assert saved.state == "activation_saved"
    assert saved.operation_id == request.operation_id
    assert saved.review == review
    assert saved.activated_at.tzinfo is not None
    assert await activate(db_session, request) == saved
    assert await activate(db_session, request, "read") == saved
    assert await facts(db_session) == before
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_electronic_refund_activation WHERE id=:id"),
            {"id": request.operation_id},
        )
        == 1
    )
    # Both the service path and a direct app INSERT must be fenced after activation.
    with pytest.raises(
        DBAPIError, match="unit_electronic_refund_activation_cancel_denied"
    ) as denied:
        async with db_session.begin_nested():
            await binding(db_session, request, "cancel")
    assert getattr(denied.value.orig, "sqlstate", None) == "23514"
    with pytest.raises(DBAPIError, match="unit_electronic_refund_activation_cancel_denied"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO pos_unit_electronic_refund_binding_cancel "
                    "(tenant_id,id,parent_sale_id,branch_id,actor_id,created_by) "
                    "SELECT tenant_id,id,parent_sale_id,branch_id,actor_id,created_by "
                    "FROM pos_unit_electronic_refund_binding WHERE id=:id"
                ),
                {"id": request.operation_id},
            )
    assert await activate(db_session, request, "read") == saved
    assert await facts(db_session) == before


async def test_closed_shift_recovery_is_exact_but_changed_shift_conflicts(
    db_session: AsyncSession,
) -> None:
    request, _ = await persist_review(db_session)
    await binding(db_session, request, "bind")
    saved = await activate(db_session, request)
    await close_shift(db_session, saved.shift_id)
    before = await facts(db_session)
    assert await activate(db_session, request, shift_id=saved.shift_id) == saved
    assert await activate(db_session, request, "read") == saved
    with pytest.raises(ConflictError):
        await activate(db_session, request, shift_id=uuid4())
    assert await facts(db_session) == before


@pytest.mark.parametrize("preparation", ["missing", "cancelled"])
async def test_missing_or_cancelled_preparation_never_activates(
    db_session: AsyncSession, preparation: str
) -> None:
    request, _ = await persist_review(db_session)
    if preparation == "cancelled":
        await binding(db_session, request, "bind")
        await binding(db_session, request, "cancel")
    before = await facts(db_session)
    with pytest.raises(ConflictError):
        await activate(db_session, request)
    assert await facts(db_session) == before
    with pytest.raises(NotFoundError):
        await activate(db_session, request, "read")


@pytest.mark.parametrize("invalid", ["missing_shift", "closed_shift", "inactive_register"])
async def test_new_activation_requires_open_shift_and_active_original_register(
    db_session: AsyncSession, invalid: str
) -> None:
    request, _ = await persist_review(db_session)
    await binding(db_session, request, "bind")
    row = (
        await db_session.execute(
            text("SELECT shift_id,register_id FROM sale WHERE id=:id"),
            {"id": request.request.parent_sale_id},
        )
    ).one()
    if invalid == "closed_shift":
        await close_shift(db_session, row.shift_id)
    elif invalid == "inactive_register":
        await db_session.execute(
            text("UPDATE register SET is_active=false WHERE id=:id"), {"id": row.register_id}
        )
    before = await facts(db_session)
    with pytest.raises(AurumError):
        await activate(
            db_session, request, shift_id=uuid4() if invalid == "missing_shift" else row.shift_id
        )
    assert await facts(db_session) == before
    assert (
        await db_session.scalar(text("SELECT count(*) FROM pos_unit_electronic_refund_activation"))
        == 0
    )


@pytest.mark.parametrize("verb", ["UPDATE", "DELETE"])
async def test_activation_is_append_only_even_with_raw_sql(
    db_session: AsyncSession, verb: str
) -> None:
    request, _ = await persist_review(db_session)
    await binding(db_session, request, "bind")
    saved = await activate(db_session, request)
    statement = (
        "UPDATE pos_unit_electronic_refund_activation SET created_at=created_at WHERE id=:id"
        if verb == "UPDATE"
        else "DELETE FROM pos_unit_electronic_refund_activation WHERE id=:id"
    )
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(text(statement), {"id": request.operation_id})
    assert await activate(db_session, request, "read") == saved


async def test_readonly_restores_activation_but_rejects_a_new_marker(
    db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    request, _ = await persist_review(db_session)
    await binding(db_session, request, "bind")
    saved = await activate(db_session, request)
    # A second completed original and bound review exist before readonly takes effect.
    second, _ = await persist_review(db_session)
    await binding(db_session, second, "bind")
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
    assert await activate(db_session, request, "read") == saved
    assert await activate(db_session, request, shift_id=saved.shift_id) == saved
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await activate(db_session, second)
    assert await facts(db_session) == before


async def test_other_author_and_wrong_tenant_cannot_discover_activation(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    request, _ = await persist_review(db_session)
    await binding(db_session, request, "bind")
    saved = await activate(db_session, request)
    context = runtime(db_session)
    await db_session.commit()
    other = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, context.stock, other)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_electronic_refund_activation WHERE id=:id"),
            {"id": request.operation_id},
        )
        == 0
    )
    with pytest.raises(NotFoundError):
        await activate(db_session, request, "read", actor_id=other)
    await db_session.execute(
        text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(uuid4())}
    )
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_unit_electronic_refund_activation WHERE id=:id"),
            {"id": request.operation_id},
        )
        == 0
    )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await activate(db_session, request, "read") == saved


@pytest.mark.parametrize("first_action", ["start", "cancel"])
async def test_activation_and_cancellation_serialize_to_one_final_outcome(
    db_session: AsyncSession, first_action: Literal["start", "cancel"]
) -> None:
    request, _ = await persist_review(db_session)
    await binding(db_session, request, "bind")
    context = runtime(db_session)
    shift_id = await db_session.scalar(
        text("SELECT shift_id FROM sale WHERE id=:id"), {"id": request.request.parent_sale_id}
    )
    assert isinstance(shift_id, UUID)
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    if first_action == "start":
        await activate(db_session, request, shift_id=shift_id)
    else:
        await binding(db_session, request, "cancel")
    holder_pid = await db_session.scalar(text("SELECT pg_backend_pid()"))
    waiting_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def contender() -> None:
        async with context.factory() as session:
            session.info["receiving_runtime"] = context
            await set_receiving_context(session, context.stock, context.actor_id)
            pid = await session.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(pid, int)
            waiting_pid.set_result(pid)
            if first_action == "start":
                with pytest.raises(
                    DBAPIError, match="unit_electronic_refund_activation_cancel_denied"
                ):
                    await binding(session, request, "cancel")
            else:
                with pytest.raises(ConflictError):
                    await activate(session, request, shift_id=shift_id)
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
                    pytest.fail("Competing command bypassed activation transaction lock")
                await asyncio.sleep(0)
        await db_session.commit()
        await asyncio.wait_for(waiting, timeout=10)
    finally:
        if not waiting.done():
            waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    if first_action == "start":
        assert (
            (await activate(db_session, request, "read")).state,
            (await binding(db_session, request, "read")).state,
        ) == ("activation_saved", "bound")
    else:
        assert (await binding(db_session, request, "read")).state == "cancelled"
        with pytest.raises(NotFoundError):
            await activate(db_session, request, "read")
