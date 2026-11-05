"""Electronic evidence posts one atomic internal return without bank side effects."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import AurumError, BusinessRuleError, ConflictError, NotFoundError
from app.domains.foundation.repository import FoundationRepository
from app.domains.pos.mixed_electronic_refund_intent_schemas import (
    SaveMixedElectronicRefundIntentRequest,
)
from app.domains.pos.mixed_electronic_refund_posting_repository import (
    MixedElectronicRefundPostingRepository,
)
from app.domains.pos.mixed_electronic_refund_posting_schemas import PostedMixedElectronicRefund
from app.domains.pos.mixed_electronic_refund_posting_service import (
    MixedElectronicRefundPostingService,
)
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.pos.test_mixed_electronic_refund_outcome import activated, confirmation, outcome
from tests.domains.pos.test_mixed_electronic_refund_outcome import facts as outcome_facts
from tests.domains.pos.test_physical_cash_refund import close_shift
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def facts(session: AsyncSession) -> dict[str, list[object]]:
    result = await outcome_facts(session)
    result["pos_mixed_electronic_refund_posting"] = list(
        (
            await session.execute(
                text(
                    "SELECT to_jsonb(row) FROM pos_mixed_electronic_refund_posting row "
                    "WHERE tenant_id=:tenant ORDER BY id"
                ),
                {"tenant": runtime(session).stock.tenant_id},
            )
        ).scalars()
    )
    return result


async def reconciled(session: AsyncSession) -> tuple[SaveMixedElectronicRefundIntentRequest, UUID]:
    request, activation = await activated(session)
    for payment in activation.review.preview.payments:
        if payment.method in {"card", "qr"} and payment.amount > 0:
            await outcome(session, request, confirmation(payment.original_payment_id))
    assert (await outcome(session, request)).state == "confirmed_evidence_only"
    return request, activation.shift_id


async def post(
    session: AsyncSession,
    request: SaveMixedElectronicRefundIntentRequest,
    shift_id: UUID,
    *,
    actor_id: UUID | None = None,
    tenant_id: UUID | None = None,
    branches: set[UUID] | None = None,
    reason_code: str | None = None,
    comment: str | None = None,
) -> PostedMixedElectronicRefund:
    context = runtime(session)
    return await MixedElectronicRefundPostingService(
        MixedElectronicRefundPostingRepository(session)
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
    session: AsyncSession, request: SaveMixedElectronicRefundIntentRequest
) -> PostedMixedElectronicRefund:
    context = runtime(session)
    return await MixedElectronicRefundPostingService(
        MixedElectronicRefundPostingRepository(session)
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
    assert posted.receipt.document_kind == "mixed_electronic_refund_receipt"
    assert posted.total_amount == evidence.activation.review.preview.total_amount
    assert posted.receipt.lines[0].offset == 0
    assert {line.inventory_kind for line in posted.receipt.lines} == {
        "legacy_pack",
        "physical_unit",
    }
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
    assert len(after["pos_mixed_return_command"]) == 1
    assert len(after["pos_unit_refund_payment_component"]) == len(expected)
    rows = (
        await db_session.execute(
            text(
                "SELECT original_payment_id,electronic_refund_id,mixed_electronic_refund_id,"
                "physical_refund_id,"
                "mixed_refund_id,sale_id,amount FROM pos_unit_refund_payment_component "
                "WHERE sale_id=:id"
            ),
            {"id": posted.sale_id},
        )
    ).all()
    assert {row.original_payment_id for row in rows} == set(expected)
    for row in rows:
        assert row.mixed_electronic_refund_id == request.operation_id
        assert row.electronic_refund_id is None
        assert row.physical_refund_id is None and row.mixed_refund_id is None
        assert row.amount == expected[row.original_payment_id].amount


async def test_posting_payment_audit_omits_evidence_and_preserves_financial_fields(
    db_session: AsyncSession,
) -> None:
    request, shift_id = await reconciled(db_session)
    posted = await post(db_session, request, shift_id)
    parts = {part.id: part for part in posted.receipt.payments}
    rows = (
        await db_session.execute(
            text(
                "SELECT p.id,p.metadata,a.new_values FROM sale_payment p "
                "JOIN audit_log a ON a.tenant_id=p.tenant_id AND a.record_id=p.id "
                "WHERE p.tenant_id=:tenant AND p.sale_id=:sale "
                "AND a.table_name='sale_payment' AND a.action='INSERT'"
            ),
            {"tenant": posted.tenant_id, "sale": posted.sale_id},
        )
    ).all()
    assert len(rows) == len(parts)
    assert {row.id for row in rows} == set(parts)
    assert {part.method for part in parts.values()} == {"cash", "card", "qr"}
    for row in rows:
        part = parts[row.id]
        assert isinstance(row.new_values, dict)
        assert row.new_values["id"] == str(part.id)
        assert row.new_values["sale_id"] == str(posted.sale_id)
        assert row.new_values["payment_method"] == part.method
        assert Decimal(str(row.new_values["amount"])) == part.amount
        assert row.new_values["currency"] == part.currency
        if part.method == "cash":
            assert row.metadata is None
            assert row.new_values["metadata"] is None
        else:
            assert row.metadata == {
                "terminal_id": part.terminal_id,
                "document_number": part.document_number,
            }
            assert row.new_values["metadata"] == {}


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

    async def refuse_completion(repository: MixedElectronicRefundPostingRepository) -> None:
        assert (
            await repository.session.scalar(
                text("SELECT count(*) FROM sale WHERE sale_type='return' AND status='completed'")
            )
            == 1
        )
        assert (
            await repository.session.scalar(text("SELECT count(*) FROM pos_mixed_return_command"))
            == 1
        )
        raise BusinessRuleError("Simulated electronic posting final persistence refusal")

    with monkeypatch.context() as patch:
        patch.setattr(
            MixedElectronicRefundPostingRepository, "validate_completion", refuse_completion
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


async def test_completed_posting_releases_exclusive_parent_binding(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
) -> None:
    request, shift_id = await reconciled(db_session)
    context = runtime(db_session)
    parameters = {"tenant": context.stock.tenant_id, "parent": request.request.parent_sale_id}
    query = text("SELECT mixed_electronic_refund_binding_active(:tenant,:parent)")
    await db_session.commit()
    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            assert str(await connection.scalar(text("SELECT current_database()"))).endswith("_test")
            assert await connection.scalar(text("SELECT session_user")) == "aurum_migrator"
            assert await connection.scalar(text("SHOW transaction_isolation")) == "read committed"
            assert await connection.scalar(query, parameters) is True
            await set_receiving_context(db_session, context.stock, context.actor_id)
            posted = await post(db_session, request, shift_id)
            await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            await db_session.commit()
            assert await connection.scalar(query, parameters) is False
        finally:
            await transaction.rollback()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await read(db_session, request) == posted


async def test_partial_posting_advances_fresh_payment_history_and_allows_next_review(
    db_session: AsyncSession,
) -> None:
    from app.domains.pos.mixed_cash_refund_api_schemas import MixedRefundQuoteRequest
    from app.domains.pos.mixed_documents import LegacyResultLineV4
    from app.domains.pos.mixed_electronic_refund_intent_repository import (
        MixedElectronicRefundIntentRepository,
    )
    from app.domains.pos.mixed_electronic_refund_intent_service import (
        MixedElectronicRefundIntentService,
    )
    from app.domains.pos.mixed_electronic_refund_preview_repository import (
        MixedElectronicRefundPreviewRepository,
    )
    from app.domains.pos.mixed_electronic_refund_preview_service import (
        MixedElectronicRefundPreviewService,
    )
    from tests.domains.pos.test_mixed_electronic_refund_activation import activate
    from tests.domains.pos.test_mixed_electronic_refund_binding import binding
    from tests.domains.pos.test_mixed_electronic_refund_preview import original

    source = await original(db_session, ("cash", "card", "qr"))
    physical = next(line for line in source.lines if not isinstance(line, LegacyResultLineV4))
    assert physical.base_units >= 2
    selected = MixedRefundQuoteRequest.model_validate(
        {
            "items": [
                {
                    "inventory_kind": "physical_unit",
                    "line_id": physical.id,
                    "sale_format": "unit",
                    "quantity": 1,
                }
            ]
        }
    )
    context = runtime(db_session)
    arguments = {
        "tenant_id": context.stock.tenant_id,
        "actor_id": context.actor_id,
        "allowed_branch_ids": {context.stock.branch_id},
        "parent_sale_id": source.sale_id,
    }
    previews = MixedElectronicRefundPreviewService(
        MixedElectronicRefundPreviewRepository(db_session)
    )
    reviews = MixedElectronicRefundIntentService(MixedElectronicRefundIntentRepository(db_session))
    quoted = await previews.quote(**arguments, request=selected)
    request = SaveMixedElectronicRefundIntentRequest.model_validate(
        {
            "operation_id": uuid4(),
            "request": {
                "parent_sale_id": source.sale_id,
                "selection": selected,
                "expected_total_amount": quoted.total_amount,
                "expected_revision_hash": quoted.revision_hash,
            },
        }
    )
    await reviews.save(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        request=request,
    )
    await binding(db_session, request, "bind")
    activated = await activate(db_session, request)
    for part in activated.review.preview.payments:
        if part.method in {"card", "qr"} and part.amount > 0:
            await outcome(db_session, request, confirmation(part.original_payment_id))
    posted = await post(db_session, request, activated.shift_id)
    before = await facts(db_session)
    following = await previews.quote(**arguments, request=selected)
    assert following.lines[0].offset == 1
    assert following.revision_hash != quoted.revision_hash
    assert following.total_amount > 0
    assert sum((part.amount for part in following.payments), Decimal()) == following.total_amount
    original_amounts = {part.id: part.amount for part in source.payments}
    returned = {part.original_payment_id: part.amount for part in posted.receipt.payments}
    assert all(
        returned.get(part.original_payment_id, Decimal()) + part.amount
        <= original_amounts[part.original_payment_id]
        for part in following.payments
    )
    assert await facts(db_session) == before
    next_request = SaveMixedElectronicRefundIntentRequest.model_validate(
        {
            "operation_id": uuid4(),
            "request": {
                "parent_sale_id": source.sale_id,
                "selection": selected,
                "expected_total_amount": following.total_amount,
                "expected_revision_hash": following.revision_hash,
            },
        }
    )
    saved = await reviews.save(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        request=next_request,
    )
    assert saved.preview == following
    assert (await binding(db_session, next_request, "bind")).state == "bound"
