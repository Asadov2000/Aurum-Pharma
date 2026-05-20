"""Pydantic schemas for the billing domain."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

BILLING_PERIODS = {"monthly", "yearly"}
PAYMENT_METHODS = {"bank_transfer", "card", "cash"}


class PlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    code: str
    name: str
    description: str | None
    price_per_branch: Decimal
    currency: str
    billing_period: str
    annual_discount_pct: Decimal
    features: dict[str, Any] | None
    is_active: bool


class SubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    plan_id: UUID
    status: str
    billing_period: str
    period_start: datetime
    period_end: datetime
    branches_count: int
    amount: Decimal
    currency: str
    cancelled_at: datetime | None


class SubscriptionWithPlan(SubscriptionRead):
    plan_name: str
    plan_code: str
    plan_features: dict[str, Any] | None


class SubscriptionCreate(BaseModel):
    plan_id: UUID
    billing_period: str = "monthly"
    branches_count: int = Field(ge=1)

    @field_validator("billing_period")
    @classmethod
    def _check_period(cls, v: str) -> str:
        if v not in BILLING_PERIODS:
            raise ValueError(f"billing_period must be one of {sorted(BILLING_PERIODS)}")
        return v


class InvoiceCreate(BaseModel):
    subscription_id: UUID
    amount: Decimal = Field(gt=0)
    due_in_days: int = Field(default=7, ge=0, le=365)
    notes: str | None = None
    discount_amount: Decimal = Field(default=Decimal("0"), ge=0)
    discount_reason: str | None = None


class InvoiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    subscription_id: UUID
    invoice_number: str
    issued_at: datetime
    due_at: datetime
    amount: Decimal
    currency: str
    discount_amount: Decimal
    discount_reason: str | None
    status: str
    paid_at: datetime | None
    notes: str | None


class PaymentCreate(BaseModel):
    amount: Decimal = Field(gt=0)
    paid_at: datetime
    method: str = "bank_transfer"
    reference: str | None = None
    notes: str | None = None

    @field_validator("method")
    @classmethod
    def _check_method(cls, v: str) -> str:
        if v not in PAYMENT_METHODS:
            raise ValueError(f"method must be one of {sorted(PAYMENT_METHODS)}")
        return v


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    invoice_id: UUID
    amount: Decimal
    currency: str
    method: str
    reference: str | None
    paid_at: datetime
    notes: str | None
    created_at: datetime


class InvoiceWithPayments(InvoiceRead):
    payments: list[PaymentRead]
