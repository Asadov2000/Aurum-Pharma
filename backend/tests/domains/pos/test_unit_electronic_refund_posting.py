"""Electronic evidence posts one atomic internal return without bank side effects."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import AurumError, BusinessRuleError, ConflictError, NotFoundError
from app.domains.foundation.repository import FoundationRepository
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.pos.unit_electronic_refund_intent_schemas import SaveElectronicRefundIntentRequest
from app.domains.pos.unit_electronic_refund_posting_repository import (
    UnitElectronicRefundPostingRepository,
)
from app.domains.pos.unit_electronic_refund_posting_schemas import PostedElectronicRefund
from app.domains.pos.unit_electronic_refund_posting_service import (
    UnitElectronicRefundPostingService,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.pos.test_physical_cash_refund import close_shift
from tests.domains.pos.test_unit_electronic_refund_outcome import activated, confirmation, outcome
from tests.domains.pos.test_unit_electronic_refund_preview import facts
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def reconciled(session: AsyncSession) -> tuple[SaveElectronicRefundIntentRequest, UUID]:
    request, activation = await activated(session)
    for payment in activation.review.preview.payments:
        if payment.method in {"card", "qr"} and payment.amount > 0:
            await outcome(session, request, confirmation(payment.original_payment_id))
    assert (await outcome(session, request)).state == "confirmed_evidence_only"
    return request, activation.shift_id


async def post(
    session: AsyncSession,
    request: SaveElectronicRefundIntentRequest,
    shift_id: UUID,
    *,
    actor_id: UUID | None = None,
    tenant_id: UUID | None = None,
    branches: set[UUID] | None = None,
    reason_code: str | None = None,
    comment: str | None = None,
) -> PostedElectronicRefund:
    context = runtime(session)
    return await UnitElectronicRefundPostingService(
        UnitElectronicRefundPostingRepository(session)
    ).post(
        tenant_id=tenant_id or context.stock.tenant_id,
        actor_id=actor_id or context.actor_id,
        allowed_branch_ids=branches if branches is not None else {context.stock.branch_id},
        parent_sale_id=request.request.parent_sale_id,
        operation_id=request.operation_id,
        shift_id=shift_id,
        reason_code=reason_code,
        comment=comment,
    )


async def read(
    session: AsyncSession, request: SaveElectronicRefundIntentRequest
) -> PostedElectronicRefund:
    context = runtime(session)
    return await UnitElectronicRefundPostingService(
        UnitElectronicRefundPostingRepository(session)
    ).read(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        parent_sale_id=request.request.parent_sale_id,
        operation_id=request.operation_id,
    )


async def test_posting_links_exact_mixed_allocation_and_preserves_original_stock(
    db_session: AsyncSession,
) -> None:
    request, shift_id = await reconciled(db_session)
    evidence = await outcome(db_session, request)
    before = await facts(db_session)
    original = await db_session.scalar(
        text("SELECT to_jsonb(sale) FROM sale WHERE id=:id"),
        {"id": request.request.parent_sale_id},
    )
    posted = await post(db_session, request, shift_id)
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    context = runtime(db_session)
    assert posted.operation_id == request.operation_id
    assert posted.parent_sale_id == request.request.parent_sale_id
    assert posted.sale_id != posted.parent_sale_id
    assert posted.tenant_id == context.stock.tenant_id
    assert posted.actor_id == context.actor_id
    assert posted.shift_id == shift_id
    assert posted.receipt.document_kind == "physical_electronic_refund_receipt"
    assert posted.total_amount == evidence.activation.review.preview.total_amount
    assert posted.receipt.lines[0].offset == 0
    assert posted.receipt.lines[0].base_units == 1
    expected = {
        part.original_payment_id: part
        for part in evidence.activation.review.preview.payments
        if part.amount > 0
    }
    confirmations = {part.original_payment_id: part for part in evidence.confirmations}
    assert {part.method for part in posted.receipt.payments} == {"cash", "card", "qr"}
    assert len(posted.receipt.payments) == len(expected)
    assert sum((part.amount for part in posted.receipt.payments), Decimal()) == posted.total_amount
    for part in posted.receipt.payments:
        frozen = expected[part.original_payment_id]
        assert (part.method, part.amount, part.currency) == (
            frozen.method,
            frozen.amount,
            frozen.currency,
        )
        if part.method == "cash":
            assert part.terminal_id is None and part.document_number is None
        else:
            recorded = confirmations[part.original_payment_id]
            assert (part.terminal_id, part.document_number) == (
                recorded.terminal_id,
                recorded.document_number,
            )
        row = (
            await db_session.execute(
                text("SELECT sale_id,payment_method,amount FROM sale_payment WHERE id=:id"),
                {"id": part.id},
            )
        ).one()
        assert (row.sale_id, row.payment_method, row.amount) == (
            posted.sale_id,
            part.method,
            part.amount,
        )
    row = (
        await db_session.execute(
            text("SELECT sale_type,status,parent_sale_id,receipt_snapshot FROM sale WHERE id=:id"),
            {"id": posted.sale_id},
        )
    ).one()
    assert (row.sale_type, row.status, row.parent_sale_id) == (
        "return",
        "completed",
        posted.parent_sale_id,
    )
    assert row.receipt_snapshot == posted.receipt.model_dump(mode="json")
    assert (
        await db_session.scalar(
            text("SELECT to_jsonb(sale) FROM sale WHERE id=:id"), {"id": posted.parent_sale_id}
        )
        == original
    )
    after = await facts(db_session)
    for table, rows in before.items():
        if table.startswith("unit_stock_") or table in {
            "pos_physical_checkout",
            "pos_refund_attempt",
            "pos_payment_attempt",
            "pos_unit_checkout_payment_component",
            "pos_unit_payment_attempt_binding",
        }:
            assert after[table] == rows
    assert len(after["sale"]) == len(before["sale"]) + 1
    assert len(after["sale_payment"]) == len(before["sale_payment"]) + len(expected)
    assert len(after["pos_physical_return_command"]) == 1
    assert len(after["pos_unit_refund_payment_component"]) == len(expected)
    rows = (
        await db_session.execute(
            text(
                "SELECT original_payment_id,electronic_refund_id,physical_refund_id,"
                "mixed_refund_id,sale_id,amount FROM pos_unit_refund_payment_component "
                "WHERE sale_id=:id"
            ),
            {"id": posted.sale_id},
        )
    ).all()
    assert {row.original_payment_id for row in rows} == set(expected)
    for row in rows:
        assert row.electronic_refund_id == request.operation_id
        assert row.physical_refund_id is None and row.mixed_refund_id is None
        assert row.amount == expected[row.original_payment_id].amount


async def test_replay_returns_exact_receipt_without_duplicate_financial_rows(
    db_session: AsyncSession,
) -> None:
    request, shift_id = await reconciled(db_session)
    posted = await post(db_session, request, shift_id)
    before = await facts(db_session)
    assert await post(db_session, request, shift_id) == posted
    assert await read(db_session, request) == posted
    assert await facts(db_session) == before
    with pytest.raises(ConflictError):
        await post(db_session, request, uuid4())
    assert await facts(db_session) == before


@pytest.mark.parametrize("confirmation_count", [0, 1])
async def test_missing_electronic_confirmation_never_posts_partial_financial_return(
    db_session: AsyncSession, confirmation_count: int
) -> None:
    request, activation = await activated(db_session)
    payments = [p for p in activation.review.preview.payments if p.method in {"card", "qr"}]
    for payment in payments[:confirmation_count]:
        await outcome(db_session, request, confirmation(payment.original_payment_id))
    before = await facts(db_session)
    with pytest.raises(AurumError):
        await post(db_session, request, activation.shift_id)
    with pytest.raises(NotFoundError):
        await read(db_session, request)
    assert await facts(db_session) == before


async def test_closed_activation_shift_can_post_in_own_new_shift_on_original_register(
    db_session: AsyncSession,
) -> None:
    request, shift_id = await reconciled(db_session)
    evidence = await outcome(db_session, request)
    await close_shift(db_session, shift_id)
    before = await facts(db_session)
    with pytest.raises(AurumError):
        await post(db_session, request, shift_id)
    assert await facts(db_session) == before
    context = runtime(db_session)
    shift = await POSService(POSRepository(db_session)).open_shift(
        tenant_id=context.stock.tenant_id,
        register_id=evidence.activation.register_id,
        opened_by_user_id=context.actor_id,
        opening_cash=Decimal("100.00"),
        allowed_branch_ids={context.stock.branch_id},
    )
    posted = await post(db_session, request, shift.id)
    assert posted.shift_id == shift.id
    assert posted.register_id == evidence.activation.register_id


async def test_committed_lost_response_recovers_after_shift_close_and_readonly(
    db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    request, shift_id = await reconciled(db_session)
    posted = await post(db_session, request, shift_id)
    await close_shift(db_session, shift_id)
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
    assert await read(db_session, request) == posted
    assert await post(db_session, request, shift_id) == posted
    assert await facts(db_session) == before


async def test_inactive_register_denies_new_posting_without_changing_evidence(
    db_session: AsyncSession,
) -> None:
    request, shift_id = await reconciled(db_session)
    evidence = await outcome(db_session, request)
    await db_session.execute(
        text("UPDATE register SET is_active=false WHERE id=:id"),
        {"id": evidence.activation.register_id},
    )
    before = await facts(db_session)
    with pytest.raises(AurumError):
        await post(db_session, request, shift_id)
    assert await facts(db_session) == before
    assert await outcome(db_session, request) == evidence


async def test_foreign_author_tenant_and_branch_cannot_post_or_discover_receipt(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    request, shift_id = await reconciled(db_session)
    posted = await post(db_session, request, shift_id)
    context = runtime(db_session)
    await db_session.commit()
    other = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, context.stock, other)
    with pytest.raises(NotFoundError):
        await post(db_session, request, shift_id, actor_id=other)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    for overrides in ({"tenant_id": uuid4()}, {"branches": set()}):
        with pytest.raises(AurumError):
            await post(db_session, request, shift_id, **overrides)
    assert await read(db_session, request) == posted


async def test_caller_rollback_removes_posting_receipt_payments_and_quarantine(
    db_session: AsyncSession,
) -> None:
    request, shift_id = await reconciled(db_session)
    before = await facts(db_session)
    async with db_session.begin_nested() as caller:
        await post(db_session, request, shift_id)
        await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        await caller.rollback()
    assert await facts(db_session) == before
    with pytest.raises(NotFoundError):
        await read(db_session, request)
    retry = await post(db_session, request, shift_id)
    assert retry.receipt.lines[0].offset == 0


async def test_late_persistence_failure_rolls_back_whole_posting(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    request, shift_id = await reconciled(db_session)
    before = await facts(db_session)

    async def refuse_completion(repository: UnitElectronicRefundPostingRepository) -> None:
        assert (
            await repository.session.scalar(
                text("SELECT count(*) FROM sale WHERE sale_type='return' AND status='completed'")
            )
            == 1
        )
        assert (
            await repository.session.scalar(
                text("SELECT count(*) FROM pos_physical_return_command")
            )
            == 1
        )
        raise BusinessRuleError("Simulated electronic posting final persistence refusal")

    with monkeypatch.context() as patch:
        patch.setattr(
            UnitElectronicRefundPostingRepository, "validate_completion", refuse_completion
        )
        with pytest.raises(BusinessRuleError, match="final persistence refusal"):
            await post(db_session, request, shift_id)
    assert await facts(db_session) == before
    assert (await outcome(db_session, request)).state == "confirmed_evidence_only"
    assert (await post(db_session, request, shift_id)).receipt.lines[0].offset == 0


@pytest.mark.parametrize("missing", ["reason", "comment"])
async def test_required_reason_policy_rejects_before_any_financial_write(
    db_session: AsyncSession, missing: str
) -> None:
    request, shift_id = await reconciled(db_session)
    settings_repository = FoundationRepository(db_session)
    settings = await settings_repository.get_settings(runtime(db_session).stock.tenant_id)
    assert settings is not None
    await settings_repository.update_settings(settings, refund_reason_mode="required_with_text")
    before = await facts(db_session)
    with pytest.raises(BusinessRuleError):
        await post(
            db_session,
            request,
            shift_id,
            reason_code=None if missing == "reason" else "other",
            comment=None if missing == "comment" else "Wrong quantity",
        )
    assert await facts(db_session) == before
    posted = await post(
        db_session, request, shift_id, reason_code="other", comment="Wrong quantity"
    )
    assert (
        await post(db_session, request, shift_id, reason_code="other", comment="Wrong quantity")
        == posted
    )
    with pytest.raises(ConflictError):
        await post(db_session, request, shift_id, reason_code="other", comment="Changed reason")
    assert await read(db_session, request) == posted


async def test_posted_component_advances_next_review_without_stranding_parent_history(
    db_session: AsyncSession,
) -> None:
    from app.domains.pos.unit_electronic_refund_preview_repository import (
        UnitElectronicRefundPreviewRepository,
    )
    from app.domains.pos.unit_electronic_refund_preview_service import (
        UnitElectronicRefundPreviewService,
    )

    request, shift_id = await reconciled(db_session)
    posted = await post(db_session, request, shift_id)
    context = runtime(db_session)
    before = await facts(db_session)
    quoted = await UnitElectronicRefundPreviewService(
        UnitElectronicRefundPreviewRepository(db_session)
    ).quote(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        parent_sale_id=request.request.parent_sale_id,
        request=request.request.selection,
    )
    assert quoted.lines[0].offset == posted.receipt.lines[0].base_units
    assert quoted.lines[0].base_units == posted.receipt.lines[0].base_units
    assert quoted.total_amount > 0
    assert sum((part.amount for part in quoted.payments), Decimal()) == quoted.total_amount
    assert quoted.revision_hash != request.request.expected_revision_hash
    assert await facts(db_session) == before
