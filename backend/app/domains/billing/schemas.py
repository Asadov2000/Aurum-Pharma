"""Pydantic schemas for the billing domain."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationInfo,
    field_serializer,
    field_validator,
)

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
    row_version: int


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


class PlatformBillingOverviewRead(BaseModel):
    generated_at: datetime
    tenants_total: int
    active_subscriptions: int
    attention_subscriptions: int
    open_invoices: int
    overdue_invoices: int
    outstanding_amount: Decimal
    currency: str = "TJS"


class PlatformInvoiceRead(BaseModel):
    tenant_name: str
    invoice_number: str
    issued_at: datetime
    due_at: datetime
    amount: Decimal
    paid_amount: Decimal
    outstanding_amount: Decimal
    currency: str
    status: str
    subscription_status: str


class PlatformInvoiceList(BaseModel):
    items: list[PlatformInvoiceRead]
    total: int
    page: int
    page_size: int


class _StrictBillingCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PricingPlanCreate(_StrictBillingCommand):
    operation_id: UUID
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{2,63}$")
    name: str = Field(min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=2000)


class PricingPriceDraftCreate(_StrictBillingCommand):
    operation_id: UUID
    monthly_price_per_branch: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    annual_discount_pct: Decimal = Field(
        default=Decimal("20.00"),
        ge=0,
        lt=100,
        max_digits=5,
        decimal_places=2,
    )
    audience: Literal["default", "new_customers"] = "default"
    notice_days: int = Field(default=30, ge=0, le=365)
    change_reason: str = Field(min_length=10, max_length=1000)
    terms_snapshot: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("notice_days")
    @classmethod
    def _validate_default_notice(cls, value: int, info: ValidationInfo) -> int:
        if info.data.get("audience", "default") == "default" and value < 30:
            raise ValueError("default audience requires at least 30 days notice")
        return value


class PricingSchedule(_StrictBillingCommand):
    operation_id: UUID
    expected_row_version: int = Field(ge=1)
    effective_from: datetime

    @field_validator("effective_from")
    @classmethod
    def _normalize_effective_from(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("effective_from must include a timezone")
        return value.astimezone(UTC)


class PricingActivate(_StrictBillingCommand):
    operation_id: UUID
    expected_row_version: int = Field(ge=1)


class PricingCancel(_StrictBillingCommand):
    operation_id: UUID
    expected_row_version: int = Field(ge=1)
    reason_code: Literal[
        "pricing_error",
        "commercial_change",
        "legal_requirement",
        "security_incident",
        "other",
    ]
    reason: str = Field(min_length=10, max_length=500)


class PlatformPricingVersionRead(BaseModel):
    price_version_id: UUID
    plan_id: UUID | None = None
    version_number: int
    status: Literal["draft", "scheduled", "active", "archived", "cancelled"]
    monthly_price_per_branch: Decimal
    annual_discount_pct: Decimal
    currency: Literal["TJS"]
    audience: Literal["default", "new_customers"]
    effective_from: datetime | None
    notice_days: int
    change_reason: str | None
    created_by: UUID
    approved_by: UUID | None
    approved_at: datetime | None
    activated_at: datetime | None
    archived_at: datetime | None
    row_version: int
    created_at: datetime

    @field_serializer("monthly_price_per_branch", "annual_discount_pct")
    def _serialize_decimal(self, value: Decimal) -> str:
        return format(value, "f")


class PlatformPricingPlanRead(BaseModel):
    plan_id: UUID
    code: str
    name: str
    description: str | None
    currency: Literal["TJS"]
    is_active: bool
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    versions: list[PlatformPricingVersionRead]


class PlatformPricingPlanList(BaseModel):
    items: list[PlatformPricingPlanRead]
    total: int
    page: int
    page_size: int


class PlatformPricingPlanCommandResult(BaseModel):
    item: PlatformPricingPlanRead
    applied: bool


class PlatformPricingVersionCommandResult(BaseModel):
    item: PlatformPricingVersionRead
    applied: bool


class SubscriptionPriceApplicationCreate(_StrictBillingCommand):
    operation_id: UUID
    expected_row_version: int = Field(ge=1)


class SubscriptionPriceApplicationRead(BaseModel):
    application_id: UUID
    subscription_id: UUID
    application_kind: Literal["initial", "renewal"]
    source_type: Literal["price_version", "contract_override"]
    plan_code: str
    plan_name: str
    billing_period: Literal["monthly", "yearly"]
    period_start: datetime
    period_end: datetime
    timezone: Literal["Asia/Dushanbe"]
    branches_count: int
    monthly_price_per_branch: Decimal
    annual_discount_pct: Decimal
    calculated_amount: Decimal
    currency: Literal["TJS"]
    created_at: datetime

    @field_serializer(
        "monthly_price_per_branch",
        "annual_discount_pct",
        "calculated_amount",
    )
    def _serialize_money(self, value: Decimal) -> str:
        return format(value, "f")


class SubscriptionPriceApplicationCommandResult(BaseModel):
    item: SubscriptionPriceApplicationRead
    applied: bool
