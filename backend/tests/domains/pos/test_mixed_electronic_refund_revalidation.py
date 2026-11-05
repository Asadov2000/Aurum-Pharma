"""Real RLS/transaction locks for read-only electronic review revalidation."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import AurumError, BusinessRuleError, NotFoundError
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
from app.domains.pos.mixed_electronic_refund_revalidation import (
    MixedElectronicRefundRevalidationService,
)
from app.domains.pos.mixed_return_journal_repository import MixedReturnJournalRepository
from app.domains.pos.mixed_return_journal_service import MixedReturnJournalService
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.pos.test_mixed_electronic_refund_intent import save_review
from tests.domains.pos.test_mixed_electronic_refund_preview import financial_facts as facts
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


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


async def revalidate(
    session: AsyncSession, request: SaveMixedElectronicRefundIntentRequest
) -> SavedMixedElectronicRefundIntent:
    context = runtime(session)
    return await MixedElectronicRefundRevalidationService(
        MixedElectronicRefundIntentRepository(session)
    ).revalidate_locked_review(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        parent_sale_id=request.request.parent_sale_id,
        operation_id=request.operation_id,
    )


async def test_revalidation_preserves_review_and_all_financial_facts(
    db_session: AsyncSession,
) -> None:
    request, saved = await persist_review(db_session)
    before = await facts(db_session)
    assert await revalidate(db_session, request) == saved
    assert await revalidate(db_session, request) == saved
    assert await facts(db_session) == before
    assert (
        await db_session.scalar(text("SELECT count(*) FROM pos_mixed_electronic_refund_intent"))
        == 1
    )


async def test_parent_lock_belongs_to_callers_transaction(db_session: AsyncSession) -> None:
    request, saved = await persist_review(db_session)
    context = runtime(db_session)
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await revalidate(db_session, request) == saved
    statement = text("SELECT id FROM sale WHERE tenant_id=:tenant AND id=:id FOR UPDATE NOWAIT")
    values = {"tenant": context.stock.tenant_id, "id": request.request.parent_sale_id}
    async with context.factory() as competitor:
        await set_receiving_context(competitor, context.stock, context.actor_id)
        with pytest.raises(DBAPIError) as denied:
            async with competitor.begin_nested():
                await competitor.execute(statement, values)
        assert getattr(denied.value.orig, "sqlstate", None) == "55P03"
        await db_session.rollback()
        assert await competitor.scalar(statement, values) == request.request.parent_sale_id


async def test_waiting_revalidation_reads_history_committed_while_waiting(
    db_session: AsyncSession,
) -> None:
    request, saved = await persist_review(db_session)
    context = runtime(db_session)
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    await MixedReturnJournalService(MixedReturnJournalRepository(db_session)).receive(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        operation_id=uuid4(),
        parent_sale_id=request.request.parent_sale_id,
        selections=request.request.selection.selections(),
    )
    # The quarantine-only history is intentionally unreconciled, not a payout.
    before = await facts(db_session)
    holder_pid = await db_session.scalar(text("SELECT pg_backend_pid()"))
    waiting_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def waiting_review() -> None:
        async with context.factory() as session:
            session.info["receiving_runtime"] = context
            await set_receiving_context(session, context.stock, context.actor_id)
            pid = await session.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(pid, int)
            waiting_pid.set_result(pid)
            with pytest.raises(AurumError, match="reconciliation"):
                await revalidate(session, request)
            assert (
                await MixedElectronicRefundIntentService(
                    MixedElectronicRefundIntentRepository(session)
                ).read(
                    tenant_id=context.stock.tenant_id,
                    actor_id=context.actor_id,
                    allowed_branch_ids={context.stock.branch_id},
                    parent_sale_id=request.request.parent_sale_id,
                    operation_id=request.operation_id,
                )
                == saved
            )

    waiting = asyncio.create_task(waiting_review())
    try:
        pid = await asyncio.wait_for(waiting_pid, timeout=5)
        async with asyncio.timeout(10):
            while not await db_session.scalar(
                text("SELECT :holder = ANY(pg_blocking_pids(:waiting))"),
                {"holder": holder_pid, "waiting": pid},
            ):
                if waiting.done():
                    await waiting
                    pytest.fail("Revalidation bypassed the original sale lock")
                await asyncio.sleep(0)
        await db_session.commit()
        await asyncio.wait_for(waiting, timeout=10)
    finally:
        if not waiting.done():
            waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await facts(db_session) == before


@pytest.mark.parametrize("isolation", ["REPEATABLE READ", "SERIALIZABLE"])
async def test_repeatable_snapshots_are_rejected_by_real_transaction_setting(
    db_session: AsyncSession, isolation: str
) -> None:
    request, _ = await persist_review(db_session)
    context = runtime(db_session)
    await db_session.commit()
    # First statement of this fresh synthetic transaction; no stale report is used.
    await db_session.execute(text(f"SET TRANSACTION ISOLATION LEVEL {isolation}"))
    await set_receiving_context(db_session, context.stock, context.actor_id)
    with pytest.raises(BusinessRuleError, match="READ COMMITTED"):
        await revalidate(db_session, request)


async def test_refund_only_author_can_lock_another_cashiers_original(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    request = await save_review(db_session)
    context = runtime(db_session)
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, context.stock, actor)
    service = MixedElectronicRefundIntentService(MixedElectronicRefundIntentRepository(db_session))
    saved = await service.save(
        tenant_id=context.stock.tenant_id,
        actor_id=actor,
        allowed_branch_ids={context.stock.branch_id},
        request=request,
    )
    before = await facts(db_session)
    assert (
        await MixedElectronicRefundRevalidationService(
            MixedElectronicRefundIntentRepository(db_session)
        ).revalidate_locked_review(
            tenant_id=context.stock.tenant_id,
            actor_id=actor,
            allowed_branch_ids={context.stock.branch_id},
            parent_sale_id=request.request.parent_sale_id,
            operation_id=request.operation_id,
        )
        == saved
    )
    assert await facts(db_session) == before
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    # Sale author cannot use the other operator's saved review.
    with pytest.raises(NotFoundError):
        await revalidate(db_session, request)


async def test_readonly_revalidation_is_only_a_read_not_write_authorization(
    db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    request, saved = await persist_review(db_session)
    context = runtime(db_session)
    await db_session.commit()
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:tenant"),
            {"tenant": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await facts(db_session)
    assert await revalidate(db_session, request) == saved
    assert await facts(db_session) == before
