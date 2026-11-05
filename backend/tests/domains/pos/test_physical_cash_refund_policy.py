"""Reasons and reviewed amounts gate the whole payout, including quarantine."""

import json
from decimal import Decimal
from hashlib import sha256
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, ConflictError
from app.domains.foundation.repository import FoundationRepository
from app.domains.pos.physical_cash_refund_models import PhysicalCashRefund
from app.domains.pos.physical_cash_refund_repository import PhysicalCashRefundRepository
from app.domains.pos.physical_cash_refund_schemas import PhysicalCashRefundResultV3
from app.domains.pos.physical_cash_refund_service import (
    PhysicalCashRefundService,
    physical_cash_refund_fingerprint,
)
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection, plan_physical_return
from app.domains.pos.physical_return_journal_service import physical_return_fingerprint
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.pos.test_physical_cash_checkout import complete
from tests.domains.pos.test_physical_cash_refund import facts
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request

db_session = receiving_session_fixture


async def _source(session: AsyncSession) -> PhysicalCheckoutResultV3:
    source = await checkout_source(session)
    await put_physical(session, source.sale.id, sale_format="pack")
    return await complete(session, await saved_request(session, source.sale))


async def _policy(session: AsyncSession, tenant_id: UUID, mode: str) -> None:
    repo = FoundationRepository(session)
    settings = await repo.get_settings(tenant_id)
    assert settings is not None
    await repo.update_settings(settings, refund_reason_mode=mode)


async def _refund(
    session: AsyncSession,
    source: PhysicalCheckoutResultV3,
    *,
    operation_id: UUID | None = None,
    reason_code: str | None = None,
    comment: str | None = None,
    expected: Decimal | None = None,
    revision: str | None = None,
) -> PhysicalCashRefundResultV3:
    return await PhysicalCashRefundService(PhysicalCashRefundRepository(session)).refund(
        tenant_id=source.tenant_id,
        actor_id=source.cashier_id,
        operation_id=operation_id or uuid4(),
        parent_sale_id=source.sale_id,
        shift_id=source.shift_id,
        selections=(PhysicalReturnSelection(source.lines[0].id, "unit", 1),),
        reason_code=reason_code,
        comment=comment,
        expected_total_amount=expected,
        expected_revision_hash=revision,
    )


def test_empty_reason_fields_preserve_the_legacy_request_fingerprint() -> None:
    tenant, actor, sale, shift, line = (uuid4() for _ in range(5))
    selections = (PhysicalReturnSelection(line, "unit", 1),)
    legacy = {
        "version": 3,
        "kind": "physical_cash_refund",
        "tenant_id": str(tenant),
        "actor_id": str(actor),
        "parent_sale_id": str(sale),
        "shift_id": str(shift),
        "journal_request_hash": physical_return_fingerprint(tenant, actor, sale, selections),
    }
    expected = sha256(
        json.dumps(legacy, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert physical_cash_refund_fingerprint(tenant, actor, sale, shift, selections) == expected
    assert (
        physical_cash_refund_fingerprint(
            tenant, actor, sale, shift, selections, reason_code=" \t", comment="\u2003\n"
        )
        == expected
    )


@pytest.mark.parametrize(
    "mode,reason,comment,error",
    [
        ("off", "other", None, "fields are disabled"),
        ("required", None, None, "reason is required"),
        ("required_with_text", "other", " \n", "comment is required"),
        ("optional", "unknown", None, "Unsupported refund reason"),
        ("optional", "other", "x" * 501, "comment is too long"),
    ],
)
async def test_reason_policy_denial_leaves_no_journal_or_financial_changes(
    db_session: AsyncSession,
    mode: str,
    reason: str | None,
    comment: str | None,
    error: str,
) -> None:
    source = await _source(db_session)
    await _policy(db_session, source.tenant_id, mode)
    before = await facts(db_session)
    with pytest.raises(BusinessRuleError, match=error):
        await _refund(db_session, source, reason_code=reason, comment=comment)
    assert await facts(db_session) == before


async def test_normalized_reason_is_immutable_and_replay_uses_saved_policy(
    db_session: AsyncSession,
) -> None:
    source = await _source(db_session)
    await _policy(db_session, source.tenant_id, "required_with_text")
    operation_id = uuid4()
    result = await _refund(
        db_session,
        source,
        operation_id=operation_id,
        reason_code=" other ",
        comment='\u2003Проверка "текста" 😀\n',
    )
    marker = await PhysicalCashRefundRepository(db_session).get(source.tenant_id, operation_id)
    assert marker is not None
    assert marker.reason_code == "other"
    assert marker.comment == 'Проверка "текста" 😀'
    audit = await db_session.scalar(
        text(
            "SELECT new_values->>'comment' FROM audit_log WHERE table_name="
            "'pos_physical_cash_refund' AND record_id=:id ORDER BY created_at DESC LIMIT 1"
        ),
        {"id": operation_id},
    )
    assert audit == "***"
    await _policy(db_session, source.tenant_id, "off")
    before = await facts(db_session)
    assert (
        await _refund(
            db_session,
            source,
            operation_id=operation_id,
            reason_code="other",
            comment='Проверка "текста" 😀',
            expected=result.total_amount,
        )
        == result
    )
    with pytest.raises(ConflictError):
        await _refund(
            db_session, source, operation_id=operation_id, reason_code="other", comment="changed"
        )
    assert await facts(db_session) == before


async def test_reviewed_amount_conflict_is_atomic_and_replay_checks_it(
    db_session: AsyncSession,
) -> None:
    source = await _source(db_session)
    selections = (PhysicalReturnSelection(UUID(str(source.lines[0].id)), "unit", 1),)
    amount = plan_physical_return(source, selections, returned_base_units={}).total_amount
    operation_id = uuid4()
    before = await facts(db_session)
    with pytest.raises(ConflictError, match="amount changed"):
        await _refund(
            db_session, source, operation_id=operation_id, expected=amount + Decimal("0.01")
        )
    assert await facts(db_session) == before
    result = await _refund(db_session, source, operation_id=operation_id, expected=amount)
    before = await facts(db_session)
    with pytest.raises(ConflictError, match="amount changed"):
        await _refund(
            db_session,
            source,
            operation_id=operation_id,
            expected=result.total_amount + Decimal("0.01"),
        )
    assert await facts(db_session) == before


async def test_database_rejects_disabled_reason_when_service_marker_is_tampered(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = await _source(db_session)
    await _policy(db_session, source.tenant_id, "off")
    insert = PhysicalCashRefundRepository.insert

    async def forged_insert(repo: PhysicalCashRefundRepository, marker: PhysicalCashRefund) -> None:
        marker.reason_code = "other"
        await insert(repo, marker)

    monkeypatch.setattr(PhysicalCashRefundRepository, "insert", forged_insert)
    before = await facts(db_session)
    with pytest.raises(DBAPIError, match="physical_cash_refund_reason_invalid"):
        await _refund(db_session, source)
    assert await facts(db_session) == before


async def test_changed_return_cursor_invalidates_review_even_without_amount_check(
    db_session: AsyncSession,
) -> None:
    from app.domains.pos.physical_cash_refund_review import physical_refund_revision

    source = await _source(db_session)
    selections = (PhysicalReturnSelection(source.lines[0].id, "unit", 1),)
    revision = physical_refund_revision(source, {}, selections)
    await _refund(db_session, source)
    before = await facts(db_session)
    with pytest.raises(ConflictError, match="history changed"):
        await _refund(db_session, source, revision=revision)
    assert await facts(db_session) == before


async def test_review_hash_is_stored_and_replayed_after_the_cursor_advances(
    db_session: AsyncSession,
) -> None:
    from app.domains.pos.physical_cash_refund_review import physical_refund_revision

    source = await _source(db_session)
    await _policy(db_session, source.tenant_id, "off")
    selections = (PhysicalReturnSelection(source.lines[0].id, "unit", 1),)
    revision = physical_refund_revision(source, {}, selections)
    operation_id = uuid4()
    result = await _refund(db_session, source, operation_id=operation_id, revision=revision)
    marker = await PhysicalCashRefundRepository(db_session).get(source.tenant_id, operation_id)
    assert marker is not None and marker.review_hash == revision
    assert marker.reason_code is None and marker.comment is None
    before = await facts(db_session)
    assert (
        await _refund(
            db_session,
            source,
            operation_id=operation_id,
            revision=revision,
            expected=result.total_amount,
        )
        == result
    )
    changed = physical_refund_revision(source, {source.lines[0].id: 1}, selections)
    with pytest.raises(ConflictError, match="different request"):
        await _refund(db_session, source, operation_id=operation_id, revision=changed)
    assert await facts(db_session) == before
