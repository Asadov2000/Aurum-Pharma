"""Cost-free quarantine acknowledgments; these are never payment receipts."""

from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import UUID4, Field, model_validator

from app.domains.customer_returns.reasons import RefundReasonCode
from app.domains.pos.physical_cash_refund_api_schemas import PhysicalRefundQuoteRequest
from app.domains.pos.unit_contracts import FrozenContract, Money, WholeQuantity


class PhysicalZeroReturnRequest(PhysicalRefundQuoteRequest):
    operation_id: UUID4
    expected_revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)
    reason_code: RefundReasonCode | None = None
    comment: str | None = Field(default=None, max_length=500, strict=True)


class PhysicalZeroReturnLine(FrozenContract):
    line_id: UUID
    base_units: WholeQuantity
    offset: int = Field(strict=True, ge=0)


class PhysicalZeroReturnResult(FrozenContract):
    version: Literal[1] = 1
    kind: Literal["physical_zero_return"] = "physical_zero_return"
    operation_id: UUID
    parent_sale_id: UUID
    tenant_id: UUID
    branch_id: UUID
    actor_id: UUID
    state: Literal["prepared_quarantine"] = "prepared_quarantine"
    currency: Literal["TJS"] = "TJS"
    total_amount: Money
    lines: tuple[PhysicalZeroReturnLine, ...] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def zero_and_unique(self) -> Self:
        if self.total_amount != 0:
            raise ValueError("Quarantine without payout requires zero historical net amount")
        if len({line.line_id for line in self.lines}) != len(self.lines):
            raise ValueError("Return lines must be unique")
        return self


class PhysicalZeroReturnNotFound(FrozenContract):
    state: Literal["not_found"] = "not_found"
    operation_id: UUID4
    parent_sale_id: UUID


PhysicalZeroReturnRead = Annotated[
    PhysicalZeroReturnNotFound | PhysicalZeroReturnResult, Field(discriminator="state")
]
