"""Mixed cash payouts bind one immutable return receipt to both quarantine kinds."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.inventory.unit_stock_v2 import SaleFormatV2
from app.domains.pos.mixed_cash_refund_repository import MixedCashRefundRepository
from app.domains.pos.mixed_cash_refund_review import mixed_refund_revision
from app.domains.pos.mixed_cash_refund_schemas import MixedCashRefundResultV4
from app.domains.pos.mixed_cash_refund_service import MixedCashRefundService
from app.domains.pos.mixed_return_allocation import LegacyReturnSelection, MixedReturnSelection
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from tests.domains.incoming.test_unit_acceptance import (
    ReceivingRuntime,
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.pos.test_mixed_return_journal import (
    TABLES,
    MixedSource,
    journal_facts,
    mixed_source,
    receive,
    sale_stock_facts,
)
from tests.domains.pos.test_physical_cash_refund import close_shift
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture
REFUND_TABLES = (*TABLES, "pos_mixed_cash_refund")


async def refund(
    session: AsyncSession,
    source: MixedSource,
    selections: tuple[MixedReturnSelection, ...] | None = None,
    *,
    operation_id: UUID | None = None,
    shift_id: UUID | None = None,
    actor_id: UUID | None = None,
    tenant_id: UUID | None = None,
    parent_sale_id: UUID | None = None,
    reason_code: str | None = None,
    comment: str | None = None,
    expected_total_amount: Decimal | None = None,
    expected_revision_hash: str | None = None,
) -> MixedCashRefundResultV4:
    context = runtime(session)
    return await MixedCashRefundService(MixedCashRefundRepository(session)).refund(
        tenant_id=tenant_id or context.stock.tenant_id,
        actor_id=actor_id or context.actor_id,
        operation_id=operation_id or uuid4(),
        parent_sale_id=parent_sale_id or source.original.sale_id,
        shift_id=shift_id or source.original.shift_id,
        selections=source.selections() if selections is None else selections,
        reason_code=reason_code,
        comment=comment,
        expected_total_amount=expected_total_amount,
        expected_revision_hash=expected_revision_hash,
    )


async def facts(session: AsyncSession) -> dict[str, list[object]]:
    result = await sale_stock_facts(session)
    result.update(await journal_facts(session))
    result["pos_mixed_cash_refund"] = list(
        (
            await session.execute(
                text(
                    "SELECT to_jsonb(row) FROM pos_mixed_cash_refund row WHERE tenant_id=:tenant "
                    "ORDER BY to_jsonb(row)::text"
                ),
                {"tenant": runtime(session).stock.tenant_id},
            )
        ).scalars()
    )
    return result


async def policy(session: AsyncSession, tenant_id: UUID, mode: str) -> None:
    repository = FoundationRepository(session)
    settings = await repository.get_settings(tenant_id)
    assert settings is not None
    await repository.update_settings(settings, refund_reason_mode=mode)


@pytest.mark.parametrize(
    "sale_format,quantity,units", [("unit", 2, 2), ("blister", 1, 10), ("pack", 1, 30)]
)
async def test_one_cash_payment_and_linked_receipt_cover_both_quarantine_kinds(
    db_session: AsyncSession, sale_format: SaleFormatV2, quantity: int, units: int
) -> None:
    source = await mixed_source(db_session)
    before = await facts(db_session)
    original_row = await db_session.scalar(
        text("SELECT to_jsonb(sale) FROM sale WHERE id=:id"), {"id": source.original.sale_id}
    )
    result = await refund(
        db_session,
        source,
        (
            PhysicalReturnSelection(source.physical_id, sale_format, quantity),
            LegacyReturnSelection(source.legacy_id, Decimal("0.001")),
        ),
    )
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert result.schema_version == result.receipt.schema_version == 4
    assert result.document_kind == "mixed_cash_refund"
    assert result.receipt.document_kind == "mixed_cash_refund_receipt"
    assert result.parent_sale_id == result.receipt.parent_sale_id == source.original.sale_id
    assert result.sale_id != result.parent_sale_id
    assert result.operation_id == result.payment_id
    assert result.tenant_id == source.original.tenant_id
    assert result.branch_id == source.original.branch_id
    assert result.register_id == source.original.register_id
    assert result.shift_id == source.original.shift_id
    assert result.actor_id == runtime(db_session).actor_id
    assert result.receipt.parent_receipt_number == source.original.receipt_number
    assert result.receipt.payment_method == "cash"
    assert result.receipt.currency == "TJS"
    assert result.receipt.is_test is False
    assert [
        (row.position, row.original_position, row.inventory_kind) for row in result.receipt.lines
    ] == [
        (1, 1, "legacy_pack"),
        (2, 2, "physical_unit"),
    ]
    legacy, physical = result.receipt.lines
    assert legacy.inventory_kind == "legacy_pack"
    assert legacy.original_line_id == source.legacy_id
    assert legacy.quantity == Decimal("0.001") and legacy.offset == Decimal("0.000")
    assert physical.inventory_kind == "physical_unit"
    assert physical.original_line_id == source.physical_id
    assert physical.sale_format == sale_format
    assert physical.quantity == quantity and physical.base_units == units
    for returned, original in zip(result.receipt.lines, source.original.lines, strict=True):
        assert returned.name == original.name
        assert returned.original_retail_unit_price == original.original_retail_unit_price
    row = (
        await db_session.execute(
            text(
                "SELECT parent_sale_id,sale_type,status,total_amount,receipt_snapshot "
                "FROM sale WHERE id=:id"
            ),
            {"id": result.sale_id},
        )
    ).one()
    assert (row.parent_sale_id, row.sale_type, row.status) == (
        source.original.sale_id,
        "return",
        "completed",
    )
    assert row.total_amount == result.total_amount
    assert row.receipt_snapshot == result.receipt.model_dump(mode="json")
    payment = (
        await db_session.execute(
            text("SELECT sale_id,payment_method,amount FROM sale_payment WHERE id=:id"),
            {"id": result.payment_id},
        )
    ).one()
    assert (payment.sale_id, payment.payment_method, payment.amount) == (
        result.sale_id,
        "cash",
        result.total_amount,
    )
    assert (
        await db_session.scalar(
            text("SELECT to_jsonb(sale) FROM sale WHERE id=:id"), {"id": source.original.sale_id}
        )
        == original_row
    )
    after = await facts(db_session)
    for table, rows in before.items():
        if table.startswith("unit_stock_") or table in (
            "batch",
            "batch_movement",
            "sale_item",
            "pos_mixed_checkout",
        ):
            assert after[table] == rows
    assert len(after["sale"]) == len(before["sale"]) + 1
    assert len(after["sale_payment"]) == len(before["sale_payment"]) + 1
    assert len(after["pos_mixed_cash_refund"]) == 1
    assert len(after["pos_mixed_return_command"]) == 1
    assert len(after["pos_mixed_return_line"]) == 2
    assert MixedCashRefundResultV4.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("kind", ["legacy_pack", "physical_unit"])
async def test_subset_receipt_has_dense_positions_and_retains_original_position(
    db_session: AsyncSession, kind: str
) -> None:
    source = await mixed_source(db_session)
    index = 0 if kind == "legacy_pack" else 1
    result = await refund(db_session, source, (source.selections()[index],))
    assert len(result.receipt.lines) == 1
    assert result.receipt.lines[0].inventory_kind == kind
    assert result.receipt.lines[0].position == 1
    assert result.receipt.lines[0].original_position == index + 1
    if kind == "legacy_pack":
        assert result.physical_cost_amount == Decimal("0.00")


async def test_partial_both_kind_refunds_conserve_money_discount_and_physical_cost(
    db_session: AsyncSession,
) -> None:
    source = await mixed_source(db_session, fractional_legacy=True)
    parts = [
        await refund(db_session, source, source.selections("0.001", quantity))
        for quantity in (1, 29, 30)
    ]
    assert [part.receipt.lines[0].offset for part in parts] == [
        Decimal("0.000"),
        Decimal("0.001"),
        Decimal("0.002"),
    ]
    assert [part.receipt.lines[1].offset for part in parts] == [0, 1, 30]
    assert parts[0].receipt.lines[0].net_amount == Decimal("0.00")
    assert parts[2].receipt.lines[0].net_amount == Decimal("0.01")
    assert sum((part.total_amount for part in parts), Decimal()) == source.original.total_amount
    assert sum((part.physical_cost_amount for part in parts), Decimal()) == Decimal("20.00")
    for index in (0, 1):
        for field in ("net_amount", "gross_amount", "discount_amount"):
            assert sum(
                (getattr(part.receipt.lines[index], field) for part in parts), Decimal()
            ) == getattr(source.original.lines[index], field)
    before = await facts(db_session)
    with pytest.raises(BusinessRuleError):
        await refund(db_session, source)
    assert await facts(db_session) == before


async def test_zero_total_never_claims_quarantine_or_creates_cash_effects(
    db_session: AsyncSession,
) -> None:
    source = await mixed_source(db_session, fractional_legacy=True)
    before = await facts(db_session)
    with pytest.raises(BusinessRuleError):
        await refund(
            db_session, source, (LegacyReturnSelection(source.legacy_id, Decimal("0.001")),)
        )
    assert await facts(db_session) == before
    result = await refund(db_session, source)
    assert [line.offset for line in result.receipt.lines] == [Decimal("0.000"), 0]


async def test_replay_is_canonical_and_keeps_review_after_both_cursors_advance(
    db_session: AsyncSession,
) -> None:
    source = await mixed_source(db_session)
    selections = source.selections()
    revision = mixed_refund_revision(source.original, {}, {}, selections)
    result = await refund(db_session, source, expected_revision_hash=revision)
    equivalent = (
        PhysicalReturnSelection(source.physical_id, "unit", 1),
        LegacyReturnSelection(source.legacy_id, Decimal("0.001000")),
    )
    assert mixed_refund_revision(source.original, {}, {}, equivalent) == revision
    before = await facts(db_session)
    assert (
        await refund(
            db_session,
            source,
            equivalent,
            operation_id=result.operation_id,
            expected_total_amount=result.total_amount,
            expected_revision_hash=revision,
        )
        == result
    )
    assert await facts(db_session) == before


async def test_saved_replay_survives_closed_shift_inactive_catalog_register_and_new_policy(
    db_session: AsyncSession,
) -> None:
    source = await mixed_source(db_session)
    result = await refund(db_session, source)
    await close_shift(db_session, source.original.shift_id)
    await db_session.execute(
        text("UPDATE register SET is_active=false WHERE id=:id"),
        {"id": source.original.register_id},
    )
    await db_session.execute(
        text("UPDATE tenant_catalog SET is_active=false,base_price=999.00 WHERE id=:id"),
        {"id": runtime(db_session).stock.catalog_id},
    )
    await policy(db_session, source.original.tenant_id, "required_with_text")
    before = await facts(db_session)
    assert await refund(db_session, source, operation_id=result.operation_id) == result
    assert await facts(db_session) == before


@pytest.mark.parametrize("change", ["quantity", "parent", "shift", "review"])
async def test_reused_operation_with_changed_identity_never_pays_again(
    db_session: AsyncSession, change: str
) -> None:
    source = await mixed_source(db_session)
    result = await refund(db_session, source)
    selections = (
        source.selections(physical_quantity=2) if change == "quantity" else source.selections()
    )
    before = await facts(db_session)
    with pytest.raises(ConflictError):
        await refund(
            db_session,
            source,
            selections,
            operation_id=result.operation_id,
            parent_sale_id=uuid4() if change == "parent" else None,
            shift_id=uuid4() if change == "shift" else None,
            expected_revision_hash="0" * 64 if change == "review" else None,
        )
    assert await facts(db_session) == before


@pytest.mark.parametrize("gate", ["closed_shift", "inactive_register", "wrong_register"])
async def test_current_shift_and_register_are_required_before_new_payout(
    db_session: AsyncSession, gate: str
) -> None:
    source = await mixed_source(db_session)
    shift_id = source.original.shift_id
    if gate == "closed_shift":
        await close_shift(db_session, shift_id)
    elif gate == "inactive_register":
        await db_session.execute(
            text("UPDATE register SET is_active=false WHERE id=:id"),
            {"id": source.original.register_id},
        )
    else:
        other = await checkout_source(db_session)
        shift_id = other.sale.shift_id
    before = await facts(db_session)
    with pytest.raises((BusinessRuleError, PermissionDeniedError, NotFoundError)):
        await refund(db_session, source, shift_id=shift_id)
    assert await facts(db_session) == before


async def test_new_payout_uses_historical_names_prices_and_original_register_on_a_new_shift(
    db_session: AsyncSession,
) -> None:
    source = await mixed_source(db_session)
    await close_shift(db_session, source.original.shift_id)
    context = runtime(db_session)
    shift = await POSService(POSRepository(db_session)).open_shift(
        tenant_id=context.stock.tenant_id,
        register_id=source.original.register_id,
        opened_by_user_id=context.actor_id,
        opening_cash=Decimal("100.00"),
        allowed_branch_ids={context.stock.branch_id},
    )
    await db_session.execute(
        text("UPDATE tenant_catalog SET brand_name='Changed name',base_price=999.00 WHERE id=:id"),
        {"id": context.stock.catalog_id},
    )
    await db_session.execute(
        text("UPDATE batch SET is_blocked=true WHERE id=:id"), {"id": source.batch_id}
    )
    result = await refund(db_session, source, shift_id=shift.id)
    assert result.shift_id == shift.id
    for returned, original in zip(result.receipt.lines, source.original.lines, strict=True):
        assert returned.name == original.name
        assert returned.original_retail_unit_price == original.original_retail_unit_price


async def test_journal_only_uuid_cannot_be_promoted_to_cash_payout(
    db_session: AsyncSession,
) -> None:
    source = await mixed_source(db_session)
    received = await receive(db_session, source)
    before = await facts(db_session)
    with pytest.raises(ConflictError):
        await refund(db_session, source, operation_id=received.operation_id)
    assert await facts(db_session) == before


async def test_prior_quarantine_quantities_are_not_paid_by_a_later_refund(
    db_session: AsyncSession,
) -> None:
    source = await mixed_source(db_session, fractional_legacy=True)
    await receive(db_session, source)
    result = await refund(db_session, source)
    assert [line.offset for line in result.receipt.lines] == [Decimal("0.001"), 1]


@pytest.mark.parametrize("kind", ["legacy_pack", "physical_unit"])
async def test_overreturn_of_either_kind_rolls_back_the_entire_payout(
    db_session: AsyncSession, kind: str
) -> None:
    source = await mixed_source(db_session)
    selections = source.selections(
        "0.002" if kind == "legacy_pack" else "0.001", 61 if kind == "physical_unit" else 1
    )
    before = await facts(db_session)
    with pytest.raises(BusinessRuleError):
        await refund(db_session, source, selections)
    assert await facts(db_session) == before


async def test_outer_rollback_removes_payment_receipt_marker_and_both_quarantine_claims(
    db_session: AsyncSession,
) -> None:
    source = await mixed_source(db_session)
    operation_id = uuid4()
    before = await facts(db_session)
    async with db_session.begin_nested() as caller:
        await refund(db_session, source, operation_id=operation_id)
        await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        await caller.rollback()
    assert await facts(db_session) == before
    result = await refund(db_session, source, operation_id=operation_id)
    assert [line.offset for line in result.receipt.lines] == [Decimal("0.000"), 0]


async def test_late_failure_rolls_back_receipt_sequence_payment_and_both_quarantine_lines(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = await mixed_source(db_session)

    async def refuse_completion(repository: MixedCashRefundRepository) -> None:
        assert (
            await repository.session.scalar(
                text("SELECT count(*) FROM sale WHERE sale_type='return' AND status='completed'")
            )
            == 1
        )
        assert await repository.session.scalar(text("SELECT count(*) FROM sale_payment")) == 2
        assert (
            await repository.session.scalar(text("SELECT count(*) FROM pos_mixed_cash_refund"))
            == 1
        )
        assert (
            await repository.session.scalar(text("SELECT count(*) FROM pos_mixed_return_line"))
            == 2
        )
        raise BusinessRuleError("Injected final mixed refund persistence failure")

    operation_id = uuid4()
    before = await facts(db_session)
    with monkeypatch.context() as patch:
        patch.setattr(MixedCashRefundRepository, "validate_completion", refuse_completion)
        with pytest.raises(BusinessRuleError, match="Injected final"):
            await refund(db_session, source, operation_id=operation_id)
    assert await facts(db_session) == before
    result = await refund(db_session, source, operation_id=operation_id)
    assert [line.offset for line in result.receipt.lines] == [Decimal("0.000"), 0]


@pytest.mark.parametrize(
    "mode,reason,comment",
    [
        ("off", "other", None),
        ("required", None, None),
        ("required_with_text", "other", " \n"),
        ("optional", "unknown", None),
        ("optional", "other", "x" * 501),
    ],
)
async def test_reason_policy_failure_precedes_every_financial_and_quarantine_effect(
    db_session: AsyncSession, mode: str, reason: str | None, comment: str | None
) -> None:
    source = await mixed_source(db_session)
    await policy(db_session, source.original.tenant_id, mode)
    before = await facts(db_session)
    with pytest.raises(BusinessRuleError):
        await refund(db_session, source, reason_code=reason, comment=comment)
    assert await facts(db_session) == before


async def test_normalized_reason_replay_ignores_later_policy_but_rejects_changed_comment(
    db_session: AsyncSession,
) -> None:
    source = await mixed_source(db_session)
    await policy(db_session, source.original.tenant_id, "required_with_text")
    result = await refund(db_session, source, reason_code=" other ", comment="\u2003Тест причины\n")
    await policy(db_session, source.original.tenant_id, "off")
    before = await facts(db_session)
    assert (
        await refund(
            db_session,
            source,
            operation_id=result.operation_id,
            reason_code="other",
            comment="Тест причины",
        )
        == result
    )
    with pytest.raises(ConflictError):
        await refund(
            db_session,
            source,
            operation_id=result.operation_id,
            reason_code="other",
            comment="Другой текст",
        )
    assert await facts(db_session) == before


@pytest.mark.parametrize("conflict", ["amount", "revision"])
async def test_review_conflict_leaves_no_rows_and_the_same_operation_can_be_retried(
    db_session: AsyncSession, conflict: str
) -> None:
    source = await mixed_source(db_session, fractional_legacy=True)
    operation_id = uuid4()
    revision = mixed_refund_revision(source.original, {}, {}, source.selections())
    before = await facts(db_session)
    with pytest.raises(ConflictError):
        await refund(
            db_session,
            source,
            operation_id=operation_id,
            expected_total_amount=Decimal("999.00") if conflict == "amount" else None,
            expected_revision_hash="0" * 64 if conflict == "revision" else revision,
        )
    assert await facts(db_session) == before
    result = await refund(
        db_session, source, operation_id=operation_id, expected_revision_hash=revision
    )
    before = await facts(db_session)
    with pytest.raises(ConflictError):
        await refund(
            db_session,
            source,
            operation_id=operation_id,
            expected_total_amount=result.total_amount + Decimal("0.01"),
            expected_revision_hash=revision,
        )
    assert await facts(db_session) == before


async def test_stale_review_after_a_quarantine_return_cannot_claim_more_goods(
    db_session: AsyncSession,
) -> None:
    source = await mixed_source(db_session, fractional_legacy=True)
    revision = mixed_refund_revision(source.original, {}, {}, source.selections())
    await receive(db_session, source)
    before = await facts(db_session)
    with pytest.raises(ConflictError):
        await refund(db_session, source, expected_revision_hash=revision)
    assert await facts(db_session) == before


@pytest.mark.parametrize("permissions", [("pos.sell",), ("pos.refund",)])
async def test_missing_refund_permission_or_foreign_shift_owner_cannot_payout(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    permissions: tuple[str, ...],
) -> None:
    source = await mixed_source(db_session)
    context = runtime(db_session)
    before = await facts(db_session)
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=permissions
    )
    await set_receiving_context(db_session, context.stock, actor)
    with pytest.raises((PermissionDeniedError, NotFoundError, BusinessRuleError)):
        await refund(db_session, source, actor_id=actor)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await facts(db_session) == before


async def test_refund_only_actor_can_payout_on_own_new_shift_without_sell_permission(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    source = await mixed_source(db_session)
    await close_shift(db_session, source.original.shift_id)
    context = runtime(db_session)
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, context.stock, actor)
    shift = await POSService(POSRepository(db_session)).open_shift(
        tenant_id=context.stock.tenant_id,
        register_id=source.original.register_id,
        opened_by_user_id=actor,
        opening_cash=Decimal("100.00"),
        allowed_branch_ids={context.stock.branch_id},
    )
    result = await refund(db_session, source, actor_id=actor, shift_id=shift.id)
    assert result.actor_id == actor and result.shift_id == shift.id
    assert (
        await refund(
            db_session, source, actor_id=actor, shift_id=shift.id, operation_id=result.operation_id
        )
        == result
    )


async def test_wrong_actual_actor_and_revoked_assignment_block_replay_and_new_payout(
    db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    source = await mixed_source(db_session)
    result = await refund(db_session, source)
    before = await facts(db_session)
    for operation_id in (result.operation_id, uuid4()):
        with pytest.raises((PermissionDeniedError, NotFoundError)):
            await refund(db_session, source, actor_id=uuid4(), operation_id=operation_id)
    assert await facts(db_session) == before
    context = runtime(db_session)
    await db_session.commit()
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text(
                "UPDATE user_assignment SET is_active=false "
                "WHERE user_id=:actor AND tenant_id=:tenant"
            ),
            {"actor": context.actor_id, "tenant": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    for operation_id in (result.operation_id, uuid4()):
        with pytest.raises((PermissionDeniedError, NotFoundError)):
            await refund(db_session, source, operation_id=operation_id)


@pytest.mark.parametrize("scope", ["branch", "tenant"])
async def test_real_foreign_scope_cannot_read_or_replay_financial_marker(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine, scope: str
) -> None:
    source = await mixed_source(db_session)
    result = await refund(db_session, source)
    context = runtime(db_session)
    await db_session.commit()
    async with AsyncSession(db_engine) as support, support.begin():
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        if scope == "tenant":
            foreign = await seed_ledger_context(support)
        else:
            branch = await FoundationService(FoundationRepository(support)).create_branch(
                tenant_id=context.stock.tenant_id, fields={"name": "Other refund branch"}
            )
            foreign = replace(context.stock, branch_id=branch.id)
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, foreign, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, foreign, actor)
    for table in REFUND_TABLES:
        assert await db_session.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    with pytest.raises((PermissionDeniedError, NotFoundError)):
        await refund(
            db_session,
            source,
            actor_id=actor,
            tenant_id=foreign.tenant_id,
            operation_id=result.operation_id,
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await refund(db_session, source, operation_id=result.operation_id) == result


async def test_support_session_cannot_impersonate_cash_refund_actor(
    db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    source = await mixed_source(db_session)
    context = runtime(db_session)
    await db_session.commit()
    async with AsyncSession(db_engine) as support, support.begin():
        support.info["receiving_runtime"] = context
        await support.execute(
            text(
                "SELECT set_config('app.support_session','true',true),"
                "set_config('app.tenant_id',:tenant,true),set_config('app.user_id',:actor,true)"
            ),
            {"tenant": str(context.stock.tenant_id), "actor": str(context.actor_id)},
        )
        with pytest.raises(PermissionDeniedError):
            await refund(support, source)


async def concurrent_refund(
    factory: async_sessionmaker[AsyncSession],
    context: ReceivingRuntime,
    source: MixedSource,
    operation_id: UUID,
) -> MixedCashRefundResultV4 | BusinessRuleError:
    try:
        async with factory.begin() as session:
            session.info["receiving_runtime"] = context
            await set_receiving_context(session, context.stock, context.actor_id)
            return await refund(session, source, operation_id=operation_id)
    except BusinessRuleError as error:
        return error


@pytest.mark.parametrize("same_operation", [False, True])
async def test_concurrent_last_both_kind_returns_create_one_cash_payment(
    db_session: AsyncSession, same_operation: bool
) -> None:
    source = await mixed_source(db_session, sale_format="unit", physical_quantity=1)
    context = runtime(db_session)
    await db_session.commit()
    operation_id = uuid4()
    results = await asyncio.wait_for(
        asyncio.gather(
            *(
                concurrent_refund(
                    context.factory,
                    context,
                    source,
                    operation_id if same_operation else uuid4(),
                )
                for _ in range(3)
            )
        ),
        timeout=15,
    )
    winners = [result for result in results if isinstance(result, MixedCashRefundResultV4)]
    assert len(winners) == (3 if same_operation else 1)
    assert all(result == winners[0] for result in winners)
    assert sum(isinstance(result, BusinessRuleError) for result in results) == (
        0 if same_operation else 2
    )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    final = await facts(db_session)
    assert len(final["sale"]) == 2
    assert len(final["sale_payment"]) == 2
    assert len(final["pos_mixed_cash_refund"]) == 1
    assert len(final["pos_mixed_return_line"]) == 2
