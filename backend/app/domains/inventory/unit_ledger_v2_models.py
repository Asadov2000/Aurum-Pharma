"""Source-bound initialization history for physical packaging version two."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import BigInteger, Date, Integer, Numeric, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domains.inventory.unit_ledger_models import StockIdentity


class Base(DeclarativeBase):
    """Scoped cross-domain foreign keys live in migration 0175."""


class SourceSnapshot:
    source_item_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    packaging: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class UnitStockOperationV2(StockIdentity, SourceSnapshot, Base):
    __tablename__ = "unit_stock_operation_v2"
    __table_args__ = (UniqueConstraint("tenant_id", "id"),)

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    sale_format: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    base_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)


class UnitStockLotV2(StockIdentity, SourceSnapshot, Base):
    __tablename__ = "unit_stock_lot_v2"
    __table_args__ = (UniqueConstraint("tenant_id", "id"),)

    expires_at: Mapped[date] = mapped_column(Date, nullable=False)
    initial_closed_packs: Mapped[int] = mapped_column(BigInteger, nullable=False)
    closed_packs: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    purchase_cost_per_pack: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    cost_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))


class UnitStockEntryV2(StockIdentity, Base):
    __tablename__ = "unit_stock_entry_v2"
    __table_args__ = (UniqueConstraint("tenant_id", "operation_id"),)

    source_item_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    lot_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    closed_delta: Mapped[int] = mapped_column(BigInteger, nullable=False)
    base_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
