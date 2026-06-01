"""SQLAlchemy 2.0 mapped models for the foundation domain."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Local declarative base for the foundation domain."""


class Tenant(Base):
    __tablename__ = "tenant"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    legal_name: Mapped[str | None] = mapped_column(Text)
    inn_or_tin: Mapped[str | None] = mapped_column(Text)
    registration_number: Mapped[str | None] = mapped_column(Text)
    contact_email: Mapped[str] = mapped_column(Text, nullable=False)
    contact_phone: Mapped[str | None] = mapped_column(Text)
    legal_address: Mapped[str | None] = mapped_column(Text)
    logo_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'setup'"))
    setup_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    trial_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    drug_catalog_mode: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'autonomous'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('setup','trial','active','grace_period','readonly','archived')",
            name="ck_tenant_status",
        ),
        CheckConstraint(
            "drug_catalog_mode IN ('connected','autonomous')",
            name="ck_tenant_drug_catalog_mode",
        ),
    )


class TenantSettings(Base):
    __tablename__ = "tenant_settings"

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenant.id", ondelete="CASCADE"),
        primary_key=True,
    )
    expiry_thresholds: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        # jsonb_build_object — see migration 0003 comment for the why.
        server_default=text("jsonb_build_object('yellow',6,'orange',3,'red',1)"),
    )
    expired_sale_mode: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'strict'")
    )
    refund_reason_mode: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'optional'")
    )
    session_admin_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("480")
    )
    session_pos_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("480")
    )
    pin_mode_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    # Minutes a POS draft sale survives idle in the cashier's browser before it
    # is dropped on restore instead of silently reopened. Bounded 5..240.
    draft_sale_lifetime_min: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("30")
    )
    prescription_warning_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    # FK to app_user.id is defined in the database (migration); not declared
    # here because app_user lives in another domain's metadata.
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class Branch(Base):
    __tablename__ = "branch"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenant.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[str | None] = mapped_column(Text)
    branch_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pharmacy'")
    )
    license_number: Mapped[str | None] = mapped_column(Text)
    license_expires_at: Mapped[date | None] = mapped_column(Date)
    working_hours: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    receipt_header: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    # FK to app_user.id is declared in the database (cross-domain — not
    # mirrored in SQLAlchemy metadata).
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    # FK to app_user.id is defined in the database (migration); not declared
    # here because app_user lives in another domain's metadata.
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))

    __table_args__ = (
        CheckConstraint(
            "branch_type IN ('pharmacy','pharmacy_post','kiosk')",
            name="ck_branch_type",
        ),
    )


class Register(Base):
    __tablename__ = "register"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenant.id", ondelete="CASCADE"),
        nullable=False,
    )
    branch_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("branch.id"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    printer_type: Mapped[str | None] = mapped_column(Text)
    printer_config: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    # FK to app_user.id is declared in the database (cross-domain — not
    # mirrored in SQLAlchemy metadata).
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    # FK to app_user.id is defined in the database (migration); not declared
    # here because app_user lives in another domain's metadata.
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))

    __table_args__ = (
        CheckConstraint(
            "printer_type IS NULL OR printer_type IN " "('browser','thermal_58','thermal_80','a4')",
            name="ck_register_printer_type",
        ),
    )
