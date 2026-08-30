"""SQLAlchemy models for the incoming domain."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Local declarative base for the incoming domain."""


class IncomingDocument(Base):
    __tablename__ = "incoming_document"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    # The composite tenant/supplier FK is declared in migration 0126. Supplier
    # uses another domain's SQLAlchemy metadata, so it is not mirrored here.
    supplier_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    document_number: Mapped[str | None] = mapped_column(Text)
    document_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'TJS'"))
    notes: Mapped[str | None] = mapped_column(Text)
    document_file_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    accepted_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','accepted','rejected')",
            name="ck_id_status",
        ),
    )


class IncomingItem(Base):
    __tablename__ = "incoming_item"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    document_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("incoming_document.id", ondelete="CASCADE"),
        nullable=False,
    )
    catalog_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    batch_number: Mapped[str | None] = mapped_column(Text)
    manufactured_at: Mapped[date | None] = mapped_column(Date)
    expires_at: Mapped[date] = mapped_column(Date, nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    purchase_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    sale_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'TJS'"))
    created_batch_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("qty > 0", name="ck_ii_qty"),
        CheckConstraint("purchase_price >= 0", name="ck_ii_purchase_price"),
        CheckConstraint("sale_price >= 0", name="ck_ii_sale_price"),
        CheckConstraint(
            "manufactured_at IS NULL OR manufactured_at <= expires_at",
            name="ck_ii_manufactured_before_expiry",
        ),
    )
