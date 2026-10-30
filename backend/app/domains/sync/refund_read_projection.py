"""Validate trusted refund read evidence against immutable historical checkout.

This module allocates no payout and admits no quarantine goods to stock. Offsets
may contain gaps from existing zero-value/quarantine-only returns, but paid
refund ranges must never overlap one another or exceed their historical basis.
"""

from bisect import bisect_left
from collections.abc import Sequence
from decimal import Decimal, localcontext
from uuid import UUID

from app.domains.pos.mixed_cash_refund_schemas import (
    MixedCashRefundResultV4,
    MixedLegacyCashRefundLineV4,
    MixedPhysicalCashRefundLineV4,
)
from app.domains.pos.mixed_documents import LegacyResultLineV4, MixedCheckoutResultV4
from app.domains.pos.mixed_return_allocation import (
    LegacyReturnSelection,
    MixedReturnPlan,
    plan_mixed_return,
)
from app.domains.pos.mixed_return_journal_schemas import (
    LegacyReturnJournalLine,
    MixedReturnJournalResult,
)
from app.domains.pos.physical_cash_refund_schemas import (
    PhysicalCashRefundLineV3,
    PhysicalCashRefundResultV3,
)
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3, PhysicalResultLineV3
from app.domains.pos.physical_return_allocation import (
    PhysicalReturnPlan,
    PhysicalReturnSelection,
    plan_physical_return,
)
from app.domains.pos.retail_refund_events import RetailRefundEventResult
from app.domains.pos.retail_sale_events import RetailCheckoutResult
from app.domains.pos.unit_contracts import EXACT_CONTEXT


def _scope(refund: RetailRefundEventResult, parent: RetailCheckoutResult) -> None:
    result, receipt = refund.result, refund.result.receipt
    mixed = isinstance(result, MixedCashRefundResultV4) or refund.source_kind == "mixed_electronic"
    if (
        mixed != isinstance(parent, MixedCheckoutResultV4)
        or result.parent_sale_id != parent.sale_id
        or result.sale_id == parent.sale_id
        or result.tenant_id != parent.tenant_id
        or result.branch_id != parent.branch_id
        or result.register_id != parent.register_id
        or receipt.parent_receipt_number != parent.receipt_number
        or receipt.currency != parent.currency
        or receipt.is_test
        or parent.is_test
        or receipt.completed_at < parent.completed_at
    ):
        raise ValueError("Refund parent scope or lifecycle differs")
    originals = {line.id: line for line in parent.lines}
    for line in receipt.lines:
        original = originals.get(line.original_line_id)
        if original is None or (
            line.name != original.name
            or line.original_retail_unit_price != original.original_retail_unit_price
        ):
            raise ValueError("Refund receipt differs from its original line")
        if isinstance(original, PhysicalResultLineV3):
            if not isinstance(line, PhysicalCashRefundLineV3) or (
                line.packaging != original.packaging
                or line.original_sale_format != original.sale_format
            ):
                raise ValueError("Refund receipt physical basis differs")
        elif not isinstance(line, MixedLegacyCashRefundLineV4):
            raise ValueError("Refund receipt legacy basis differs")
        if mixed and (
            not isinstance(line, MixedLegacyCashRefundLineV4 | MixedPhysicalCashRefundLineV4)
            or line.original_position != original.position
        ):
            raise ValueError("Refund receipt original order differs")


def _allocation(refund: RetailRefundEventResult, parent: RetailCheckoutResult) -> None:
    journal = refund.quarantine
    plan: MixedReturnPlan | PhysicalReturnPlan
    receipt_lines = {line.original_line_id: line for line in refund.result.receipt.lines}
    if isinstance(journal, MixedReturnJournalResult):
        if not isinstance(parent, MixedCheckoutResultV4):
            raise ValueError("Mixed refund requires a mixed parent")
        selections = tuple(
            (
                LegacyReturnSelection(line.line_id, line.quantity)
                if isinstance(line, LegacyReturnJournalLine)
                else PhysicalReturnSelection(line.line_id, line.sale_format, line.quantity)
            )
            for line in journal.lines
        )
        plan = plan_mixed_return(
            parent,
            selections,
            returned_legacy_quantities={
                line.line_id: line.offset
                for line in journal.lines
                if isinstance(line, LegacyReturnJournalLine)
            },
            returned_base_units={
                line.line_id: line.offset
                for line in journal.lines
                if not isinstance(line, LegacyReturnJournalLine)
            },
        )
        originals = {line.id: line for line in parent.lines}
        for line in journal.lines:
            if isinstance(line, LegacyReturnJournalLine):
                original = originals.get(line.line_id)
                if (
                    not isinstance(original, LegacyResultLineV4)
                    or original.batch_id != line.batch_id
                ):
                    raise ValueError("Refund legacy batch differs from its parent")
    else:
        if not isinstance(parent, PhysicalCheckoutResultV3):
            raise ValueError("Physical refund requires a physical parent")
        plan = plan_physical_return(
            parent,
            tuple(
                PhysicalReturnSelection(
                    line.line_id,
                    receipt_line.sale_format,
                    receipt_line.quantity,
                )
                for line in journal.lines
                if isinstance(receipt_line := receipt_lines[line.line_id], PhysicalCashRefundLineV3)
            ),
            returned_base_units={line.line_id: line.offset for line in journal.lines},
        )
    expected = {line.line_id: line for line in plan.lines}
    if plan.total_amount != journal.total_amount or any(
        (line.offset, line.net_amount, line.discount_amount, line.gross_amount)
        != (
            expected[line.line_id].offset,
            expected[line.line_id].net_amount,
            expected[line.line_id].discount_amount,
            expected[line.line_id].gross_amount,
        )
        for line in journal.lines
    ):
        raise ValueError("Refund allocation differs from original net and discount")


def _ranges(refund: RetailRefundEventResult) -> dict[UUID, tuple[Decimal, Decimal]]:
    with localcontext(EXACT_CONTEXT):
        return {
            line.line_id: (
                Decimal(line.offset),
                Decimal(line.offset)
                + (line.quantity if isinstance(line, LegacyReturnJournalLine) else line.base_units),
            )
            for line in refund.quarantine.lines
        }


def _payments(
    refund: RetailRefundEventResult, parent: RetailCheckoutResult
) -> dict[UUID | None, Decimal]:
    result = refund.result
    if isinstance(result, PhysicalCashRefundResultV3 | MixedCashRefundResultV4):
        if any(part.method != "cash" for part in parent.payments):
            raise ValueError("Cash refund parent contains external payments")
        return {None: result.total_amount}
    originals = {part.id: part for part in parent.payments}
    amounts: dict[UUID | None, Decimal] = {}
    for part in result.receipt.payments:
        original = originals.get(part.original_payment_id)
        if original is None or (
            part.method != original.method
            or part.currency != original.currency
            or part.amount > original.amount
        ):
            raise ValueError("Refund payment differs from original payment")
        amounts[original.id] = part.amount
    return amounts


def validate_retail_refund_parent(
    refund: RetailRefundEventResult,
    parent: RetailCheckoutResult,
    prior: Sequence[RetailRefundEventResult],
) -> None:
    """Check one already authenticated event against preceding paid history only."""
    current = RetailRefundEventResult.model_validate(refund.model_dump(mode="python"))
    _scope(current, parent)
    _allocation(current, parent)
    ranges = {key: [value] for key, value in _ranges(current).items()}
    payments = _payments(current, parent)
    seen_operations = {current.result.operation_id}
    seen_sales = {current.result.sale_id}
    total = current.result.total_amount
    with localcontext(EXACT_CONTEXT):
        for old in prior:
            _scope(old, parent)
            _allocation(old, parent)
            if old.result.operation_id in seen_operations or old.result.sale_id in seen_sales:
                raise ValueError("Refund history repeats a financial identity")
            seen_operations.add(old.result.operation_id)
            seen_sales.add(old.result.sale_id)
            for line_id, span in _ranges(old).items():
                existing = ranges.setdefault(line_id, [])
                index = bisect_left(existing, span)
                if (index and existing[index - 1][1] > span[0]) or (
                    index < len(existing) and existing[index][0] < span[1]
                ):
                    raise ValueError("Refund quantity overlaps preceding return history")
                existing.insert(index, span)
            total += old.result.total_amount
            for payment_id, amount in _payments(old, parent).items():
                payments[payment_id] = payments.get(payment_id, Decimal("0.00")) + amount
        available: dict[UUID | None, Decimal] = {part.id: part.amount for part in parent.payments}
        available[None] = parent.total_amount
        if total > parent.total_amount or any(
            amount > available[payment_id] for payment_id, amount in payments.items()
        ):
            raise ValueError("Refund history exceeds original paid amount")
