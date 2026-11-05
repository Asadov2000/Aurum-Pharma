"""Internal posted return contracts; terminal evidence is operator-recorded."""

from decimal import Decimal, localcontext
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.domains.pos.mixed_cash_refund_schemas import MixedCashRefundLineV4
from app.domains.pos.unit_contracts import EXACT_CONTEXT, FrozenContract, Money, Position
from app.domains.pos.unit_electronic_refund_posting_schemas import ElectronicRefundPaymentV3


class MixedElectronicRefundReceiptV4(FrozenContract):
    schema_version: Literal[4] = 4
    document_kind: Literal["mixed_electronic_refund_receipt"] = "mixed_electronic_refund_receipt"
    parent_sale_id: UUID
    parent_receipt_number: str = Field(min_length=1, max_length=100)
    receipt_number: str = Field(min_length=1, max_length=100)
    receipt_sequence: Position
    completed_at: AwareDatetime
    is_test: Literal[False] = False
    lines: tuple[MixedCashRefundLineV4, ...] = Field(min_length=1, max_length=200)
    payments: tuple[ElectronicRefundPaymentV3, ...] = Field(min_length=1, max_length=3)
    total_amount: Money
    currency: Literal["TJS"] = "TJS"

    @model_validator(mode="after")
    def balanced(self) -> Self:
        if tuple(line.position for line in self.lines) != tuple(range(1, len(self.lines) + 1)):
            raise ValueError("Refund line positions differ")
        positions = tuple(line.original_position for line in self.lines)
        if positions != tuple(sorted(set(positions))):
            raise ValueError("Refund source order differs")
        if len({line.original_line_id for line in self.lines}) != len(self.lines):
            raise ValueError("Refund source lines must be unique")
        if len({part.id for part in self.payments}) != len(self.payments) or len(
            {part.original_payment_id for part in self.payments}
        ) != len(self.payments):
            raise ValueError("Refund payment identities must be unique")
        if not any(part.method != "cash" for part in self.payments):
            raise ValueError("Electronic refund requires electronic payment evidence")
        with localcontext(EXACT_CONTEXT):
            if self.total_amount <= 0 or any(
                value != self.total_amount
                for value in (
                    sum((line.net_amount for line in self.lines), Decimal("0.00")),
                    sum((part.amount for part in self.payments), Decimal("0.00")),
                )
            ):
                raise ValueError("Refund receipt totals differ")
        return self


class PostedMixedElectronicRefund(FrozenContract):
    state: Literal["posted_refund"] = "posted_refund"
    operation_id: UUID
    parent_sale_id: UUID
    sale_id: UUID
    tenant_id: UUID
    branch_id: UUID
    register_id: UUID
    shift_id: UUID
    actor_id: UUID
    total_amount: Money
    physical_cost_amount: Money
    receipt: MixedElectronicRefundReceiptV4

    @model_validator(mode="after")
    def bound_receipt(self) -> Self:
        if self.sale_id == self.parent_sale_id:
            raise ValueError("A refund must create a distinct return sale")
        if (self.receipt.parent_sale_id, self.receipt.total_amount) != (
            self.parent_sale_id,
            self.total_amount,
        ):
            raise ValueError("Posted refund receipt scope differs")
        return self
