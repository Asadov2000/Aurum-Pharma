"""Immutable internal return reservations; quarantined units never re-enter stock."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal
from uuid import UUID

from sqlalchemy import BigInteger, Integer, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domains.catalog.unit_sales import DispenseUnit, SaleFormat
from app.domains.inventory.unit_ledger_models import StockIdentity


class Base(DeclarativeBase):
    """Cross-domain foreign keys and invariants belong to migration 0169."""


class UnitReturnCommand(StockIdentity, Base):
    __tablename__ = "pos_unit_return_command"
    __table_args__ = (UniqueConstraint("tenant_id", "id"),)

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    actor_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    original_line_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[Literal["prepared_quarantine"]] = mapped_column(
        Text, nullable=False, default="prepared_quarantine", server_default="prepared_quarantine"
    )
    version: Mapped[Literal[1]] = mapped_column(Integer, nullable=False)
    unit: Mapped[DispenseUnit] = mapped_column(Text, nullable=False)
    units_per_pack: Mapped[int] = mapped_column(Integer, nullable=False)
    dispense_step: Mapped[int] = mapped_column(Integer, nullable=False)
    sale_format: Mapped[SaleFormat] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    allocation_offset: Mapped[int] = mapped_column(BigInteger, nullable=False)
    base_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_retail_unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    cost_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    money_version: Mapped[Literal[1]] = mapped_column(Integer, nullable=False)
    currency: Mapped[Literal["TJS"]] = mapped_column(Text, nullable=False)


class UnitReturnSlice(StockIdentity, Base):
    __tablename__ = "pos_unit_return_slice"
    __table_args__ = (UniqueConstraint("tenant_id", "command_id", "position"),)

    command_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    original_entry_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    lot_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    pack_ordinal_start: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pack_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    unit_offset: Mapped[int] = mapped_column(Integer, nullable=False)
    base_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_pack_cost: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    cost_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    money_version: Mapped[Literal[1]] = mapped_column(Integer, nullable=False)
    currency: Mapped[Literal["TJS"]] = mapped_column(Text, nullable=False)
