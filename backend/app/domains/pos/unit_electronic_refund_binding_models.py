"""Immutable exclusive preparation and cancellation of electronic refund reviews."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Migration owns constraints and trigger enforcement."""


class BindingFields:
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    parent_sale_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)


class UnitElectronicRefundBinding(BindingFields, Base):
    __tablename__ = "pos_unit_electronic_refund_binding"


class UnitElectronicRefundBindingCancel(BindingFields, Base):
    __tablename__ = "pos_unit_electronic_refund_binding_cancel"
