"""A review result is neither a terminal instruction nor proof of a payout."""

from typing import Literal
from uuid import UUID

from pydantic import Field

from app.domains.pos.mixed_cash_refund_api_schemas import MixedRefundQuoteLine
from app.domains.pos.unit_contracts import FrozenContract, Money
from app.domains.pos.unit_electronic_refund_preview import ElectronicRefundPreviewPayment


class MixedElectronicRefundPreview(FrozenContract):
    state: Literal["preview"] = "preview"
    parent_sale_id: UUID
    branch_id: UUID
    original_receipt_number: str
    revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    total_amount: Money
    lines: tuple[MixedRefundQuoteLine, ...]
    payments: tuple[ElectronicRefundPreviewPayment, ...]
    currency: Literal["TJS"] = "TJS"
