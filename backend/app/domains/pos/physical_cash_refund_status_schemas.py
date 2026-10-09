"""Public recovery documents deliberately contain no acquisition costs."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import UUID4, Field

from app.domains.pos.physical_cash_refund_schemas import PhysicalCashRefundReceiptV3
from app.domains.pos.unit_contracts import FrozenContract, Money


class PhysicalRefundOperationNotFound(FrozenContract):
    state: Literal["not_found"] = "not_found"
    operation_id: UUID4
    parent_sale_id: UUID


class PhysicalRefundOperationCompleted(FrozenContract):
    state: Literal["completed"] = "completed"
    operation_id: UUID4
    parent_sale_id: UUID
    sale_id: UUID
    total_amount: Money
    receipt: PhysicalCashRefundReceiptV3


PhysicalRefundOperationRead = Annotated[
    PhysicalRefundOperationNotFound | PhysicalRefundOperationCompleted,
    Field(discriminator="state"),
]
