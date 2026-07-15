"""Persistence models for Cloud-to-Edge shadow synchronization."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Numeric, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Local declarative base for the sync domain."""


class SyncNode(Base):
    __tablename__ = "sync_node"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    register_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    node_kind: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    credential_kid: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    credential_hash: Mapped[str | None] = mapped_column(Text)
    credential_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    shadow_start_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    shadow_start_checksum: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("repeat('0', 64)")
    )
    shadow_start_projection_checksum: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("repeat('0', 64)")
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class SyncStream(Base):
    __tablename__ = "sync_stream"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    writer_node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    writer_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    current_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    current_projection_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class SyncOutboxEvent(Base):
    __tablename__ = "sync_outbox"

    event_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    origin_node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    writer_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(Text, nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    stream_checksum: Mapped[str | None] = mapped_column(Text)
    projection_hash: Mapped[str | None] = mapped_column(Text)
    projection_checksum: Mapped[str | None] = mapped_column(Text)
    delivery_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class SyncInboxEvent(Base):
    __tablename__ = "sync_inbox"

    event_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    origin_node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    writer_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(Text, nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    stream_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    projection_hash: Mapped[str] = mapped_column(Text, nullable=False)
    projection_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    reason_code: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class SyncCursor(Base):
    __tablename__ = "sync_cursor"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    origin_node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    writer_epoch: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    start_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    start_source_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    start_projection_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    last_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_event_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    source_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    projection_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class SyncSaleProjection(Base):
    __tablename__ = "sync_sale_projection"

    sale_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    origin_node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    writer_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_event_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    register_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    shift_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    cashier_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    receipt_number: Mapped[str] = mapped_column(Text, nullable=False)
    receipt_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sale_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False)
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False)
    items: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    payments: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    source_payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    projection_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class SyncShadowReport(Base):
    __tablename__ = "sync_shadow_report"

    report_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    edge_node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    origin_node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    writer_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    expected_source_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    source_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    projection_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    expected_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class SyncWriterActivation(Base):
    __tablename__ = "sync_writer_activation"

    activation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    writer_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    writer_node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    allowed_register_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    capability: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    root_source_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    root_projection_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    last_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    current_source_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    current_projection_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    previous_writer_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    previous_terminal_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    previous_terminal_source_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    previous_terminal_projection_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    bootstrap_snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False)
    activation_manifest_hash: Mapped[str] = mapped_column(Text, nullable=False)
    receipt_baseline_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    prepare_request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    prepared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    aborted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class SyncWriterEpoch(Base):
    __tablename__ = "sync_writer_epoch"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    writer_epoch: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    activation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    writer_node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    allowed_register_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    capability: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    root_source_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    root_projection_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    last_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    current_source_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    current_projection_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    previous_writer_epoch: Mapped[int | None] = mapped_column(BigInteger)
    previous_terminal_sequence: Mapped[int | None] = mapped_column(BigInteger)
    previous_terminal_source_checksum: Mapped[str | None] = mapped_column(Text)
    previous_terminal_projection_checksum: Mapped[str | None] = mapped_column(Text)
    bootstrap_snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False)
    activation_manifest_hash: Mapped[str] = mapped_column(Text, nullable=False)
    receipt_baseline_seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    prepared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fenced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class SyncWriterReadiness(Base):
    __tablename__ = "sync_writer_readiness"

    activation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    edge_node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    register_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    writer_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    previous_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    previous_source_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    previous_projection_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    bootstrap_snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False)
    activation_manifest_hash: Mapped[str] = mapped_column(Text, nullable=False)
    receipt_baseline_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    reported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class RegisterReceiptCounter(Base):
    __tablename__ = "register_receipt_counter"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    register_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    writer_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_receipt_seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
