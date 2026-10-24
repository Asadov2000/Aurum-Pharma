"""Saved refund reviews are not reservations, terminal commands or payout evidence."""

from typing import Literal
from uuid import UUID

from pydantic import UUID4, AwareDatetime, Field

from app.domains.pos.mixed_cash_refund_api_schemas import MixedRefundQuoteRequest
from app.domains.pos.mixed_electronic_refund_preview import MixedElectronicRefundPreview
from app.domains.pos.unit_contracts import FrozenContract, Money


class MixedElectronicRefundIntentRequest(FrozenContract):
    parent_sale_id: UUID
    selection: MixedRefundQuoteRequest
    expected_total_amount: Money
    expected_revision_hash: str = Field(pattern=r"^[0-9a-f]{64}$", strict=True)


class SaveMixedElectronicRefundIntentRequest(FrozenContract):
    operation_id: UUID4
    request: MixedElectronicRefundIntentRequest


class SavedMixedElectronicRefundIntent(FrozenContract):
    state: Literal["saved_review"] = "saved_review"
    operation_id: UUID4
    saved_at: AwareDatetime
    request: MixedElectronicRefundIntentRequest
    preview: MixedElectronicRefundPreview
