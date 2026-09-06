"""Pydantic schemas for the inventory domain."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.domains.inventory.expiry import ExpiryStatus

WriteOffReason = Literal["expired", "damaged", "spoiled", "theft", "other"]


WriteOffQuantity = Annotated[
    Decimal,
    Field(gt=0, max_digits=14, decimal_places=3, allow_inf_nan=False),
]


class BatchRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    branch_id: UUID
    catalog_id: UUID
    batch_number: str | None
    manufactured_at: date | None
    expires_at: date
    purchase_price: Decimal | None
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
    branch_name: str
    catalog_name: str
    catalog_form: str | None
    catalog_dosage: str | None
    catalog_pack_size: str | None
    expiry_status: ExpiryStatus
    days_to_expiry: int


class BatchSummary(BaseModel):
    total_qty: Decimal
    purchase_value: Decimal | None
    sale_value: Decimal
    attention_count: int
    expired_count: int
    blocked_count: int


class BatchList(BaseModel):
    items: list[BatchWithExpiry]
    total: int
    page: int
    page_size: int
    summary: BatchSummary


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


class BatchDetails(BatchWithExpiry):
    report_timezone: str
    recent_movements: list[MovementRead]


class WriteOffCreate(BaseModel):
    operation_id: UUID
    qty: WriteOffQuantity
    reason: WriteOffReason
    comment: str | None = Field(default=None, max_length=2000)


class WriteOffRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    batch_id: UUID
    qty: Decimal
    reason: WriteOffReason
    comment: str | None
    amount: Decimal | None
    currency: str
    created_at: datetime
