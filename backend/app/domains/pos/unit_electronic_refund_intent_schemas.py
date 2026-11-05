"""Saved refund reviews are not reservations, terminal commands or payout evidence."""

from typing import Literal
from uuid import UUID

from pydantic import UUID4, AwareDatetime, Field

from app.domains.pos.physical_cash_refund_api_schemas import PhysicalRefundQuoteRequest
from app.domains.pos.unit_contracts import FrozenContract, Money
from app.domains.pos.unit_electronic_refund_preview import UnitElectronicRefundPreview


class ElectronicRefundIntentRequest(FrozenContract):
    parent_sale_id: UUID
    selection: PhysicalRefundQuoteRequest
    expected_total_amount: Money
    expected_revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)


class SaveElectronicRefundIntentRequest(FrozenContract):
    operation_id: UUID4
    request: ElectronicRefundIntentRequest


class SavedElectronicRefundIntent(FrozenContract):
    state: Literal["saved_review"] = "saved_review"
    operation_id: UUID4
    saved_at: AwareDatetime
    request: ElectronicRefundIntentRequest
    preview: UnitElectronicRefundPreview
