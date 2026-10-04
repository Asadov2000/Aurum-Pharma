"""Immutable cashier confirmations; these rows are not financial operations."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Numeric, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Migration owns cross-domain foreign keys and guards."""


class UnitCashIntent(Base):
    __tablename__ = "pos_unit_cash_intent"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    sale_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    register_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    shift_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    cashier_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    revision_hash: Mapped[str] = mapped_column(Text, nullable=False)
    expected_total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    cash_tendered: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    change_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, default="TJS")
    quote_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
