"""Mixed electronic quotes authorize immutable history without making payouts."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AurumError, BusinessRuleError, NotFoundError, PermissionDeniedError
from app.domains.pos.mixed_cash_checkout_models import MixedCashCheckout
from app.domains.pos.mixed_cash_refund_api_schemas import MixedRefundQuoteRequest
from app.domains.pos.mixed_documents import LegacyResultLineV4, MixedCheckoutResultV4
from app.domains.pos.mixed_electronic_refund_preview import MixedElectronicRefundPreview
from app.domains.pos.mixed_electronic_refund_preview_repository import (
    MixedElectronicRefundPreviewRepository,
)
from app.domains.pos.mixed_electronic_refund_preview_service import (
    MixedElectronicRefundPreviewService,
)
from app.domains.pos.mixed_return_allocation import LegacyReturnSelection
from app.domains.pos.mixed_return_journal_repository import MixedReturnJournalRepository
from app.domains.pos.mixed_return_journal_service import MixedReturnJournalService
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.pos.test_mixed_electronic_checkout import (
    attempts,
    complete,
    confirm,
    facts,
    prepared,
)
from tests.domains.pos.test_physical_cash_refund import close_shift
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def original(session: AsyncSession, methods: tuple[str, ...]) -> MixedCheckoutResultV4:
    request, ids = await prepared(session, methods)
    await confirm(session, request, await attempts(session, request, ids))
    return await complete(session, request)


def selection(source: MixedCheckoutResultV4) -> MixedRefundQuoteRequest:
    return MixedRefundQuoteRequest.model_validate(
        {
            "items": [
                (
                    {"inventory_kind": "legacy_pack", "line_id": line.id, "quantity": line.quantity}
                    if isinstance(line, LegacyResultLineV4)
                    else {
                        "inventory_kind": "physical_unit",
                        "line_id": line.id,
                        "sale_format": line.sale_format,
                        "quantity": line.quantity,
                    }
                )
                for line in source.lines
            ]
        }
    )


async def preview(
    session: AsyncSession,
    source: MixedCheckoutResultV4,
    *,
    branches: set[UUID] | None = None,
    actor: UUID | None = None,
    tenant: UUID | None = None,
) -> MixedElectronicRefundPreview:
    context = runtime(session)
    return await MixedElectronicRefundPreviewService(
        MixedElectronicRefundPreviewRepository(session)
    ).quote(
        tenant_id=tenant or source.tenant_id,
        parent_sale_id=source.sale_id,
        actor_id=actor or context.actor_id,
        allowed_branch_ids=branches if branches is not None else {source.branch_id},
        request=selection(source),
    )


async def financial_facts(session: AsyncSession) -> dict[str, list[object]]:
    result = await facts(session)
    for table in (
        "pos_mixed_return_command",
        "pos_mixed_return_line",
        "pos_mixed_return_slice",
        "pos_mixed_cash_refund",
        "pos_unit_refund_payment_component",
        "pos_refund_attempt",
    ):
        rows = await session.execute(
            text(
                f"SELECT to_jsonb(row) FROM {table} row WHERE tenant_id=:tenant "
                "ORDER BY to_jsonb(row)::text"
            ),
            {"tenant": runtime(session).stock.tenant_id},
        )
        result[table] = list(rows.scalars())
    return result


@pytest.mark.parametrize("methods", [("card",), ("qr",), ("cash", "card", "qr")])
async def test_full_quote_keeps_original_payment_ids_and_has_no_effects(
    db_session: AsyncSession, methods: tuple[str, ...]
) -> None:
    source = await original(db_session, methods)
    before = await financial_facts(db_session)
    result = await preview(db_session, source)
    assert result.state == "preview" and result.total_amount == source.total_amount
    assert result.original_receipt_number == source.receipt_number
    assert {line.inventory_kind for line in result.lines} == {"legacy_pack", "physical_unit"}
    assert {part.original_payment_id: (part.method, part.amount) for part in result.payments} == {
        part.id: (part.method, part.amount) for part in source.payments
    }
    assert await preview(db_session, source) == result
    assert await financial_facts(db_session) == before


@pytest.mark.parametrize("denial", ["branch", "actor", "tenant"])
async def test_foreign_scope_cannot_read_or_change_mixed_refund_history(
    db_session: AsyncSession, denial: str
) -> None:
    source = await original(db_session, ("card",))
    before = await financial_facts(db_session)
    options: dict[str, object] = {}
    if denial == "branch":
        options["branches"] = {uuid4()}
    elif denial == "actor":
        options["actor"] = uuid4()
    else:
        options["tenant"] = uuid4()
    with pytest.raises((PermissionDeniedError, NotFoundError, BusinessRuleError)):
        await preview(db_session, source, **options)
    assert await financial_facts(db_session) == before


async def test_closed_original_shift_remains_read_only_and_preserves_pending_edits(
    db_session: AsyncSession,
) -> None:
    source = await original(db_session, ("cash", "card", "qr"))
    first = await preview(db_session, source)
    await close_shift(db_session, source.shift_id)
    before = await financial_facts(db_session)
    marker = await db_session.get(MixedCashCheckout, (source.tenant_id, source.operation_id))
    assert marker is not None
    original_hash = marker.request_hash
    marker.request_hash = "f" * 64
    assert marker in db_session.dirty
    assert await preview(db_session, source) == first
    assert marker.request_hash == "f" * 64 and marker in db_session.dirty
    marker.request_hash = original_hash
    assert await financial_facts(db_session) == before


async def test_quarantined_goods_without_completed_payout_history_fail_closed(
    db_session: AsyncSession,
) -> None:
    source = await original(db_session, ("card",))
    context = runtime(db_session)
    selections = tuple(
        (
            LegacyReturnSelection(UUID(str(line.id)), line.quantity)
            if isinstance(line, LegacyResultLineV4)
            else PhysicalReturnSelection(UUID(str(line.id)), line.sale_format, line.quantity)
        )
        for line in source.lines
    )
    journal = await MixedReturnJournalService(MixedReturnJournalRepository(db_session)).receive(
        tenant_id=source.tenant_id,
        actor_id=context.actor_id,
        operation_id=uuid4(),
        parent_sale_id=source.sale_id,
        selections=selections,
    )
    assert journal.total_amount == source.total_amount
    before = await financial_facts(db_session)
    with pytest.raises(AurumError, match="reconciliation"):
        await preview(db_session, source)
    assert await financial_facts(db_session) == before
