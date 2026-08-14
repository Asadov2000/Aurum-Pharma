"""SQLAlchemy models for the billing domain."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Local declarative base for the billing domain."""


class SubscriptionPlan(Base):
    __tablename__ = "subscription_plan"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    price_per_branch: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'TJS'"))
    billing_period: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'monthly'")
    )
    annual_discount_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, server_default=text("0")
    )
    features: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("billing_period IN ('monthly','yearly')", name="ck_sp_billing_period"),
    )


class TenantSubscription(Base):
    __tablename__ = "tenant_subscription"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    plan_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("subscription_plan.id"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'trial'"))
    billing_period: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'monthly'")
    )
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    branches_count: Mapped[int] = mapped_column(Integer, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'TJS'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __table_args__ = (
        CheckConstraint(
            "status IN ('trial','active','grace_period','suspended','cancelled','archived')",
            name="ck_ts_status",
        ),
        CheckConstraint(
            "billing_period IN ('monthly','yearly')",
            name="ck_ts_billing_period",
        ),
    )


class Invoice(Base):
    __tablename__ = "invoice"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    subscription_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    invoice_number: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'TJS'"))
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    discount_reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    pdf_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','paid','overdue','cancelled')",
            name="ck_invoice_status",
        ),
    )


class Payment(Base):
    __tablename__ = "payment"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    invoice_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'TJS'"))
    method: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'bank_transfer'")
    )
    reference: Mapped[str | None] = mapped_column(Text)
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_payment_amount"),
        CheckConstraint("method IN ('bank_transfer','card','cash')", name="ck_payment_method"),
    )


class BillingPlan(Base):
    __tablename__ = "billing_plan"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    code: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'TJS'"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    legacy_subscription_plan_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("subscription_plan.id")
    )
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class BillingPriceVersion(Base):
    __tablename__ = "billing_price_version"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    plan_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("billing_plan.id"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    monthly_price_per_branch: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    annual_discount_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, server_default=text("20.00")
    )
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'TJS'"))
    audience: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'default'"))
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notice_days: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("30"))
    reason: Mapped[str | None] = mapped_column(Text)
    terms_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    approved_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','scheduled','active','archived','cancelled')",
            name="ck_billing_price_status",
        ),
        CheckConstraint("monthly_price_per_branch >= 0", name="ck_billing_price_amount"),
        CheckConstraint("currency = 'TJS'", name="ck_billing_price_currency"),
    )


class BillingPricingAdminEvent(Base):
    __tablename__ = "billing_pricing_admin_event"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, unique=True)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    plan_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    price_version_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    actor_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    actor_session_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    mfa_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approval_terms_hash: Mapped[str | None] = mapped_column(Text)
    previous_price_version_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    result_status: Mapped[str] = mapped_column(Text, nullable=False)
    result_row_version: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    result_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )


class BillingContractOverride(Base):
    __tablename__ = "billing_contract_override"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    plan_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("billing_plan.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    monthly_price_per_branch: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    annual_discount_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, server_default=text("20.00")
    )
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'TJS'"))
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    terms_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    approved_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','scheduled','active','archived','cancelled')",
            name="ck_billing_contract_override_status",
        ),
        CheckConstraint(
            "monthly_price_per_branch >= 0",
            name="ck_billing_contract_override_amount",
        ),
        CheckConstraint("currency = 'TJS'", name="ck_billing_contract_override_currency"),
    )


class BillingSubscriptionPriceApplication(Base):
    __tablename__ = "billing_subscription_price_application"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    subscription_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    plan_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    application_kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    price_version_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    contract_override_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    plan_code: Mapped[str] = mapped_column(Text, nullable=False)
    plan_name: Mapped[str] = mapped_column(Text, nullable=False)
    billing_period: Mapped[str] = mapped_column(Text, nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    calendar_anchor_day: Mapped[int] = mapped_column(Integer, nullable=False)
    timezone: Mapped[str] = mapped_column(Text, nullable=False)
    branches_count: Mapped[int] = mapped_column(Integer, nullable=False)
    monthly_price_per_branch: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    annual_discount_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    calculated_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    terms_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    actor_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    actor_session_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    mfa_verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    result_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
