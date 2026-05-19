"""Pydantic schemas for the inventory domain."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

WRITE_OFF_REASONS = {"expired", "damaged", "spoiled", "theft", "other"}
EXPIRY_STATUSES = {"expired", "red", "orange", "yellow", "normal"}


class BatchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    branch_id: UUID
    catalog_id: UUID
    batch_number: str | None
    manufactured_at: date | None
    expires_at: date
    purchase_price: Decimal
    sale_price: Decimal
    currency: str
    qty_initial: Decimal
    qty_remaining: Decimal
    is_blocked: bool
    block_reason: str | None
    blocked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class BatchWithExpiry(BatchRead):
    expiry_status: str
    days_to_expiry: int


class BatchList(BaseModel):
    items: list[BatchWithExpiry]
    total: int
    page: int
    page_size: int


class MovementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    batch_id: UUID
    movement_type: str
    qty_delta: Decimal
    source_table: str | None
    source_id: UUID | None
    notes: str | None
    created_at: datetime


class BatchDetails(BatchRead):
    recent_movements: list[MovementRead]


class WriteOffCreate(BaseModel):
    qty: Decimal = Field(gt=0)
    reason: str
    comment: str | None = None

    @field_validator("reason")
    @classmethod
    def _check_reason(cls, v: str) -> str:
        if v not in WRITE_OFF_REASONS:
            raise ValueError(f"reason must be one of {sorted(WRITE_OFF_REASONS)}")
        return v


class WriteOffRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    batch_id: UUID
    qty: Decimal
    reason: str
    comment: str | None
    amount: Decimal
    currency: str
    created_at: datetime
