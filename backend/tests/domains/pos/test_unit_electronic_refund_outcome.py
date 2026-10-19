"""Recover partial operator evidence without issuing or financially posting a refund."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import AurumError, ConflictError, NotFoundError
from app.domains.pos.unit_electronic_refund_activation_schemas import (
    SavedElectronicRefundActivation,
)
from app.domains.pos.unit_electronic_refund_intent_schemas import SaveElectronicRefundIntentRequest
from app.domains.pos.unit_electronic_refund_outcome_repository import (
    UnitElectronicRefundOutcomeRepository,
)
from app.domains.pos.unit_electronic_refund_outcome_schemas import (
    ElectronicRefundConfirmation,
    ReconciledElectronicRefund,
)
from app.domains.pos.unit_electronic_refund_outcome_service import (
    UnitElectronicRefundOutcomeService,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.pos.test_physical_cash_refund import close_shift
from tests.domains.pos.test_unit_electronic_refund_activation import activate
from tests.domains.pos.test_unit_electronic_refund_binding import binding
from tests.domains.pos.test_unit_electronic_refund_preview import facts
from tests.domains.pos.test_unit_electronic_refund_revalidation import persist_review
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def activated(
    session: AsyncSession,
) -> tuple[SaveElectronicRefundIntentRequest, SavedElectronicRefundActivation]:
    request, _ = await persist_review(session)
    await binding(session, request, "bind")
    return request, await activate(session, request)


def confirmation(payment_id: UUID, document: str | None = None) -> ElectronicRefundConfirmation:
    return ElectronicRefundConfirmation(
        original_payment_id=payment_id,
        terminal_id="outcome-terminal",
        document_number=document or str(uuid4()),
    )


async def outcome(
    session: AsyncSession,
    request: SaveElectronicRefundIntentRequest,
    evidence: ElectronicRefundConfirmation | None = None,
    *,
    actor_id: UUID | None = None,
) -> ReconciledElectronicRefund:
    context = runtime(session)
    service = UnitElectronicRefundOutcomeService(UnitElectronicRefundOutcomeRepository(session))
    arguments = {
        "tenant_id": context.stock.tenant_id,
        "actor_id": actor_id or context.actor_id,
        "allowed_branch_ids": {context.stock.branch_id},
        "parent_sale_id": request.request.parent_sale_id,
        "operation_id": request.operation_id,
    }
    if evidence is None:
        return await service.read(**arguments)
    return await service.record(**arguments, confirmation=evidence)


async def test_partial_then_complete_evidence_preserves_financial_and_stock_facts(
    db_session: AsyncSession,
) -> None:
    request, activation = await activated(db_session)
    expected = {
        part.original_payment_id: part
        for part in activation.review.preview.payments
        if part.method in {"card", "qr"} and part.amount > 0
    }
    assert len(expected) == 2
    before = await facts(db_session)
    initial = await outcome(db_session, request)
    assert initial.state == "requires_reconciliation"
    assert initial.confirmations == ()
    assert set(initial.missing_payment_ids) == set(expected)
    first_id, second_id = tuple(expected)
    first = confirmation(first_id)
    partial = await outcome(db_session, request, first)
    assert partial.state == "requires_reconciliation"
    assert partial.missing_payment_ids == (second_id,)
    assert len(partial.confirmations) == 1
    recorded = partial.confirmations[0]
    assert (recorded.method, recorded.amount, recorded.currency) == (
        expected[first_id].method,
        expected[first_id].amount,
        "TJS",
    )
    assert recorded.recorded_at >= activation.activated_at
    assert await outcome(db_session, request, first) == partial
    assert await outcome(db_session, request) == partial
    complete = await outcome(db_session, request, confirmation(second_id))
    assert complete.state == "confirmed_evidence_only"
    assert complete.activation == activation
    assert complete.missing_payment_ids == ()
    assert {part.original_payment_id for part in complete.confirmations} == set(expected)
    assert await outcome(db_session, request) == complete
    assert await facts(db_session) == before


async def test_changed_evidence_conflicts_without_replacing_saved_confirmation(
    db_session: AsyncSession,
) -> None:
    request, activation = await activated(db_session)
    payment = next(p for p in activation.review.preview.payments if p.method == "card")
    evidence = confirmation(payment.original_payment_id)
    saved = await outcome(db_session, request, evidence)
    with pytest.raises(ConflictError):
        await outcome(db_session, request, confirmation(payment.original_payment_id))
    assert await outcome(db_session, request) == saved


@pytest.mark.parametrize("payment_kind", ["cash", "unknown"])
async def test_only_frozen_electronic_payment_allocation_can_receive_evidence(
    db_session: AsyncSession, payment_kind: str
) -> None:
    request, activation = await activated(db_session)
    payment_id = (
        next(
            p.original_payment_id for p in activation.review.preview.payments if p.method == "cash"
        )
        if payment_kind == "cash"
        else uuid4()
    )
    before = await facts(db_session)
    with pytest.raises(AurumError):
        await outcome(db_session, request, confirmation(payment_id))
    assert (await outcome(db_session, request)).confirmations == ()
    assert await facts(db_session) == before


async def test_saved_review_without_activation_cannot_record_terminal_evidence(
    db_session: AsyncSession,
) -> None:
    request, review = await persist_review(db_session)
    await binding(db_session, request, "bind")
    payment = next(p for p in review.preview.payments if p.method == "card")
    with pytest.raises(AurumError):
        await outcome(db_session, request, confirmation(payment.original_payment_id))
    with pytest.raises(NotFoundError):
        await outcome(db_session, request)


async def test_closed_shift_allows_recording_and_restoring_external_result(
    db_session: AsyncSession,
) -> None:
    request, activation = await activated(db_session)
    await close_shift(db_session, activation.shift_id)
    payment = next(p for p in activation.review.preview.payments if p.method == "card")
    evidence = confirmation(payment.original_payment_id)
    saved = await outcome(db_session, request, evidence)
    assert await outcome(db_session, request, evidence) == saved
    assert await outcome(db_session, request) == saved


async def test_readonly_recovers_exact_evidence_but_rejects_missing_confirmation(
    db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    request, activation = await activated(db_session)
    payments = [p for p in activation.review.preview.payments if p.method in {"card", "qr"}]
    evidence = confirmation(payments[0].original_payment_id)
    saved = await outcome(db_session, request, evidence)
    context = runtime(db_session)
    await db_session.commit()
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:id"),
            {"id": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await outcome(db_session, request) == saved
    assert await outcome(db_session, request, evidence) == saved
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await outcome(db_session, request, confirmation(payments[1].original_payment_id))
    assert await outcome(db_session, request) == saved


async def test_other_refund_author_and_wrong_tenant_cannot_discover_outcome(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    request, activation = await activated(db_session)
    payment = next(p for p in activation.review.preview.payments if p.method == "card")
    saved = await outcome(db_session, request, confirmation(payment.original_payment_id))
    context = runtime(db_session)
    await db_session.commit()
    other = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, context.stock, other)
    with pytest.raises(NotFoundError):
        await outcome(db_session, request, actor_id=other)
    assert (
        await db_session.scalar(text("SELECT count(*) FROM pos_unit_electronic_refund_outcome"))
        == 0
    )
    await db_session.execute(
        text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(uuid4())}
    )
    assert (
        await db_session.scalar(text("SELECT count(*) FROM pos_unit_electronic_refund_outcome"))
        == 0
    )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await outcome(db_session, request) == saved


@pytest.mark.parametrize("verb", ["UPDATE", "DELETE"])
async def test_recorded_terminal_evidence_is_append_only(
    db_session: AsyncSession, verb: str
) -> None:
    request, activation = await activated(db_session)
    payment = next(p for p in activation.review.preview.payments if p.method == "card")
    saved = await outcome(db_session, request, confirmation(payment.original_payment_id))
    statement = (
        "UPDATE pos_unit_electronic_refund_outcome SET amount=amount WHERE operation_id=:id"
        if verb == "UPDATE"
        else "DELETE FROM pos_unit_electronic_refund_outcome WHERE operation_id=:id"
    )
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(text(statement), {"id": request.operation_id})
    assert await outcome(db_session, request) == saved


async def test_original_checkout_terminal_document_cannot_be_reused_as_refund_evidence(
    db_session: AsyncSession,
) -> None:
    request, activation = await activated(db_session)
    payment = next(p for p in activation.review.preview.payments if p.method == "card")
    terminal = (
        await db_session.execute(
            text(
                "SELECT a.terminal_id,a.external_reference FROM sale_payment p "
                "JOIN pos_payment_attempt a ON a.tenant_id=p.tenant_id "
                "AND a.id=p.payment_attempt_id WHERE p.id=:id"
            ),
            {"id": payment.original_payment_id},
        )
    ).one()
    with pytest.raises(AurumError):
        await outcome(
            db_session,
            request,
            ElectronicRefundConfirmation(
                original_payment_id=payment.original_payment_id,
                terminal_id=terminal.terminal_id,
                document_number=terminal.external_reference,
            ),
        )
    assert (await outcome(db_session, request)).confirmations == ()


async def test_raw_insert_cannot_change_frozen_allocation_amount(db_session: AsyncSession) -> None:
    request, activation = await activated(db_session)
    payment = next(p for p in activation.review.preview.payments if p.method == "card")
    context = runtime(db_session)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO pos_unit_electronic_refund_outcome "
                    "(tenant_id,id,operation_id,original_payment_id,parent_sale_id,branch_id,"
                    "actor_id,method,amount,currency,terminal_id,document_number,created_by) "
                    "VALUES (:tenant,:id,:operation,:payment,:parent,:branch,:actor,"
                    "'card',:amount,'TJS','raw-terminal','raw-document',:actor)"
                ),
                {
                    "tenant": context.stock.tenant_id,
                    "id": uuid4(),
                    "operation": request.operation_id,
                    "payment": payment.original_payment_id,
                    "parent": request.request.parent_sale_id,
                    "branch": context.stock.branch_id,
                    "actor": context.actor_id,
                    "amount": payment.amount + Decimal("0.01"),
                },
            )
    assert (await outcome(db_session, request)).confirmations == ()
