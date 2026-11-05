"""Recorded operator evidence never means financial posting or permission to retry."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, field_validator

from app.domains.pos.unit_contracts import FrozenContract, Money
from app.domains.pos.unit_electronic_refund_activation_schemas import (
    SavedElectronicRefundActivation,
)


class ElectronicRefundConfirmation(FrozenContract):
    original_payment_id: UUID
    terminal_id: str = Field(min_length=1, max_length=64, strict=True)
    document_number: str = Field(min_length=1, max_length=128, strict=True)

    @field_validator("terminal_id", "document_number", mode="before")
    @classmethod
    def normalize_evidence(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip()
            if any(ord(char) < 32 or ord(char) == 127 for char in value):
                raise ValueError("Terminal evidence must not contain control characters")
        return value


class SavedElectronicRefundConfirmation(ElectronicRefundConfirmation):
    method: Literal["card", "qr"]
    amount: Money
    currency: Literal["TJS"] = "TJS"
    recorded_at: AwareDatetime


class ReconciledElectronicRefund(FrozenContract):
    state: Literal["requires_reconciliation", "confirmed_evidence_only"]
    activation: SavedElectronicRefundActivation
    confirmations: tuple[SavedElectronicRefundConfirmation, ...]
    missing_payment_ids: tuple[UUID, ...]
