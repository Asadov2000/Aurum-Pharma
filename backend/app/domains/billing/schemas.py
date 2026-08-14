"""Pydantic schemas for the billing domain."""

from __future__ import annotations

import re
import unicodedata
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
    model_validator,
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


class PlatformBillingTenantRead(BaseModel):
    tenant_id: UUID
    name: str
    tenant_status: str
    subscription_status: str | None


class PlatformBillingTenantList(BaseModel):
    items: list[PlatformBillingTenantRead]
    total: int
    page: int
    page_size: int


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


class BillingInvoiceIssue(_StrictBillingCommand):
    operation_id: UUID
    expected_row_version: int = Field(ge=1)


class BillingFinancialInvoiceRead(BaseModel):
    invoice_id: UUID
    tenant_id: UUID
    subscription_id: UUID
    price_application_id: UUID
    price_application_kind: Literal["initial", "renewal"]
    invoice_number: str
    document_state: Literal["issued", "void"]
    settlement_state: Literal["unpaid", "partially_paid", "paid", "written_off"]
    collection_state: Literal["not_due", "due", "overdue"]
    period_start: datetime
    period_end: datetime
    due_at: datetime
    total_amount: Decimal
    outstanding_amount: Decimal
    currency: Literal["TJS"]
    issued_at: datetime

    @field_serializer("total_amount", "outstanding_amount")
    def _serialize_money(self, value: Decimal) -> str:
        return format(value, "f")


class BillingInvoiceCommandResult(BaseModel):
    item: BillingFinancialInvoiceRead
    applied: bool


class BankPaymentReviewCreate(_StrictBillingCommand):
    operation_id: UUID
    target_invoice_id: UUID
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    paid_at: datetime
    recipient_account_key: str = Field(pattern=r"^[a-z0-9][a-z0-9_.:-]{2,63}$")
    external_reference: str = Field(min_length=4, max_length=128)

    @field_validator("paid_at")
    @classmethod
    def _normalize_paid_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("paid_at must include a timezone")
        return value.astimezone(UTC)

    @field_validator("external_reference")
    @classmethod
    def _normalize_external_reference(cls, value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value).strip().upper()
        normalized = re.sub(r"[\s\-_/]+", "", normalized)
        if not re.fullmatch(r"[A-Z0-9]{4,128}", normalized):
            raise ValueError("external_reference must normalize to 4-128 Latin letters or digits")
        return normalized


class BankPaymentReviewRead(BaseModel):
    review_id: UUID
    tenant_id: UUID
    target_invoice_id: UUID
    amount: Decimal
    currency: Literal["TJS"]
    paid_at: datetime
    status: Literal["pending_approval", "approved", "rejected", "duplicate"]
    row_version: int
    created_at: datetime
    decided_at: datetime | None = None
    reason_code: str | None = None

    @field_serializer("amount")
    def _serialize_money(self, value: Decimal) -> str:
        return format(value, "f")


class BankPaymentReviewCommandResult(BaseModel):
    item: BankPaymentReviewRead
    applied: bool


class BankPaymentApprovalQueueItem(BaseModel):
    review_id: UUID
    tenant_id: UUID
    tenant_name: str
    target_invoice_id: UUID
    invoice_number: str
    amount: Decimal
    currency: Literal["TJS"]
    paid_at: datetime
    status: Literal["pending_approval"]
    row_version: int
    created_at: datetime
    is_own_review: bool

    @field_serializer("amount")
    def _serialize_money(self, value: Decimal) -> str:
        return format(value, "f")


class BankPaymentApprovalQueue(BaseModel):
    items: list[BankPaymentApprovalQueueItem]
    total: int
    page: int
    page_size: int


class BankPaymentApprove(_StrictBillingCommand):
    operation_id: UUID
    expected_row_version: int = Field(ge=1)


class BankPaymentReviewReject(BankPaymentApprove):
    reason_code: Literal[
        "bank_payment_not_found",
        "amount_mismatch",
        "date_mismatch",
        "duplicate",
        "wrong_tenant_or_invoice",
        "other",
    ]
    reason_note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _validate_reason_note(self) -> BankPaymentReviewReject:
        self.reason_note = self.reason_note.strip() if self.reason_note else None
        if self.reason_code == "other" and (self.reason_note is None or len(self.reason_note) < 10):
            raise ValueError("reason_note must contain at least 10 characters for other")
        return self


class BillingPaymentAllocationRead(BaseModel):
    invoice_id: UUID
    invoice_number: str
    amount: Decimal
    allocation_order: int

    @field_serializer("amount")
    def _serialize_money(self, value: Decimal) -> str:
        return format(value, "f")


class BillingPaymentApprovalRead(BaseModel):
    review_id: UUID
    payment_id: UUID
    tenant_id: UUID
    target_invoice_id: UUID
    amount: Decimal
    currency: Literal["TJS"]
    paid_at: datetime
    confirmed_at: datetime
    lifecycle_state: Literal["confirmed", "reversed"]
    allocated_amount: Decimal
    credit_amount: Decimal
    target_outstanding_amount: Decimal
    blocking_outstanding_amount: Decimal
    allocations: list[BillingPaymentAllocationRead]
    access_restored: bool
    subscription_status: str
    subscription_period_start: datetime
    subscription_period_end: datetime

    @field_serializer(
        "amount",
        "allocated_amount",
        "credit_amount",
        "target_outstanding_amount",
        "blocking_outstanding_amount",
    )
    def _serialize_money(self, value: Decimal) -> str:
        return format(value, "f")


class BillingPaymentApprovalCommandResult(BaseModel):
    item: BillingPaymentApprovalRead
    applied: bool


class BillingPaymentHistoryRead(BaseModel):
    payment_id: UUID
    amount: Decimal
    allocated_amount: Decimal
    credit_amount: Decimal
    corrected_amount: Decimal
    refunded_amount: Decimal
    reversible_amount: Decimal
    adjustment_pending: bool
    currency: Literal["TJS"]
    paid_at: datetime
    confirmed_at: datetime
    lifecycle_state: Literal["confirmed", "reversed"]

    @field_serializer(
        "amount",
        "allocated_amount",
        "credit_amount",
        "corrected_amount",
        "refunded_amount",
        "reversible_amount",
    )
    def _serialize_money(self, value: Decimal) -> str:
        return format(value, "f")


class BillingPaymentAdjustmentCreate(_StrictBillingCommand):
    operation_id: UUID
    adjustment_kind: Literal["correction", "bank_refund"]
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    reason_code: Literal[
        "payment_entered_in_error",
        "amount_correction",
        "bank_refund_completed",
        "contract_resolution",
        "other",
    ]
    reason_note: str = Field(min_length=10, max_length=500)
    refunded_at: datetime | None = None
    refund_reference: str | None = Field(default=None, max_length=128)

    @field_validator("reason_note")
    @classmethod
    def _normalize_reason_note(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 10:
            raise ValueError("reason_note must contain at least 10 characters")
        return normalized

    @field_validator("refunded_at")
    @classmethod
    def _normalize_refunded_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("refunded_at must include a timezone")
        return value.astimezone(UTC)

    @field_validator("refund_reference")
    @classmethod
    def _normalize_refund_reference(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = unicodedata.normalize("NFKC", value).strip().upper()
        normalized = re.sub(r"[\s\-_/]+", "", normalized)
        if not re.fullmatch(r"[A-Z0-9]{4,128}", normalized):
            raise ValueError("refund_reference must normalize to 4-128 Latin letters or digits")
        return normalized

    @model_validator(mode="after")
    def _validate_adjustment_kind(self) -> BillingPaymentAdjustmentCreate:
        correction_reasons = {"payment_entered_in_error", "amount_correction", "other"}
        refund_reasons = {"bank_refund_completed", "contract_resolution", "other"}
        if self.adjustment_kind == "correction":
            if self.reason_code not in correction_reasons:
                raise ValueError("reason_code is not valid for a correction")
            if self.refunded_at is not None or self.refund_reference is not None:
                raise ValueError("correction cannot contain bank refund details")
        else:
            if self.reason_code not in refund_reasons:
                raise ValueError("reason_code is not valid for a bank refund")
            if self.refunded_at is None or self.refund_reference is None:
                raise ValueError("bank refund timestamp and reference are required")
        return self


class BillingPaymentAdjustmentRequestRead(BaseModel):
    adjustment_id: UUID
    tenant_id: UUID
    payment_id: UUID
    adjustment_kind: Literal["correction", "bank_refund"]
    amount: Decimal
    currency: Literal["TJS"]
    reason_code: str
    reason_note: str
    refunded_at: datetime | None
    status: Literal["pending_approval"]
    row_version: int
    created_at: datetime

    @field_serializer("amount")
    def _serialize_money(self, value: Decimal) -> str:
        return format(value, "f")


class BillingPaymentAdjustmentRequestCommandResult(BaseModel):
    item: BillingPaymentAdjustmentRequestRead
    applied: bool


class BillingPaymentAdjustmentQueueItem(BillingPaymentAdjustmentRequestRead):
    tenant_name: str
    payment_amount: Decimal
    payment_paid_at: datetime
    is_own_request: bool

    @field_serializer("payment_amount")
    def _serialize_payment_amount(self, value: Decimal) -> str:
        return format(value, "f")


class BillingPaymentAdjustmentQueue(BaseModel):
    items: list[BillingPaymentAdjustmentQueueItem]
    total: int
    page: int
    page_size: int


class BillingPaymentAdjustmentApprove(_StrictBillingCommand):
    operation_id: UUID
    expected_row_version: int = Field(ge=1)


class BillingPaymentAdjustmentReject(BillingPaymentAdjustmentApprove):
    reason_code: Literal[
        "bank_refund_not_verified",
        "amount_mismatch",
        "request_not_supported",
        "duplicate",
        "other",
    ]
    reason_note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _validate_reason_note(self) -> BillingPaymentAdjustmentReject:
        self.reason_note = self.reason_note.strip() if self.reason_note else None
        if self.reason_code == "other" and (self.reason_note is None or len(self.reason_note) < 10):
            raise ValueError("reason_note must contain at least 10 characters for other")
        return self


class BillingPaymentAdjustmentApprovalRead(BaseModel):
    adjustment_id: UUID
    adjustment_record_id: UUID
    tenant_id: UUID
    payment_id: UUID
    adjustment_kind: Literal["correction", "bank_refund"]
    amount: Decimal
    credit_reversed_amount: Decimal
    allocation_reversed_amount: Decimal
    total_adjusted_amount: Decimal
    reversible_amount: Decimal
    blocking_outstanding_amount: Decimal
    access_review_required: bool
    currency: Literal["TJS"]
    status: Literal["approved"]
    approved_at: datetime

    @field_serializer(
        "amount",
        "credit_reversed_amount",
        "allocation_reversed_amount",
        "total_adjusted_amount",
        "reversible_amount",
        "blocking_outstanding_amount",
    )
    def _serialize_money(self, value: Decimal) -> str:
        return format(value, "f")


class BillingPaymentAdjustmentApprovalCommandResult(BaseModel):
    item: BillingPaymentAdjustmentApprovalRead
    applied: bool


class BillingPaymentAdjustmentRejectionRead(BaseModel):
    adjustment_id: UUID
    tenant_id: UUID
    payment_id: UUID
    adjustment_kind: Literal["correction", "bank_refund"]
    amount: Decimal
    currency: Literal["TJS"]
    status: Literal["rejected"]
    row_version: int
    created_at: datetime
    decided_at: datetime
    decision_reason_code: str

    @field_serializer("amount")
    def _serialize_money(self, value: Decimal) -> str:
        return format(value, "f")


class BillingPaymentAdjustmentRejectionCommandResult(BaseModel):
    item: BillingPaymentAdjustmentRejectionRead
    applied: bool


class BillingFinancialAccountRead(BaseModel):
    tenant_id: UUID
    currency: Literal["TJS"]
    outstanding_amount: Decimal
    credit_balance: Decimal
    invoices: list[BillingFinancialInvoiceRead]
    payments: list[BillingPaymentHistoryRead]
    journal_balanced: bool

    @field_serializer("outstanding_amount", "credit_balance")
    def _serialize_money(self, value: Decimal) -> str:
        return format(value, "f")
