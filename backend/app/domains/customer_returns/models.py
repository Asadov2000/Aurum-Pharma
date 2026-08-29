"""Append-only customer-return quarantine models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Local declarative base for the customer-return domain."""


class CustomerReturnQuarantineItem(Base):
    __tablename__ = "customer_return_quarantine_item"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    return_sale_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    return_sale_item_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    parent_sale_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    parent_sale_item_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    catalog_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    batch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    refund_reason: Mapped[str | None] = mapped_column(Text)
    refund_comment: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    received_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )

    __table_args__ = (
        CheckConstraint("qty > 0", name="ck_customer_return_quarantine_qty"),
        UniqueConstraint("tenant_id", "id", name="uq_customer_return_quarantine_tenant_id"),
        UniqueConstraint(
            "tenant_id",
            "return_sale_item_id",
            name="uq_customer_return_quarantine_return_item",
        ),
    )


class CustomerReturnDisposition(Base):
    __tablename__ = "customer_return_disposition"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    quarantine_item_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    operation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    resolved_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )

    __table_args__ = (
        CheckConstraint(
            "operation_hash ~ '^[0-9a-f]{64}$'",
            name="ck_customer_return_disposition_hash",
        ),
        CheckConstraint(
            "decision IN ('disposed','supplier_claim','regulatory_transfer')",
            name="ck_customer_return_disposition_decision",
        ),
        CheckConstraint(
            "reason IN ('damaged','quality_issue','wrong_item','expired','other')",
            name="ck_customer_return_disposition_reason",
        ),
        UniqueConstraint(
            "tenant_id", "operation_id", name="uq_customer_return_disposition_operation"
        ),
        UniqueConstraint(
            "tenant_id",
            "quarantine_item_id",
            name="uq_customer_return_disposition_item",
        ),
    )
