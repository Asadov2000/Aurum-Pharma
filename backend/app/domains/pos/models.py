"""SQLAlchemy models for the POS domain."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Local declarative base for the POS domain."""


class Shift(Base):
    __tablename__ = "shift"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    register_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    opened_by_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    closed_by_user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'open'"))
    opening_cash: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    closing_cash_actual: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    closing_cash_expected: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    closing_difference: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    totals: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'TJS'"))
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("status IN ('open','closed','suspended')", name="ck_shift_status"),
    )


class Sale(Base):
    __tablename__ = "sale"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    register_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    shift_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    sale_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'sale'"))
    parent_sale_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    receipt_number: Mapped[str | None] = mapped_column(Text)
    receipt_seq: Mapped[int | None] = mapped_column(BigInteger)
    operation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    operation_hash: Mapped[str | None] = mapped_column(Text)
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'TJS'"))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_by_sale_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    cashier_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fiscal_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    marking_codes: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        CheckConstraint("sale_type IN ('sale','return')", name="ck_sale_type"),
        CheckConstraint("status IN ('draft','completed','voided')", name="ck_sale_status"),
        CheckConstraint("receipt_seq IS NULL OR receipt_seq > 0", name="ck_sale_receipt_seq"),
        CheckConstraint(
            "(operation_id IS NULL) = (operation_hash IS NULL)",
            name="ck_sale_operation_pair",
        ),
        CheckConstraint(
            "operation_hash IS NULL OR operation_hash ~ '^[0-9a-f]{64}$'",
            name="ck_sale_operation_hash",
        ),
    )


class SaleItem(Base):
    __tablename__ = "sale_item"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    sale_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sale.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_sale_item_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sale_item.id", deferrable=True, initially="DEFERRED"),
    )
    catalog_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    batch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    total_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'TJS'"))
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("qty > 0", name="ck_si_qty"),
        CheckConstraint("unit_price >= 0", name="ck_si_unit_price"),
        CheckConstraint("total_price >= 0", name="ck_si_total_price"),
        CheckConstraint(
            "parent_sale_item_id IS NULL OR parent_sale_item_id <> id",
            name="ck_si_parent_not_self",
        ),
    )


class SalePayment(Base):
    __tablename__ = "sale_payment"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    sale_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sale.id", ondelete="CASCADE"),
        nullable=False,
    )
    payment_method: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    operation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    operation_hash: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'TJS'"))
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "payment_method IN ('cash','card','bank_transfer')",
            name="ck_sp_method",
        ),
        CheckConstraint("amount > 0", name="ck_sp_amount"),
        CheckConstraint(
            "(operation_id IS NULL) = (operation_hash IS NULL)",
            name="ck_sp_operation_pair",
        ),
        CheckConstraint(
            "operation_hash IS NULL OR operation_hash ~ '^[0-9a-f]{64}$'",
            name="ck_sp_operation_hash",
        ),
        Index(
            "uq_sale_payment_tenant_operation",
            "tenant_id",
            "operation_id",
            unique=True,
            postgresql_where=text("operation_id IS NOT NULL"),
        ),
    )


class PrescriptionLog(Base):
    __tablename__ = "prescription_log"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    sale_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sale.id", ondelete="CASCADE"),
        nullable=False,
    )
    sale_item_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    prescription_number: Mapped[str | None] = mapped_column(Text)
    doctor_name: Mapped[str | None] = mapped_column(Text)
    doctor_license: Mapped[str | None] = mapped_column(Text)
    patient_name: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
