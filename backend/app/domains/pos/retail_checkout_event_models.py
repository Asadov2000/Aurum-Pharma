"""Protected immutable checkout event evidence and detached read projections."""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, DateTime, LargeBinary, Text, text
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class RetailCheckoutEvent(Base):
    __tablename__ = "pos_retail_checkout_event"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    operation_id: Mapped[UUID] = mapped_column("id", PG_UUID(as_uuid=True), primary_key=True)
    sale_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    event_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    source_kind: Mapped[str] = mapped_column(Text, nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    register_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    cashier_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    origin_node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    writer_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    stream_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    previous_source_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    previous_projection_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    event_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    stream_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    projection_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    record: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    created_by: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)


@dataclass(frozen=True)
class RetailEventCaptureState:
    required: bool
    current_transaction: bool


@dataclass(frozen=True)
class RetailCheckoutEventSnapshot:
    tenant_id: UUID
    sale_id: UUID
    operation_id: UUID
    event_id: UUID
    source_kind: str
    branch_id: UUID
    register_id: UUID
    cashier_id: UUID
    origin_node_id: UUID
    writer_epoch: int
    sequence: int
    previous_source_checksum: str
    previous_projection_checksum: str
    event_fingerprint: str
    record: bytes
