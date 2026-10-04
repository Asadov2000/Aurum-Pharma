"""Independent immutable prepared POS lines; these are not completed sales."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import BigInteger, Integer, Numeric, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domains.catalog.unit_sales import DispenseUnit, SaleFormat
from app.domains.inventory.unit_ledger_models import StockIdentity


class Base(DeclarativeBase):
    """Migration owns cross-domain foreign keys."""


class UnitSaleCommand(StockIdentity, Base):
    __tablename__ = "pos_unit_sale_command"
    __table_args__ = (UniqueConstraint("tenant_id", "id"),)

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    sale_format: Mapped[SaleFormat] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)


class UnitSaleLine(StockIdentity, Base):
    __tablename__ = "pos_unit_sale_line"
    __table_args__ = (UniqueConstraint("tenant_id", "command_id"),)

    command_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    stock_operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    unit: Mapped[DispenseUnit] = mapped_column(Text, nullable=False)
    units_per_pack: Mapped[int] = mapped_column(Integer, nullable=False)
    dispense_step: Mapped[int] = mapped_column(Integer, nullable=False)
    sale_format: Mapped[SaleFormat] = mapped_column(Text, nullable=False)
    quantity: Mapped[int] = mapped_column(BigInteger, nullable=False)
    base_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_retail_unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    net_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    original_cost_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    money_version: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
