"""Read-only electronic options use reconciled history, never cash-only discovery."""

from decimal import Decimal, localcontext
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AurumError, BusinessRuleError, NotFoundError, PermissionDeniedError
from app.domains.pos import mixed_electronic_refund_preview_service as projection
from app.domains.pos.mixed_cash_refund_api_schemas import (
    MixedLegacyRefundOptionLine,
    MixedPhysicalRefundOptionLine,
    MixedRefundOptions,
)
from app.domains.pos.mixed_documents import build_mixed_receipt
from app.domains.pos.mixed_electronic_refund_history_schemas import MixedElectronicRefundHistory
from app.domains.pos.mixed_electronic_refund_preview_repository import (
    MixedElectronicRefundPreviewRepository,
)
from app.domains.pos.mixed_electronic_refund_preview_service import (
    MixedElectronicRefundPreviewService,
)
from app.domains.pos.mixed_return_journal_repository import MixedReturnJournalRepository
from tests.domains.pos.test_mixed_electronic_refund_history import history, legacy_return
from tests.domains.pos.test_mixed_electronic_refund_intent_service import Session


class Options:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.history = history()
        self.session = Session()
        self.service = MixedElectronicRefundPreviewService(
            MixedElectronicRefundPreviewRepository(cast(AsyncSession, self.session))
        )
        self.identity = AsyncMock(side_effect=self.check_identity)
        self.authority = AsyncMock()
        self.read = AsyncMock(side_effect=self.read_history)
        self.settings = AsyncMock(return_value="required")
        monkeypatch.setattr(MixedReturnJournalRepository, "assert_identity", self.identity)
        monkeypatch.setattr(MixedReturnJournalRepository, "assert_actor", self.authority)
        monkeypatch.setattr(MixedElectronicRefundPreviewRepository, "read", self.read)
        monkeypatch.setattr(
            MixedElectronicRefundPreviewRepository, "read_reason_mode", self.settings
        )

    async def check_identity(self, tenant: UUID, actor: UUID) -> None:
        assert self.session.autoflush_disabled
        if (tenant, actor) != (
            self.history.sale.tenant_id,
            self.history.sale.cashier_user_id,
        ):
            raise PermissionDeniedError("Active tenant actor is required")

    async def read_history(self, tenant: UUID, parent: UUID) -> MixedElectronicRefundHistory | None:
        assert self.session.autoflush_disabled
        return (
            self.history
            if (tenant, parent) == (self.history.sale.tenant_id, self.history.sale.id)
            else None
        )

    async def options(
        self,
        *,
        tenant: UUID | None = None,
        actor: UUID | None = None,
        branches: set[UUID] | None = None,
        parent: UUID | None = None,
    ) -> MixedRefundOptions:
        return await self.service.options(
            tenant_id=tenant or self.history.sale.tenant_id,
            parent_sale_id=parent or self.history.sale.id,
            actor_id=actor or self.history.sale.cashier_user_id,
            allowed_branch_ids=branches if branches is not None else {self.history.sale.branch_id},
        )


@pytest.fixture
def options(monkeypatch: pytest.MonkeyPatch) -> Options:
    return Options(monkeypatch)


def single_method(h: MixedElectronicRefundHistory, method: str) -> MixedElectronicRefundHistory:
    original = h.checkout.result_payload
    part = next(part for part in original.payments if part.method == method).model_copy(
        update={"amount": original.total_amount}
    )
    original = original.model_copy(update={"payments": (part,)})
    receipt = build_mixed_receipt(original)
    return h.model_copy(
        update={
            "sale": h.sale.model_copy(update={"receipt_snapshot": receipt}),
            "checkout": h.checkout.model_copy(
                update={"result_payload": original, "receipt_payload": receipt}
            ),
            "payments": tuple(
                payment.model_copy(update={"amount": original.total_amount})
                for payment in h.payments
                if payment.id == part.id
            ),
            "components": tuple(
                component.model_copy(update={"amount": original.total_amount})
                for component in h.components
                if component.id == part.id
            ),
        }
    )


@pytest.mark.parametrize("method", ["card", "qr", "split"])
async def test_options_preserve_original_mixed_order_packaging_and_scope(
    options: Options, method: str
) -> None:
    if method != "split":
        options.history = single_method(options.history, method)
    before = options.history.model_dump(mode="json")
    result = await options.options()
    source = options.history.checkout.result_payload
    assert (result.parent_sale_id, result.branch_id, result.register_id) == (
        source.sale_id,
        source.branch_id,
        source.register_id,
    )
    assert result.original_receipt_number == source.receipt_number
    assert result.reason_mode == "required"
    assert [line.line_id for line in result.lines] == [line.id for line in source.lines]
    legacy, physical = result.lines
    assert isinstance(legacy, MixedLegacyRefundOptionLine)
    assert isinstance(physical, MixedPhysicalRefundOptionLine)
    assert legacy.remaining_quantity == legacy.sold_quantity == Decimal("3.000")
    assert legacy.returned_quantity == Decimal("0.000")
    assert physical.remaining_base_units == physical.sold_base_units == 30
    assert physical.returned_base_units == 0
    assert physical.packaging == source.lines[1].packaging
    assert physical.original_sale_format == source.lines[1].sale_format
    assert options.history.model_dump(mode="json") == before
    assert not options.session.autoflush_disabled
    assert not options.session.new | options.session.dirty | options.session.deleted
    options.identity.assert_awaited_once_with(source.tenant_id, source.cashier_id)
    assert options.authority.await_count == 2
    options.settings.assert_awaited_once_with(source.tenant_id)
    assert "cost" not in result.model_dump_json()
    assert "terminal" not in result.model_dump_json()


@pytest.mark.parametrize("denial", ["tenant", "actor", "branch", "parent"])
async def test_options_fail_closed_for_foreign_scope(options: Options, denial: str) -> None:
    arguments = {"branches": {uuid4()}} if denial == "branch" else {denial: uuid4()}
    with pytest.raises((PermissionDeniedError, NotFoundError)):
        await options.options(**arguments)
    options.settings.assert_not_awaited()
    assert not options.session.autoflush_disabled


@pytest.mark.parametrize("mutation", ["journal", "payment", "unreconciled", "cursor"])
async def test_corrupt_or_unreconciled_history_cannot_supply_options(
    options: Options, mutation: str
) -> None:
    h = options.history
    if mutation == "journal":
        h = h.model_copy(
            update={"checkout": h.checkout.model_copy(update={"payment_journal_version": 2})}
        )
    elif mutation == "payment":
        h = h.model_copy(update={"payments": h.payments[:-1]})
    else:
        row = legacy_return(h)
        if mutation == "cursor":
            row = row.model_copy(update={"legacy_offset": Decimal("1.000")})
        h = h.model_copy(update={"return_lines": (row,)})
    options.history = h
    with pytest.raises((AurumError, BusinessRuleError)):
        await options.options()
    options.settings.assert_not_awaited()
    assert not options.session.autoflush_disabled


async def test_options_recheck_actor_after_awaiting_current_settings(options: Options) -> None:
    options.authority.side_effect = [None, PermissionDeniedError("Actor access changed")]
    with pytest.raises(PermissionDeniedError, match="access changed"):
        await options.options()
    assert options.authority.await_count == 2
    assert not options.session.autoflush_disabled


async def test_unavailable_reason_policy_never_defaults_to_optional(options: Options) -> None:
    options.settings.return_value = None
    with pytest.raises(AurumError, match="settings"):
        await options.options()
    assert not options.session.autoflush_disabled


async def test_options_keep_pending_orm_edits_without_transaction_or_flush(
    options: Options,
) -> None:
    marker = object()
    options.session.new.add(marker)
    options.session.dirty.add(marker)
    options.session.deleted.add(marker)
    await options.options()
    assert options.session.new == options.session.dirty == options.session.deleted == {marker}
    assert not options.session.autoflush_disabled


async def test_options_validate_exact_remaining_cursor_math(
    options: Options, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = options.history.checkout.result_payload
    # Domain history/reconciliation has separate corruption tests above. Isolate
    # the public projection's subtraction under an intentionally small context.
    monkeypatch.setattr(
        projection,
        "returned_goods",
        lambda _history, _source: ({source.lines[0].id: Decimal("0.125")}, {source.lines[1].id: 3}),
    )
    monkeypatch.setattr(projection, "require_reconciled_history", lambda *_arguments: None)
    with localcontext() as context:
        context.prec = 2
        result = await options.options()
    legacy, physical = result.lines
    assert isinstance(legacy, MixedLegacyRefundOptionLine)
    assert isinstance(physical, MixedPhysicalRefundOptionLine)
    assert legacy.remaining_quantity == Decimal("2.875")
    assert physical.remaining_base_units == 27
