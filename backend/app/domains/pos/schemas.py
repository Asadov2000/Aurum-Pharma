"""Pydantic schemas for the POS domain."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

PAYMENT_METHODS = {"cash", "card", "bank_transfer"}


# ---- shift ----


class ShiftOpenRequest(BaseModel):
    register_id: UUID
    opening_cash: Decimal = Field(default=Decimal("0"), ge=0)


class ShiftCloseRequest(BaseModel):
    closing_cash_actual: Decimal = Field(ge=0)
    notes: str | None = None


class ShiftRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    branch_id: UUID
    register_id: UUID
    opened_by_user_id: UUID
    closed_by_user_id: UUID | None
    opened_at: datetime
    closed_at: datetime | None
    status: str
    opening_cash: Decimal
    closing_cash_actual: Decimal | None
    closing_cash_expected: Decimal | None
    closing_difference: Decimal | None
    totals: dict[str, Any] | None
    currency: str
    notes: str | None


class ZReport(BaseModel):
    shift_id: UUID
    opened_at: datetime
    closed_at: datetime | None
    register_id: UUID
    cashier_user_id: UUID
    opening_cash: Decimal
    closing_cash_actual: Decimal | None
    closing_cash_expected: Decimal | None
    closing_difference: Decimal | None
    totals: dict[str, Any]
    sales_count: int
    returns_count: int


# ---- sale ----


class SaleCreate(BaseModel):
    register_id: UUID


class SaleItemAdd(BaseModel):
    catalog_id: UUID
    qty: Decimal = Field(gt=0)


class SaleItemPatch(BaseModel):
    qty: Decimal = Field(gt=0)


class PaymentAdd(BaseModel):
    payment_method: str
    amount: Decimal = Field(gt=0)
    metadata: dict[str, Any] | None = None

    @field_validator("payment_method")
    @classmethod
    def _check_method(cls, v: str) -> str:
        if v not in PAYMENT_METHODS:
            raise ValueError(f"payment_method must be one of {sorted(PAYMENT_METHODS)}")
        return v


class SaleItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sale_id: UUID
    catalog_id: UUID
    batch_id: UUID
    qty: Decimal
    unit_price: Decimal
    total_price: Decimal
    currency: str
    discount_amount: Decimal
    position: int


class SaleItemAdded(BaseModel):
    """The /items endpoint may have split one request into several items
    when FEFO drew from multiple batches. requires_prescription_log is
    true if any item carries a 'prescription' dispensing_type."""

    items: list[SaleItemRead]
    requires_prescription_log: bool


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sale_id: UUID
    payment_method: str
    amount: Decimal
    currency: str


class SaleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    branch_id: UUID
    register_id: UUID
    shift_id: UUID
    sale_type: str
    parent_sale_id: UUID | None
    status: str
    receipt_number: str | None
    is_test: bool
    total_amount: Decimal
    currency: str
    voided_at: datetime | None
    voided_by_sale_id: UUID | None
    cashier_user_id: UUID
    created_at: datetime
    completed_at: datetime | None


class SaleDetails(SaleRead):
    items: list[SaleItemRead]
    payments: list[PaymentRead]


class PrescriptionLogCreate(BaseModel):
    sale_item_id: UUID | None = None
    prescription_number: str | None = None
    doctor_name: str | None = None
    doctor_license: str | None = None
    patient_name: str | None = None
    notes: str | None = None


class PrescriptionLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sale_id: UUID
    sale_item_id: UUID | None
    prescription_number: str | None
    doctor_name: str | None
    doctor_license: str | None
    patient_name: str | None
    notes: str | None
    created_at: datetime


class RefundItem(BaseModel):
    sale_item_id: UUID
    qty: Decimal = Field(gt=0)


class RefundCreate(BaseModel):
    items: list[RefundItem] = Field(min_length=1)
    reason: str | None = None
    comment: str | None = None
