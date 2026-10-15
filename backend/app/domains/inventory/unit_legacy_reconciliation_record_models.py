"""Immutable recorded physical observations, not stock conversion commands."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Numeric, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Scoped foreign keys and immutable guards are enforced by migration 0200."""


class LegacyReconciliationRecord(Base):
    __tablename__ = "unit_legacy_reconciliation_record"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    batch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    catalog_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    legacy_quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    count: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
