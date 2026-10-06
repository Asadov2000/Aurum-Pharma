"""Source-bound initialization history for physical packaging version two."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    FetchedValue,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
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


class CartAudit:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class UnitStockCartCommandV2(CartAudit, Base):
    __tablename__ = "unit_stock_cart_command_v2"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    retail_version: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(Text)


class UnitStockCartLineV2(CartAudit, Base):
    __tablename__ = "unit_stock_cart_line_v2"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    command_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    line_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    catalog_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    packaging: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    sale_format: Mapped[str] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    base_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_retail_unit_price: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), server_default=FetchedValue()
    )
    gross_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), server_default=FetchedValue()
    )
    discount_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), server_default=FetchedValue()
    )
    net_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), server_default=FetchedValue()
    )


class UnitStockOpenedPackV2(StockIdentity, Base):
    __tablename__ = "unit_stock_opened_pack_v2"
    __table_args__ = (UniqueConstraint("tenant_id", "id"),)

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    lot_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    pack_ordinal: Mapped[int] = mapped_column(BigInteger, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    remaining_units: Mapped[int] = mapped_column(BigInteger, nullable=False)


class UnitStockMovementV2(StockIdentity, Base):
    __tablename__ = "unit_stock_movement_v2"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    command_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    line_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    lot_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    pack_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    entry_order: Mapped[int] = mapped_column(Integer, nullable=False)
    closed_delta: Mapped[int] = mapped_column(BigInteger, nullable=False)
    units_taken: Mapped[int] = mapped_column(BigInteger, nullable=False)
    before_state: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))
    after_state: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))
    sealed_start: Mapped[int | None] = mapped_column(BigInteger)
    sealed_count: Mapped[int | None] = mapped_column(BigInteger)
    cost_pack_ordinal_start: Mapped[int | None] = mapped_column(
        BigInteger, server_default=FetchedValue()
    )
    cost_pack_count: Mapped[int | None] = mapped_column(BigInteger, server_default=FetchedValue())
    cost_unit_offset: Mapped[int | None] = mapped_column(BigInteger, server_default=FetchedValue())
    cost_base_units: Mapped[int | None] = mapped_column(BigInteger, server_default=FetchedValue())
    cost_original_pack_cost: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), server_default=FetchedValue()
    )
    cost_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(14, 2), server_default=FetchedValue()
    )
    cost_version: Mapped[int | None] = mapped_column(Integer, server_default=FetchedValue())
