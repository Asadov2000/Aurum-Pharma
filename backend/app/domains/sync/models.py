"""Persistence models for Cloud-to-Edge shadow synchronization."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Local declarative base for the sync domain."""


class OfflinePolicyInbox(Base):
    """Immutable received envelopes awaiting independent protected acceptance."""

    __tablename__ = "offline_policy_inbox"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "activation_id",
            "edge_node_id",
            "writer_epoch",
            "delivery_sequence",
            name="uq_offline_policy_inbox_sequence",
        ),
        CheckConstraint("delivery_sequence > 0", name="ck_offline_policy_inbox_sequence"),
        CheckConstraint("tenant_access_revision > 0", name="ck_offline_policy_inbox_revision"),
        CheckConstraint("writer_epoch > 0", name="ck_offline_policy_inbox_epoch"),
        CheckConstraint("capability = 'cash_sale_v1'", name="ck_offline_policy_inbox_capability"),
        CheckConstraint(
            "status = 'pending_protected_acceptance'", name="ck_offline_policy_inbox_status"
        ),
        CheckConstraint("claims_hash ~ '^[0-9a-f]{64}$'", name="ck_offline_policy_inbox_hash"),
        CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_offline_policy_inbox_payload"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    activation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    edge_node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    register_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    writer_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    capability: Mapped[str] = mapped_column(Text, nullable=False)
    delivery_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    tenant_access_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    claims_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending_protected_acceptance'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class LocalAuthGuard(Base):
    """Provisioned durable attempt and trusted-clock state, without credentials."""

    __tablename__ = "local_auth_guard"
    __table_args__ = (
        CheckConstraint("credential_revision > 0", name="ck_local_auth_guard_revision"),
        CheckConstraint("context_digest ~ '^[0-9a-f]{64}$'", name="ck_local_auth_guard_digest"),
        CheckConstraint("failed_attempts >= 0", name="ck_local_auth_guard_attempts"),
        CheckConstraint("attempt_sequence >= 0", name="ck_local_auth_guard_sequence"),
        CheckConstraint("last_counter >= 0", name="ck_local_auth_guard_counter"),
        CheckConstraint("length(continuity_id) > 0", name="ck_local_auth_guard_continuity"),
    )

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    edge_node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    credential_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    credential_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    context_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    attempt_sequence: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    blocked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_counter: Mapped[int] = mapped_column(BigInteger, nullable=False)
    continuity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class LocalAuthSession(Base):
    """Trusted non-secret offline sessions; one current session per workplace."""

    __tablename__ = "local_auth_session"
    __table_args__ = (
        Index(
            "uq_local_auth_session_current_workplace",
            "tenant_id",
            "branch_id",
            "edge_node_id",
            "register_id",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    edge_node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    register_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    credential_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


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
    credential_issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    credential_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    shadow_start_origin_node_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    shadow_start_writer_epoch: Mapped[int | None] = mapped_column(BigInteger)
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
    lifecycle_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )


class SyncNodeCredentialRotation(Base):
    __tablename__ = "sync_node_credential_rotation"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    credential_kid: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    credential_hash: Mapped[str] = mapped_column(Text, nullable=False)
    credential_issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    credential_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    activate_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requested_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SyncNodeAdminEvent(Base):
    __tablename__ = "sync_node_admin_event"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    node_version: Mapped[int] = mapped_column(Integer, nullable=False)
    request_hash: Mapped[str | None] = mapped_column(Text)
    reason_code: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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


class SyncQuarantineIncident(Base):
    __tablename__ = "sync_quarantine_incident"

    incident_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    edge_node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    origin_node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    writer_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    cursor_status: Mapped[str] = mapped_column(Text, nullable=False)
    reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    last_applied_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    blocked_sequence: Mapped[int | None] = mapped_column(BigInteger)
    blocked_event_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    blocked_operation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    source_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    projection_checksum: Mapped[str] = mapped_column(Text, nullable=False)
    event_type: Mapped[str | None] = mapped_column(Text)
    schema_version: Mapped[int | None] = mapped_column(Integer)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
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
    sale_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'sale'"))
    parent_sale_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    parent_fully_refunded: Mapped[bool | None] = mapped_column(Boolean)
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


class SyncActivationBootstrap(Base):
    __tablename__ = "sync_activation_bootstrap"

    activation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    edge_node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    register_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    writer_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    capability: Mapped[str] = mapped_column(Text, nullable=False)
    profile: Mapped[str] = mapped_column(Text, nullable=False)
    readiness_eligible: Mapped[bool] = mapped_column(Boolean, nullable=False)
    foundation_hash: Mapped[str] = mapped_column(Text, nullable=False)
    component_manifest_hash: Mapped[str | None] = mapped_column(Text)
    snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False)
    activation_manifest_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class SyncActivationFoundation(Base):
    __tablename__ = "sync_activation_foundation"

    activation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    edge_node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    register_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    writer_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class SyncActivationBootstrapComponent(Base):
    __tablename__ = "sync_activation_bootstrap_component"

    activation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    component: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    edge_node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    register_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    writer_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    item_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    component_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class SyncActivationBootstrapChunk(Base):
    __tablename__ = "sync_activation_bootstrap_chunk"

    activation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    component: Mapped[str] = mapped_column(Text, primary_key=True)
    chunk_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    edge_node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    register_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    writer_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    item_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class SyncReadSnapshot(Base):
    """A content-addressed Cloud read model installed on one Edge node."""

    __tablename__ = "sync_read_snapshot"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'ready')", name="ck_sync_read_snapshot_status"),
        CheckConstraint("schema_version = 1", name="ck_sync_read_snapshot_schema_version"),
        CheckConstraint("source_revision >= 1", name="ck_sync_read_snapshot_source_revision"),
        CheckConstraint(
            "manifest_hash ~ '^[0-9a-f]{64}$'", name="ck_sync_read_snapshot_manifest_hash"
        ),
        CheckConstraint("snapshot_hash ~ '^[0-9a-f]{64}$'", name="ck_sync_read_snapshot_hash"),
        CheckConstraint(
            "signature_algorithm = 'hmac-sha256-edge-read-snapshot-v1'",
            name="ck_sync_read_snapshot_signature_algorithm",
        ),
        CheckConstraint("signature ~ '^[0-9a-f]{64}$'", name="ck_sync_read_snapshot_signature"),
        UniqueConstraint(
            "snapshot_id",
            "tenant_id",
            "branch_id",
            "node_id",
            name="uq_sync_read_snapshot_scope",
        ),
        UniqueConstraint(
            "snapshot_id",
            "tenant_id",
            "branch_id",
            "node_id",
            "source_revision",
            name="uq_sync_read_snapshot_revision_scope",
        ),
        UniqueConstraint(
            "tenant_id",
            "branch_id",
            "node_id",
            "source_revision",
            name="uq_sync_read_snapshot_source_revision",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "branch_id"],
            ["branch.tenant_id", "branch.id"],
            name="fk_sync_read_snapshot_branch_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["node_id", "tenant_id", "branch_id"],
            ["sync_node.id", "sync_node.tenant_id", "sync_node.branch_id"],
            name="fk_sync_read_snapshot_node_scope",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_sync_read_snapshot_scope_created",
            "tenant_id",
            "branch_id",
            "node_id",
            text("created_at DESC"),
        ),
    )

    snapshot_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    credential_kid: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(Text, nullable=False)
    signature_algorithm: Mapped[str] = mapped_column(Text, nullable=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False)
    source_generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class SyncReadSnapshotComponent(Base):
    """Immutable manifest entry within a read snapshot."""

    __tablename__ = "sync_read_snapshot_component"
    __table_args__ = (
        CheckConstraint(
            "component IN ('foundation', 'catalog', 'inventory')",
            name="ck_sync_read_snapshot_component_name",
        ),
        CheckConstraint("schema_version = 1", name="ck_sync_read_snapshot_component_schema"),
        CheckConstraint("source_revision >= 1", name="ck_sync_read_snapshot_component_revision"),
        CheckConstraint("item_count >= 0", name="ck_sync_read_snapshot_component_items"),
        CheckConstraint("chunk_count > 0", name="ck_sync_read_snapshot_component_chunks"),
        CheckConstraint(
            "component_hash ~ '^[0-9a-f]{64}$'",
            name="ck_sync_read_snapshot_component_hash",
        ),
        UniqueConstraint(
            "snapshot_id",
            "tenant_id",
            "branch_id",
            "node_id",
            "source_revision",
            "component",
            "schema_version",
            name="uq_sync_read_snapshot_component_scope",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "tenant_id", "branch_id", "node_id", "source_revision"],
            [
                "sync_read_snapshot.snapshot_id",
                "sync_read_snapshot.tenant_id",
                "sync_read_snapshot.branch_id",
                "sync_read_snapshot.node_id",
                "sync_read_snapshot.source_revision",
            ],
            name="fk_sync_read_snapshot_component_snapshot",
            ondelete="RESTRICT",
        ),
    )

    snapshot_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    component: Mapped[str] = mapped_column(Text, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    source_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    item_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False)
    component_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class SyncReadSnapshotChunk(Base):
    """Immutable chunk payload within a read snapshot component."""

    __tablename__ = "sync_read_snapshot_chunk"
    __table_args__ = (
        CheckConstraint("chunk_index >= 0", name="ck_sync_read_snapshot_chunk_index"),
        CheckConstraint("schema_version = 1", name="ck_sync_read_snapshot_chunk_schema"),
        CheckConstraint("source_revision >= 1", name="ck_sync_read_snapshot_chunk_revision"),
        CheckConstraint("item_count >= 0", name="ck_sync_read_snapshot_chunk_items"),
        CheckConstraint(
            "jsonb_typeof(payload) = 'object' "
            "AND jsonb_typeof(payload -> 'items') = 'array' "
            "AND jsonb_array_length(payload -> 'items') = item_count",
            name="ck_sync_read_snapshot_chunk_payload",
        ),
        CheckConstraint("payload_hash ~ '^[0-9a-f]{64}$'", name="ck_sync_read_snapshot_chunk_hash"),
        ForeignKeyConstraint(
            [
                "snapshot_id",
                "tenant_id",
                "branch_id",
                "node_id",
                "source_revision",
                "component",
                "schema_version",
            ],
            [
                "sync_read_snapshot_component.snapshot_id",
                "sync_read_snapshot_component.tenant_id",
                "sync_read_snapshot_component.branch_id",
                "sync_read_snapshot_component.node_id",
                "sync_read_snapshot_component.source_revision",
                "sync_read_snapshot_component.component",
                "sync_read_snapshot_component.schema_version",
            ],
            name="fk_sync_read_snapshot_chunk_component",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_sync_read_snapshot_chunk_scope",
            "tenant_id",
            "branch_id",
            "node_id",
            "snapshot_id",
        ),
    )

    snapshot_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    component: Mapped[str] = mapped_column(Text, primary_key=True)
    chunk_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    source_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    item_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class SyncReadSnapshotCursor(Base):
    """Mutable pointer to the active complete snapshot for an Edge node."""

    __tablename__ = "sync_read_snapshot_cursor"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_sync_read_snapshot_cursor_version"),
        ForeignKeyConstraint(
            ["active_snapshot_id", "tenant_id", "branch_id", "node_id"],
            [
                "sync_read_snapshot.snapshot_id",
                "sync_read_snapshot.tenant_id",
                "sync_read_snapshot.branch_id",
                "sync_read_snapshot.node_id",
            ],
            name="fk_sync_read_snapshot_cursor_active",
            ondelete="RESTRICT",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    active_snapshot_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    last_confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class SyncReadProjection(Base):
    """Ready marker and foundation payload for an indexed snapshot projection."""

    __tablename__ = "sync_read_projection"
    __table_args__ = (
        CheckConstraint("projection_version = 1", name="ck_sync_read_projection_version"),
        CheckConstraint("status IN ('building', 'ready')", name="ck_sync_read_projection_status"),
        CheckConstraint(
            "catalog_count >= 0 AND supplier_count >= 0 AND inventory_count >= 0",
            name="ck_sync_read_projection_counts",
        ),
        CheckConstraint(
            "foundation_payload ->> 'record_type' = 'foundation' "
            "AND (foundation_payload #>> '{tenant,id}')::uuid = tenant_id "
            "AND (foundation_payload #>> '{branch,id}')::uuid = branch_id",
            name="ck_sync_read_projection_foundation_scope",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "tenant_id", "branch_id", "node_id", "source_revision"],
            [
                "sync_read_snapshot.snapshot_id",
                "sync_read_snapshot.tenant_id",
                "sync_read_snapshot.branch_id",
                "sync_read_snapshot.node_id",
                "sync_read_snapshot.source_revision",
            ],
            name="fk_sync_read_projection_snapshot",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "snapshot_id",
            "tenant_id",
            "branch_id",
            "node_id",
            name="uq_sync_read_projection_scope",
        ),
    )

    snapshot_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    source_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    projection_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'building'"))
    foundation_payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    catalog_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    supplier_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    inventory_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class SyncReadCatalogProjection(Base):
    __tablename__ = "sync_read_catalog_projection"
    __table_args__ = (
        CheckConstraint(
            "payload ->> 'record_type' = 'catalog_product' "
            "AND (payload ->> 'id')::uuid = catalog_id "
            "AND (payload ->> 'tenant_id')::uuid = tenant_id "
            "AND search_text = lower(concat_ws(' ', payload ->> 'brand_name', "
            "payload ->> 'inn', payload ->> 'manufacturer'))",
            name="ck_sync_read_catalog_projection_payload",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "tenant_id", "branch_id", "node_id"],
            [
                "sync_read_projection.snapshot_id",
                "sync_read_projection.tenant_id",
                "sync_read_projection.branch_id",
                "sync_read_projection.node_id",
            ],
            name="fk_sync_read_catalog_projection_header",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_sync_read_catalog_projection_search",
            "search_text",
            postgresql_using="gin",
            postgresql_ops={"search_text": "gin_trgm_ops"},
        ),
    )

    snapshot_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    catalog_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class SyncReadBarcodeProjection(Base):
    __tablename__ = "sync_read_barcode_projection"
    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_id", "catalog_id"],
            [
                "sync_read_catalog_projection.snapshot_id",
                "sync_read_catalog_projection.catalog_id",
            ],
            name="fk_sync_read_barcode_projection_catalog",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_sync_read_barcode_projection_lookup",
            "tenant_id",
            "branch_id",
            "node_id",
            "snapshot_id",
            "code",
            unique=True,
        ),
        Index(
            "ix_sync_read_barcode_projection_search",
            "code",
            postgresql_using="gin",
            postgresql_ops={"code": "gin_trgm_ops"},
        ),
    )

    snapshot_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    barcode_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    catalog_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    code_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class SyncReadSupplierProjection(Base):
    __tablename__ = "sync_read_supplier_projection"
    __table_args__ = (
        CheckConstraint(
            "payload ->> 'record_type' = 'supplier' "
            "AND (payload ->> 'id')::uuid = supplier_id "
            "AND (payload ->> 'tenant_id')::uuid = tenant_id "
            "AND search_text = lower(concat_ws(' ', payload ->> 'name', "
            "payload ->> 'legal_name', payload ->> 'inn_or_tin', "
            "payload ->> 'contact_person'))",
            name="ck_sync_read_supplier_projection_payload",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "tenant_id", "branch_id", "node_id"],
            [
                "sync_read_projection.snapshot_id",
                "sync_read_projection.tenant_id",
                "sync_read_projection.branch_id",
                "sync_read_projection.node_id",
            ],
            name="fk_sync_read_supplier_projection_header",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_sync_read_supplier_projection_search",
            "search_text",
            postgresql_using="gin",
            postgresql_ops={"search_text": "gin_trgm_ops"},
        ),
    )

    snapshot_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    supplier_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    search_text: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class SyncReadInventoryProjection(Base):
    __tablename__ = "sync_read_inventory_projection"
    __table_args__ = (
        CheckConstraint(
            "payload ->> 'record_type' = 'inventory_batch' "
            "AND (payload ->> 'id')::uuid = batch_id "
            "AND (payload ->> 'tenant_id')::uuid = tenant_id "
            "AND (payload ->> 'branch_id')::uuid = branch_id "
            "AND (payload ->> 'catalog_id')::uuid = catalog_id "
            "AND (payload ->> 'expires_at')::date = expires_at "
            "AND (payload ->> 'qty_remaining')::numeric = qty_remaining "
            "AND (payload ->> 'purchase_price')::numeric = purchase_price "
            "AND (payload ->> 'sale_price')::numeric = sale_price "
            "AND (payload ->> 'is_blocked')::boolean = is_blocked",
            name="ck_sync_read_inventory_projection_payload",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "tenant_id", "branch_id", "node_id"],
            [
                "sync_read_projection.snapshot_id",
                "sync_read_projection.tenant_id",
                "sync_read_projection.branch_id",
                "sync_read_projection.node_id",
            ],
            name="fk_sync_read_inventory_projection_header",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_sync_read_inventory_projection_catalog",
            "tenant_id",
            "branch_id",
            "node_id",
            "snapshot_id",
            "catalog_id",
        ),
        Index(
            "ix_sync_read_inventory_projection_expiry",
            "tenant_id",
            "branch_id",
            "node_id",
            "snapshot_id",
            "expires_at",
        ),
        Index(
            "ix_sync_read_inventory_projection_available",
            "tenant_id",
            "branch_id",
            "node_id",
            "snapshot_id",
            "batch_id",
            postgresql_where=text("qty_remaining > 0"),
        ),
    )

    snapshot_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    batch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    catalog_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    expires_at: Mapped[date] = mapped_column(Date, nullable=False)
    qty_remaining: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    purchase_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    sale_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class SyncReadInventoryRollup(Base):
    __tablename__ = "sync_read_inventory_rollup"
    __table_args__ = (
        CheckConstraint("batch_count > 0", name="ck_sync_read_inventory_rollup_batch_count"),
        CheckConstraint("blocked_count >= 0", name="ck_sync_read_inventory_rollup_blocked_count"),
        CheckConstraint(
            "blocked_count <= batch_count",
            name="ck_sync_read_inventory_rollup_blocked_within_total",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "tenant_id", "branch_id", "node_id"],
            [
                "sync_read_projection.snapshot_id",
                "sync_read_projection.tenant_id",
                "sync_read_projection.branch_id",
                "sync_read_projection.node_id",
            ],
            name="fk_sync_read_inventory_rollup_header",
            ondelete="RESTRICT",
        ),
    )

    snapshot_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    has_stock: Mapped[bool] = mapped_column(Boolean, primary_key=True)
    expires_at: Mapped[date] = mapped_column(Date, primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    batch_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3), nullable=False)
    purchase_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    sale_value: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    blocked_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
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
