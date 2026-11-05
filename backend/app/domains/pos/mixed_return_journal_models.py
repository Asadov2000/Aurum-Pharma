"""Immutable mixed return history; migration owns scoped keys and guards."""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import BigInteger, Integer, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.domains.inventory.unit_ledger_v2_models import CartAudit


class Base(DeclarativeBase):
    pass


class MixedReturnCommand(CartAudit, Base):
    __tablename__ = "pos_mixed_return_command"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    parent_sale_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    parent_checkout_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    stock_command_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    physical_cost_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    result_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)


class MixedReturnLine(CartAudit, Base):
    __tablename__ = "pos_mixed_return_line"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    command_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    line_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    catalog_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    batch_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    legacy_quantity: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    legacy_offset: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    inventory_kind: Mapped[str] = mapped_column(Text, nullable=False)
    offset: Mapped[int | None] = mapped_column(BigInteger)
    base_units: Mapped[int | None] = mapped_column(BigInteger)
    sale_format: Mapped[str | None] = mapped_column(Text)
    quantity: Mapped[int | None] = mapped_column(BigInteger)
    net_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    gross_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    cost_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))


class MixedReturnSlice(CartAudit, Base):
    __tablename__ = "pos_mixed_return_slice"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    command_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    line_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    source_movement_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    lot_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    source_offset: Mapped[int] = mapped_column(BigInteger, nullable=False)
    base_units: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cost_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
