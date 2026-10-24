"""Recover partial operator evidence without issuing or financially posting a refund."""

import asyncio
from decimal import Decimal
from typing import Literal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import AurumError, ConflictError, NotFoundError
from app.domains.pos.mixed_electronic_refund_activation_schemas import (
    SavedMixedElectronicRefundActivation,
)
from app.domains.pos.mixed_electronic_refund_intent_schemas import (
    SaveMixedElectronicRefundIntentRequest,
)
from app.domains.pos.mixed_electronic_refund_outcome_repository import (
    MixedElectronicRefundOutcomeRepository,
)
from app.domains.pos.mixed_electronic_refund_outcome_schemas import (
    MixedElectronicRefundConfirmation,
    ReconciledMixedElectronicRefund,
)
from app.domains.pos.mixed_electronic_refund_outcome_service import (
    MixedElectronicRefundOutcomeService,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import (
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.pos.test_mixed_electronic_refund_activation import activate
from tests.domains.pos.test_mixed_electronic_refund_binding import (
    binding,
    persist_review,
)
from tests.domains.pos.test_mixed_electronic_refund_binding import (
    facts as preparation_facts,
)
from tests.domains.pos.test_physical_cash_refund import close_shift
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def facts(session: AsyncSession) -> dict[str, list[object]]:
    result = await preparation_facts(session)
    result["audit_log"] = [
        row
        for row in result["audit_log"]
        if not isinstance(row, dict)
        or row.get("table_name") != "pos_mixed_electronic_refund_outcome"
    ]
    return result


async def activated(
    session: AsyncSession,
) -> tuple[SaveMixedElectronicRefundIntentRequest, SavedMixedElectronicRefundActivation]:
    request, _ = await persist_review(session)
    await binding(session, request, "bind")
    return request, await activate(session, request)


def confirmation(
    payment_id: UUID, document: str | None = None
) -> MixedElectronicRefundConfirmation:
    return MixedElectronicRefundConfirmation(
        original_payment_id=payment_id,
        terminal_id="outcome-terminal",
        document_number=document or str(uuid4()),
    )


async def outcome(
    session: AsyncSession,
    request: SaveMixedElectronicRefundIntentRequest,
    evidence: MixedElectronicRefundConfirmation | None = None,
    *,
    actor_id: UUID | None = None,
) -> ReconciledMixedElectronicRefund:
    context = runtime(session)
    service = MixedElectronicRefundOutcomeService(MixedElectronicRefundOutcomeRepository(session))
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
        await db_session.scalar(text("SELECT count(*) FROM pos_mixed_electronic_refund_outcome"))
        == 0
    )
    await db_session.execute(
        text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(uuid4())}
    )
    assert (
        await db_session.scalar(text("SELECT count(*) FROM pos_mixed_electronic_refund_outcome"))
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
        "UPDATE pos_mixed_electronic_refund_outcome SET amount=amount WHERE operation_id=:id"
        if verb == "UPDATE"
        else "DELETE FROM pos_mixed_electronic_refund_outcome WHERE operation_id=:id"
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
            MixedElectronicRefundConfirmation(
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
                    "INSERT INTO pos_mixed_electronic_refund_outcome "
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


async def activated_payment_ids(session: AsyncSession) -> dict[str, tuple[UUID, UUID]]:
    from tests.domains.pos.test_unit_electronic_refund_outcome import (
        activated as physical_activated,
    )

    mixed_request, mixed = await activated(session)
    physical_request, physical = await physical_activated(session)
    return {
        "mixed": (
            mixed_request.operation_id,
            next(
                p.original_payment_id for p in mixed.review.preview.payments if p.method == "card"
            ),
        ),
        "physical": (
            physical_request.operation_id,
            next(
                p.original_payment_id
                for p in physical.review.preview.payments
                if p.method == "card"
            ),
        ),
    }


async def raw_outcome(
    session: AsyncSession,
    kind: Literal["mixed", "physical"],
    operation: UUID,
    payment_id: UUID,
    document: str,
    *,
    terminal_id: str = "cross-outcome-terminal",
) -> None:
    prefix = "pos_mixed_electronic_refund" if kind == "mixed" else "pos_unit_electronic_refund"
    assert await session.scalar(text("SELECT session_user")) == "aurum_app"
    assert await session.scalar(text("SHOW transaction_isolation")) == "read committed"
    inserted = await session.scalar(
        text(
            f"INSERT INTO {prefix}_outcome "
            "(tenant_id,id,operation_id,parent_sale_id,branch_id,actor_id,original_payment_id,"
            "method,amount,currency,terminal_id,document_number,created_by) "
            "SELECT a.tenant_id,:id,a.id,a.parent_sale_id,a.branch_id,a.actor_id,"
            "CAST(p.value->>'original_payment_id' AS uuid),p.value->>'method',"
            "CAST(p.value->>'amount' AS numeric),p.value->>'currency',"
            ":terminal,:document,a.actor_id "
            f"FROM {prefix}_activation a JOIN {prefix}_intent r "
            "ON r.tenant_id=a.tenant_id AND r.id=a.id "
            "CROSS JOIN LATERAL jsonb_array_elements(r.result_payload->'payments') p "
            "WHERE a.id=:operation AND p.value->>'original_payment_id'=:payment RETURNING id"
        ),
        {
            "id": uuid4(),
            "operation": operation,
            "payment": str(payment_id),
            "document": document,
            "terminal": terminal_id,
        },
    )
    assert isinstance(inserted, UUID)


async def seed_legacy_refund_reference(
    db_engine: AsyncEngine,
    *,
    tenant_id: UUID,
    parent_sale_id: UUID,
    terminal_id: str,
    document: str,
) -> UUID:
    # Seed a separate legacy sale/attempt with all production guards enabled.
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        sale_id = await support.scalar(
            text(
                "INSERT INTO sale "
                "(tenant_id,branch_id,register_id,shift_id,cashier_user_id) "
                "SELECT tenant_id,branch_id,register_id,shift_id,cashier_user_id "
                "FROM sale WHERE tenant_id=:tenant AND id=:parent RETURNING id"
            ),
            {"tenant": tenant_id, "parent": parent_sale_id},
        )
        assert isinstance(sale_id, UUID)
        attempt_id = await support.scalar(
            text(
                "INSERT INTO pos_refund_attempt "
                "(tenant_id,parent_sale_id,register_id,requested_by_user_id,operation_id,"
                "operation_hash,items_json,external_allocations_json,total_amount,external_amount) "
                "SELECT tenant_id,id,register_id,cashier_user_id,:operation,repeat('a',64),"
                "jsonb_build_array(jsonb_build_object('sale_item_id',"
                "CAST(:item AS text),'qty','1')),"
                "jsonb_build_array(jsonb_build_object('payment_method','card','amount','10.00')),"
                "10,10 FROM sale WHERE tenant_id=:tenant AND id=:sale RETURNING id"
            ),
            {"tenant": tenant_id, "sale": sale_id, "operation": uuid4(), "item": str(uuid4())},
        )
        assert isinstance(attempt_id, UUID)
        reference_id = await support.scalar(
            text(
                "INSERT INTO pos_refund_reference "
                "(tenant_id,refund_attempt_id,payment_method,amount,terminal_id,document_number,"
                "confirmed_by_user_id) SELECT tenant_id,id,'card',10,:terminal,:document,"
                "requested_by_user_id FROM pos_refund_attempt "
                "WHERE tenant_id=:tenant AND id=:attempt RETURNING id"
            ),
            {
                "tenant": tenant_id,
                "attempt": attempt_id,
                "terminal": terminal_id,
                "document": document,
            },
        )
        assert isinstance(reference_id, UUID)
        return reference_id


@pytest.mark.parametrize("legacy_kind", ["payment", "refund"])
async def test_raw_mixed_evidence_cannot_reuse_existing_legacy_reference(
    db_session: AsyncSession, db_engine: AsyncEngine, legacy_kind: str
) -> None:
    request, activation = await activated(db_session)
    payment = next(p for p in activation.review.preview.payments if p.method == "card")
    context = runtime(db_session)
    terminal_id, document = "legacy-refund-terminal", str(uuid4())
    await db_session.commit()
    if legacy_kind == "refund":
        reference_id = await seed_legacy_refund_reference(
            db_engine,
            tenant_id=context.stock.tenant_id,
            parent_sale_id=request.request.parent_sale_id,
            terminal_id=terminal_id,
            document=document,
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    if legacy_kind == "payment":
        reference = (
            await db_session.execute(
                text(
                    "SELECT a.id,a.terminal_id,a.external_reference FROM sale_payment p "
                    "JOIN pos_payment_attempt a ON a.tenant_id=p.tenant_id "
                    "AND a.id=p.payment_attempt_id WHERE p.id=:payment"
                ),
                {"payment": payment.original_payment_id},
            )
        ).one()
        reference_id, terminal_id, document = reference
    else:
        assert (
            await db_session.scalar(
                text("SELECT id FROM pos_refund_reference WHERE id=:id"), {"id": reference_id}
            )
            == reference_id
        )
    before = await facts(db_session)
    with pytest.raises(DBAPIError, match="Terminal evidence was already used") as denied:
        async with db_session.begin_nested():
            await raw_outcome(
                db_session,
                "mixed",
                request.operation_id,
                payment.original_payment_id,
                document,
                terminal_id=terminal_id,
            )
    assert getattr(denied.value.orig, "sqlstate", None) == "23505"
    assert (
        getattr(denied.value.orig.__cause__, "constraint_name", None)
        == "uq_pos_terminal_evidence_cross_type"
    )
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM pos_mixed_electronic_refund_outcome WHERE operation_id=:id"),
            {"id": request.operation_id},
        )
        == 0
    )
    assert await facts(db_session) == before


async def cross_type_race_facts(session: AsyncSession) -> dict[str, list[object]]:
    result = await facts(session)
    result["audit_log"] = [
        row
        for row in result["audit_log"]
        if not isinstance(row, dict)
        or row.get("table_name") != "pos_unit_electronic_refund_outcome"
    ]
    return result


@pytest.mark.parametrize("first_kind", ["mixed", "physical"])
async def test_raw_mixed_and_physical_evidence_serialize_one_terminal_document(
    db_session: AsyncSession, first_kind: Literal["mixed", "physical"]
) -> None:
    ids = await activated_payment_ids(db_session)
    context = runtime(db_session)
    other_kind: Literal["mixed", "physical"] = "physical" if first_kind == "mixed" else "mixed"
    document = str(uuid4())
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await cross_type_race_facts(db_session)
    await raw_outcome(db_session, first_kind, *ids[first_kind], document)
    holder_pid = await db_session.scalar(text("SELECT pg_backend_pid()"))
    assert isinstance(holder_pid, int)
    waiting_pid: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def contender() -> None:
        async with context.factory() as session:
            await set_receiving_context(session, context.stock, context.actor_id)
            pid = await session.scalar(text("SELECT pg_backend_pid()"))
            assert isinstance(pid, int) and pid != holder_pid
            waiting_pid.set_result(pid)
            with pytest.raises(DBAPIError, match="Terminal evidence was already used") as denied:
                await raw_outcome(session, other_kind, *ids[other_kind], document)
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
                    pytest.fail("Raw cross-type evidence bypassed the terminal-reference lock")
                await asyncio.sleep(0)
        await db_session.commit()
        await asyncio.wait_for(waiting, timeout=10)
    finally:
        if not waiting.done():
            waiting.cancel()
        await asyncio.gather(waiting, return_exceptions=True)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    counts = {}
    for kind, (operation, _) in ids.items():
        prefix = "pos_mixed_electronic_refund" if kind == "mixed" else "pos_unit_electronic_refund"
        counts[kind] = await db_session.scalar(
            text(f"SELECT count(*) FROM {prefix}_outcome WHERE operation_id=:operation"),
            {"operation": operation},
        )
    assert counts == {
        "mixed": int(first_kind == "mixed"),
        "physical": int(first_kind == "physical"),
    }
    audit_counts = {}
    for kind, (operation, _) in ids.items():
        prefix = "pos_mixed_electronic_refund" if kind == "mixed" else "pos_unit_electronic_refund"
        audit_counts[kind] = await db_session.scalar(
            text(
                "SELECT count(*) FROM audit_log a "
                f"JOIN {prefix}_outcome o ON a.record_id=o.id "
                "AND a.tenant_id=o.tenant_id WHERE a.table_name=:table "
                "AND o.operation_id=:operation"
            ),
            {"table": f"{prefix}_outcome", "operation": operation},
        )
    assert audit_counts == counts
    assert await cross_type_race_facts(db_session) == before


async def test_recorded_outcome_audit_omits_terminal_and_document(
    db_session: AsyncSession,
) -> None:
    request, activation = await activated(db_session)
    payment = next(p for p in activation.review.preview.payments if p.method == "card")
    evidence = confirmation(payment.original_payment_id)
    await outcome(db_session, request, evidence)
    audit = await db_session.scalar(
        text(
            "SELECT new_values FROM audit_log WHERE table_name="
            "'pos_mixed_electronic_refund_outcome' AND record_id=("
            "SELECT id FROM pos_mixed_electronic_refund_outcome "
            "WHERE operation_id=:operation AND original_payment_id=:payment)"
        ),
        {"operation": request.operation_id, "payment": payment.original_payment_id},
    )
    assert isinstance(audit, dict)
    assert not ({"terminal_id", "document_number"} & audit.keys())
    assert audit["original_payment_id"] == str(payment.original_payment_id)
    assert audit["method"] == "card" and audit["currency"] == "TJS"
    assert "amount" in audit
