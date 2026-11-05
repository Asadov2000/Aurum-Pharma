"""A review result is neither a terminal instruction nor proof of a payout."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from app.domains.pos.physical_cash_refund_api_schemas import PhysicalRefundQuoteLine
from app.domains.pos.unit_contracts import FrozenContract, Money


class ElectronicRefundPreviewPayment(FrozenContract):
    original_payment_id: UUID
    method: Literal["cash", "card", "qr"]
    amount: Money
    currency: Literal["TJS"] = "TJS"


class UnitElectronicRefundPreview(FrozenContract):
    state: Literal["preview"] = "preview"
    parent_sale_id: UUID
    branch_id: UUID
    original_receipt_number: str
    revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    total_amount: Money
    lines: tuple[PhysicalRefundQuoteLine, ...]
    payments: tuple[ElectronicRefundPreviewPayment, ...]
    currency: Literal["TJS"] = "TJS"
