"""Public mixed refund recovery contains only saved retail receipt facts."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import UUID4, Field

from app.domains.pos.mixed_cash_refund_schemas import MixedCashRefundReceiptV4
from app.domains.pos.unit_contracts import FrozenContract, Money


class MixedRefundOperationNotFound(FrozenContract):
    state: Literal["not_found"] = "not_found"
    operation_id: UUID4
    parent_sale_id: UUID


class MixedRefundOperationCompleted(FrozenContract):
    state: Literal["completed"] = "completed"
    operation_id: UUID4
    parent_sale_id: UUID
    sale_id: UUID
    total_amount: Money
    receipt: MixedCashRefundReceiptV4


MixedRefundOperationRead = Annotated[
    MixedRefundOperationNotFound | MixedRefundOperationCompleted,
    Field(discriminator="state"),
]
