"""Immutable source identity captured by the database, never by a checkout caller."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RetailCheckoutOrigin(Base):
    __tablename__ = "pos_retail_checkout_origin"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    operation_id: Mapped[UUID] = mapped_column("id", PG_UUID(as_uuid=True), primary_key=True)
    source_kind: Mapped[str] = mapped_column(Text, nullable=False)
    sale_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    register_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    cashier_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    physical_checkout_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    mixed_checkout_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    origin_node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    writer_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)

    source_transaction_id: Mapped[int | None] = mapped_column(BigInteger)
    event_capture_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
