"""Persist a training document without financial or inventory effects."""

from decimal import Decimal, localcontext
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, ConflictError, PermissionDeniedError
from app.domains.inventory.unit_stock_v2 import SaleFormatV2
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.pos.training_checkout_repository import TrainingCheckoutRepository
from app.domains.pos.training_checkout_schemas import (
    TrainingCheckoutOperationRead,
    TrainingCheckoutRequest,
    TrainingCheckoutResultV1,
)
from app.domains.pos.training_checkout_service import TrainingCheckoutService
from app.domains.pos.unit_contracts import EXACT_CONTEXT
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.pos.test_mixed_physical_checkout_preparation import facts as mixed_facts
from tests.domains.pos.test_mixed_physical_checkout_preparation import legacy_with_stock
from tests.domains.pos.test_payment_configuration import _set_payment_settings
from tests.domains.pos.test_unit_cart_quote import quote
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import (
    PhysicalCheckoutSource,
    checkout_source,
)
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def request_for(
    session: AsyncSession, sale_id: UUID, *, mode: str = "cash"
) -> TrainingCheckoutRequest:
    current = await quote(session, sale_id)
    with localcontext(EXACT_CONTEXT):
        parts = (
            [
                {"method": "cash", "amount": "0.10"},
                {"method": "card", "amount": "0.10"},
                {"method": "qr", "amount": current.total_amount - Decimal("0.20")},
            ]
            if mode == "split"
            else [{"method": mode, "amount": current.total_amount}]
        )
        cash = Decimal("0.10") if mode == "split" else current.total_amount
        tendered = cash + Decimal("1.00") if mode in {"cash", "split"} else Decimal("0.00")
    return TrainingCheckoutRequest.model_validate(
        {
            "operation_id": uuid4(),
            "sale_id": sale_id,
            "expected_revision_hash": current.revision_hash,
            "expected_total_amount": current.total_amount,
            "simulated_payments": parts,
            "cash_tendered": tendered,
        }
    )


async def training_source(
    session: AsyncSession,
    *,
    mixed: bool = False,
    sale_format: SaleFormatV2 = "unit",
    mode: str = "cash",
) -> tuple[PhysicalCheckoutSource, TrainingCheckoutRequest]:
    source = await checkout_source(session)
    if mixed:
        await legacy_with_stock(session, source.sale)
    await put_physical(session, source.sale.id, sale_format=sale_format)
    await session.execute(text("UPDATE sale SET is_test=true WHERE id=:id"), {"id": source.sale.id})
    await session.refresh(source.sale)
    await _set_payment_settings(
        session, tenant_id=source.sale.tenant_id, methods=["cash", "card", "qr"], mixed=True
    )
    return source, await request_for(session, source.sale.id, mode=mode)


async def complete(
    session: AsyncSession, request: TrainingCheckoutRequest
) -> TrainingCheckoutResultV1:
    context = runtime(session)
    return await TrainingCheckoutService(TrainingCheckoutRepository(session)).complete(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        request=request,
    )


async def read(
    session: AsyncSession, request: TrainingCheckoutRequest
) -> TrainingCheckoutOperationRead:
    context = runtime(session)
    return await TrainingCheckoutService(TrainingCheckoutRepository(session)).read(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        sale_id=request.sale_id,
        operation_id=request.operation_id,
    )


async def facts(session: AsyncSession) -> dict[str, list[object]]:
    result = await mixed_facts(session)
    # The app role may read its stream's writer_node_id, not private sync_node rows.
    for table in ("pos_training_checkout", "register_receipt_counter", "sync_stream"):
        result[table] = list(
            (
                await session.execute(
                    text(
                        f"SELECT to_jsonb(row) FROM {table} row WHERE tenant_id=:tenant "
                        "ORDER BY to_jsonb(row)::text"
                    ),
                    {"tenant": runtime(session).stock.tenant_id},
                )
            ).scalars()
        )
    return result


def receipt_sequence(snapshot: dict[str, list[object]], register_id: UUID) -> int:
    for row in snapshot["register_receipt_counter"]:
        if isinstance(row, dict) and row.get("register_id") == str(register_id):
            sequence = row.get("last_receipt_seq")
            assert type(sequence) is int
            return sequence
    return 0


def unchanged_effects(snapshot: dict[str, list[object]]) -> dict[str, list[object]]:
    # The first allocation may initialize its counter and cloud writer stream.
    return {
        name: rows
        for name, rows in snapshot.items()
        if name
        not in {
            "sale",
            "audit_log",
            "register",
            "pos_training_checkout",
            "register_receipt_counter",
            "sync_stream",
        }
    }


@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("sale_format", ["pack", "blister", "unit"])
@pytest.mark.parametrize("mode", ["cash", "card", "qr", "split"])
async def test_training_completion_and_replay_leave_both_stocks_and_payments_unchanged(
    db_session: AsyncSession, mixed: bool, sale_format: SaleFormatV2, mode: str
) -> None:
    source, request = await training_source(
        db_session, mixed=mixed, sale_format=sale_format, mode=mode
    )
    before = await facts(db_session)
    first = await complete(db_session, request)
    assert first.receipt_number.startswith("TEST-")
    assert first.is_test is True and first.receipt.is_test is True
    assert first.receipt.change_amount == (Decimal("1.00") if mode in {"cash", "split"} else 0)
    assert unchanged_effects(await facts(db_session)) == unchanged_effects(before)
    assert source.sale.status == "completed" and source.sale.is_test is True
    assert source.sale.receipt_seq == first.receipt_sequence
    saved = await facts(db_session)
    assert len(saved["pos_training_checkout"]) == 1
    assert first.receipt_sequence == receipt_sequence(before, source.sale.register_id) + 1
    assert receipt_sequence(saved, source.sale.register_id) == first.receipt_sequence
    assert all(row in saved["sync_stream"] for row in before["sync_stream"])
    assert len(saved["sync_stream"]) <= len(before["sync_stream"]) + 1
    assert await complete(db_session, request) == first
    recovery = await read(db_session, request)
    assert recovery.state == "completed" and recovery.result == first
    assert await facts(db_session) == saved
    receipt = await POSService(POSRepository(db_session)).build_receipt(source.sale.id)
    assert receipt.receipt_number == first.receipt_number
    assert len(receipt.items) == len(first.receipt.lines)
    assert receipt.total == first.total_amount
    assert POSService.get_receipt_item_names(source.sale) == {}
    assert POSService.get_physical_receipt_items(source.sale) == []
    assert len(POSService.get_mixed_receipt_items(source.sale)) == len(first.receipt.lines)


async def test_saved_result_reads_after_closed_shift_without_current_settings_or_quote(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, request = await training_source(db_session, mixed=True, mode="split")
    result = await complete(db_session, request)
    await POSService(POSRepository(db_session)).close_shift(
        shift_id=source.sale.shift_id,
        closing_cash_actual=Decimal("0.00"),
        closed_by_user_id=source.sale.cashier_user_id,
        allowed_branch_ids={source.sale.branch_id},
    )

    async def unavailable(*args: object, **kwargs: object) -> None:
        raise AssertionError("Saved training recovery must not recalculate the live cart")

    monkeypatch.setattr(TrainingCheckoutService, "_complete", unavailable)
    monkeypatch.setattr(TrainingCheckoutRepository, "stock_snapshots", unavailable)
    before = await facts(db_session)
    assert await complete(db_session, request) == result
    recovery = await read(db_session, request)
    assert recovery.state == "completed" and recovery.result == result
    assert await facts(db_session) == before


async def test_draft_creation_operation_identity_is_preserved(
    db_session: AsyncSession,
) -> None:
    source, request = await training_source(db_session)
    original_id, original_hash = uuid4(), "a" * 64
    source.sale.operation_id = original_id
    source.sale.operation_hash = original_hash
    await db_session.flush()
    result = await complete(db_session, request)
    assert result.operation_id != original_id
    assert (source.sale.operation_id, source.sale.operation_hash) == (original_id, original_hash)
    assert await complete(db_session, request) == result
    assert (source.sale.operation_id, source.sale.operation_hash) == (original_id, original_hash)


@pytest.mark.parametrize("change", ["real_sale", "stale_quote", "missing_rx", "disabled_method"])
async def test_rejected_completion_creates_no_journal_or_effects(
    db_session: AsyncSession, change: str
) -> None:
    source, request = await training_source(db_session, mixed=True)
    if change == "real_sale":
        source.sale.is_test = False
        await db_session.flush()
    elif change == "stale_quote":
        request = request.model_copy(update={"expected_revision_hash": "a" * 64})
    elif change == "missing_rx":
        await db_session.execute(
            text("UPDATE tenant_catalog SET dispensing_type='prescription' WHERE id=:id"),
            {"id": runtime(db_session).stock.catalog_id},
        )
    else:
        await _set_payment_settings(
            db_session, tenant_id=source.sale.tenant_id, methods=["card"], mixed=False
        )
    before = await facts(db_session)
    with pytest.raises((BusinessRuleError, ConflictError)):
        await complete(db_session, request)
    assert await facts(db_session) == before


async def test_reused_operation_request_is_rejected_and_recovery_stays_identical(
    db_session: AsyncSession,
) -> None:
    _, request = await training_source(db_session)
    result = await complete(db_session, request)
    changed = request.model_copy(update={"cash_tendered": request.cash_tendered + Decimal("1.00")})
    before = await facts(db_session)
    with pytest.raises(ConflictError, match="reused"):
        await complete(db_session, changed)
    recovery = await read(db_session, request)
    assert recovery.state == "completed" and recovery.result == result
    assert await facts(db_session) == before


async def test_dirty_session_cannot_flush_implicitly_and_read_preserves_pending_changes(
    db_session: AsyncSession,
) -> None:
    source, request = await training_source(db_session)
    original = source.sale.total_amount
    source.sale.total_amount = Decimal("999.00")
    with pytest.raises(ConflictError, match="Persist pending"):
        await complete(db_session, request)
    recovery = await read(db_session, request)
    assert recovery.state == "not_found"
    assert source.sale.total_amount == Decimal("999.00")
    with db_session.no_autoflush:
        assert (
            await db_session.scalar(
                text("SELECT total_amount FROM sale WHERE id=:id"), {"id": source.sale.id}
            )
            == original
        )
    source.sale.total_amount = original
    await db_session.flush()


async def test_late_failure_rolls_back_journal_completion_and_receipt_counter(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, request = await training_source(db_session)
    sale_id = source.sale.id
    before = await facts(db_session)

    async def reject(self: TrainingCheckoutRepository) -> None:
        raise BusinessRuleError("final training completion refusal")

    monkeypatch.setattr(TrainingCheckoutRepository, "validate_completion", reject)
    with pytest.raises(BusinessRuleError, match="final training"):
        await complete(db_session, request)
    assert await facts(db_session) == before
    assert (
        await db_session.scalar(text("SELECT status FROM sale WHERE id=:id"), {"id": sale_id})
        == "draft"
    )


@pytest.mark.parametrize("scope", ["branch", "actor"])
async def test_saved_recovery_requires_current_owner_scope(
    db_session: AsyncSession, scope: str
) -> None:
    _, request = await training_source(db_session)
    await complete(db_session, request)
    context = runtime(db_session)
    before = await facts(db_session)
    with pytest.raises(PermissionDeniedError):
        await TrainingCheckoutService(TrainingCheckoutRepository(db_session)).read(
            tenant_id=context.stock.tenant_id,
            actor_id=uuid4() if scope == "actor" else context.actor_id,
            allowed_branch_ids=set() if scope == "branch" else {context.stock.branch_id},
            sale_id=request.sale_id,
            operation_id=request.operation_id,
        )
    assert await facts(db_session) == before
