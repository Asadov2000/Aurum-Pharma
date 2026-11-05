"""Internal historical physical cash return documents, separate from sale receipts."""

from decimal import Decimal, localcontext
from typing import Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from app.domains.inventory.unit_stock_v2 import SaleFormatV2, UnitPackagingV2, to_base_units_v2
from app.domains.pos.unit_contracts import (
    EXACT_CONTEXT,
    FrozenContract,
    Money,
    Position,
    WholeQuantity,
)


class PhysicalCashRefundLineV3(FrozenContract):
    original_line_id: UUID
    position: Position
    name: str = Field(min_length=1, max_length=500)
    packaging: UnitPackagingV2
    original_sale_format: SaleFormatV2
    original_retail_unit_price: Money
    sale_format: SaleFormatV2
    quantity: WholeQuantity
    base_units: WholeQuantity
    offset: int = Field(strict=True, ge=0)
    gross_amount: Money
    discount_amount: Money
    net_amount: Money

    @model_validator(mode="after")
    def balanced(self) -> Self:
        if self.base_units != to_base_units_v2(
            self.packaging, quantity=self.quantity, sale_format=self.sale_format
        ):
            raise ValueError("Refund physical quantity differs")
        with localcontext(EXACT_CONTEXT):
            if self.gross_amount != self.net_amount + self.discount_amount:
                raise ValueError("Refund allocation differs")
        return self


class PhysicalCashRefundReceiptV3(FrozenContract):
    schema_version: Literal[3] = 3
    document_kind: Literal["physical_cash_refund_receipt"] = "physical_cash_refund_receipt"
    parent_sale_id: UUID
    parent_receipt_number: str = Field(min_length=1, max_length=100)
    receipt_number: str = Field(min_length=1, max_length=100)
    receipt_sequence: Position
    completed_at: AwareDatetime
    is_test: bool = Field(strict=True)
    lines: tuple[PhysicalCashRefundLineV3, ...] = Field(min_length=1, max_length=200)
    payment_method: Literal["cash"] = "cash"
    total_amount: Money
    currency: Literal["TJS"] = "TJS"

    @model_validator(mode="after")
    def balanced(self) -> Self:
        if tuple(line.position for line in self.lines) != tuple(range(1, len(self.lines) + 1)):
            raise ValueError("Refund positions differ")
        if len({line.original_line_id for line in self.lines}) != len(self.lines):
            raise ValueError("Refund source lines must be unique")
        with localcontext(EXACT_CONTEXT):
            if sum((line.net_amount for line in self.lines), Decimal("0.00")) != self.total_amount:
                raise ValueError("Refund receipt total differs")
        if self.total_amount <= 0:
            raise ValueError("Cash refund must be positive")
        return self


class PhysicalCashRefundResultV3(FrozenContract):
    schema_version: Literal[3] = 3
    document_kind: Literal["physical_cash_refund"] = "physical_cash_refund"
    operation_id: UUID
    parent_sale_id: UUID
    sale_id: UUID
    tenant_id: UUID
    branch_id: UUID
    register_id: UUID
    shift_id: UUID
    actor_id: UUID
    payment_id: UUID
    total_amount: Money
    total_cost_amount: Money
    receipt: PhysicalCashRefundReceiptV3

    @model_validator(mode="after")
    def bound_receipt(self) -> Self:
        if (
            self.receipt.parent_sale_id != self.parent_sale_id
            or self.receipt.total_amount != self.total_amount
        ):
            raise ValueError("Refund receipt identity differs")
        if self.payment_id != self.operation_id:
            raise ValueError("Refund payment identity differs")
        return self
