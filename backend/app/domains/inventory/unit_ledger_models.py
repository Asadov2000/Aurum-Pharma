"""Independent prepared stock: immutable commands and entries, derived balances."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domains.catalog.unit_sales import DispenseUnit, SaleFormat


class Base(DeclarativeBase):
    """Local metadata; cross-domain foreign keys are enforced by the migration."""


class StockIdentity:
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    catalog_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class PackagingSnapshot:
    unit: Mapped[DispenseUnit] = mapped_column(Text, nullable=False)
    units_per_pack: Mapped[int] = mapped_column(Integer, nullable=False)
    dispense_step: Mapped[int] = mapped_column(Integer, nullable=False)


class UnitStockOperation(StockIdentity, PackagingSnapshot, Base):
    __tablename__ = "unit_stock_operation"
    __table_args__ = (UniqueConstraint("tenant_id", "id"),)

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)

    kind: Mapped[str] = mapped_column(Text, nullable=False)
    sale_format: Mapped[SaleFormat] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    base_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    stock_source_kind: Mapped[Literal["prepared", "received"]] = mapped_column(
        Text, nullable=False, server_default="prepared", default="prepared"
    )


class UnitStockLot(StockIdentity, PackagingSnapshot, Base):
    __tablename__ = "unit_stock_lot"
    __table_args__ = (UniqueConstraint("tenant_id", "id"),)

    state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'prepared'"))
    version: Mapped[Literal[1]] = mapped_column(Integer, nullable=False, server_default=text("1"))
    expires_at: Mapped[date] = mapped_column(Date, nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    closed_packs: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))

    initial_closed_packs: Mapped[int | None] = mapped_column(BigInteger)
    purchase_cost_per_pack: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    cost_version: Mapped[int | None] = mapped_column(Integer)


class UnitStockOpenedPack(StockIdentity, Base):
    __tablename__ = "unit_stock_opened_pack"
    __table_args__ = (UniqueConstraint("tenant_id", "id", "lot_id"),)

    lot_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    pack_ordinal: Mapped[int | None] = mapped_column(BigInteger)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    remaining_units: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )


class UnitStockEntry(StockIdentity, Base):
    __tablename__ = "unit_stock_entry"
    __table_args__ = (UniqueConstraint("tenant_id", "operation_id", "entry_order"),)

    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    lot_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    pack_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    entry_order: Mapped[int] = mapped_column(Integer, nullable=False)
    closed_delta: Mapped[int] = mapped_column(BigInteger, nullable=False)
    opened_delta: Mapped[int] = mapped_column(BigInteger, nullable=False)
    units_delta: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pack_remaining_after: Mapped[int] = mapped_column(BigInteger, nullable=False)

    cost_pack_ordinal_start: Mapped[int | None] = mapped_column(BigInteger)
    cost_pack_count: Mapped[int | None] = mapped_column(BigInteger)
    cost_unit_offset: Mapped[int | None] = mapped_column(BigInteger)
    cost_base_units: Mapped[int | None] = mapped_column(BigInteger)
    cost_original_pack_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    cost_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    cost_version: Mapped[int | None] = mapped_column(Integer)
