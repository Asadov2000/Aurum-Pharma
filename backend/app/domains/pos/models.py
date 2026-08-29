"""SQLAlchemy models for the POS domain."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, OID
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Local declarative base for the POS domain."""


class Shift(Base):
    __tablename__ = "shift"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    register_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    opened_by_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    closed_by_user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'open'"))
    opening_cash: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    closing_cash_actual: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    closing_cash_expected: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    closing_difference: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    totals: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'TJS'"))
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("status IN ('open','closed','suspended')", name="ck_shift_status"),
    )


class POSFavorite(Base):
    __tablename__ = "pos_favorite"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    catalog_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "user_id",
            "catalog_id",
            name="uq_pos_favorite_tenant_user_catalog",
        ),
    )


class POSCommand(Base):
    __tablename__ = "pos_command"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    actor_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    sale_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    command_type: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    result_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "operation_id",
            name="uq_pos_command_tenant_operation",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_pos_command_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "sale_id"],
            ["sale.tenant_id", "sale.id"],
            name="fk_pos_command_tenant_sale",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "command_type IN ('sale.create','item.add','item.update','item.delete')",
            name="ck_pos_command_type",
        ),
        CheckConstraint(
            "command_type = 'sale.create' OR sale_id IS NOT NULL",
            name="ck_pos_command_sale_reference",
        ),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_pos_command_request_hash",
        ),
        Index(
            "ix_pos_command_actor_created",
            "tenant_id",
            "actor_user_id",
            "created_at",
        ),
        Index(
            "ix_pos_command_sale_created",
            "tenant_id",
            "sale_id",
            "created_at",
            postgresql_where=text("sale_id IS NOT NULL"),
        ),
    )


class EdgeCashNodeIdentity(Base):
    __tablename__ = "edge_cash_node_identity"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    edge_node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    register_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    database_role: Mapped[str] = mapped_column(Text, nullable=False)
    database_role_oid: Mapped[int] = mapped_column(OID, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))

    # Cross-domain Sync/Foundations FKs stay migration-only because each domain
    # owns an independent SQLAlchemy metadata registry.
    __table_args__ = (
        UniqueConstraint("database_role", name="uq_edge_cash_node_identity_role"),
        UniqueConstraint("database_role_oid", name="uq_edge_cash_node_identity_role_oid"),
        UniqueConstraint("edge_node_id", name="uq_edge_cash_node_identity_node"),
        UniqueConstraint(
            "id",
            "tenant_id",
            "branch_id",
            "edge_node_id",
            "register_id",
            name="uq_edge_cash_node_identity_scope",
        ),
        CheckConstraint(
            "database_role ~ '^aurum_edge_node_[0-9a-f]{32}$' "
            "AND octet_length(database_role) <= 63",
            name="ck_edge_cash_node_identity_role",
        ),
        CheckConstraint(
            "database_role_oid <> 0",
            name="ck_edge_cash_node_identity_role_oid",
        ),
    )


class EdgeCashCommand(Base):
    __tablename__ = "edge_cash_command"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    edge_identity_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    activation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    edge_node_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    writer_epoch: Mapped[int] = mapped_column(BigInteger, nullable=False)
    register_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    cashier_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    sale_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    sale_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'completed'")
    )
    receipt_number: Mapped[str] = mapped_column(Text, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'TJS'"))
    command_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'sale.cash.complete'")
    )
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    result_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    result_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))

    # Activation, epoch, node, and register FKs are migration-only for the same
    # independent-metadata reason documented on EdgeCashNodeIdentity.
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "operation_id",
            name="uq_edge_cash_command_tenant_operation",
        ),
        UniqueConstraint("tenant_id", "sale_id", name="uq_edge_cash_command_tenant_sale"),
        UniqueConstraint("tenant_id", "id", name="uq_edge_cash_command_tenant_id"),
        ForeignKeyConstraint(
            [
                "edge_identity_id",
                "tenant_id",
                "branch_id",
                "edge_node_id",
                "register_id",
            ],
            [
                "edge_cash_node_identity.id",
                "edge_cash_node_identity.tenant_id",
                "edge_cash_node_identity.branch_id",
                "edge_cash_node_identity.edge_node_id",
                "edge_cash_node_identity.register_id",
            ],
            name="fk_edge_cash_command_identity_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "sale_id",
                "tenant_id",
                "branch_id",
                "register_id",
                "cashier_user_id",
                "operation_id",
                "sale_status",
                "receipt_number",
                "total_amount",
                "currency",
            ],
            [
                "sale.id",
                "sale.tenant_id",
                "sale.branch_id",
                "sale.register_id",
                "sale.cashier_user_id",
                "sale.operation_id",
                "sale.status",
                "sale.receipt_number",
                "sale.total_amount",
                "sale.currency",
            ],
            name="fk_edge_cash_command_sale_scope",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "command_type = 'sale.cash.complete'",
            name="ck_edge_cash_command_type",
        ),
        CheckConstraint(
            "sale_status = 'completed'",
            name="ck_edge_cash_command_sale_status",
        ),
        CheckConstraint(
            "btrim(receipt_number) <> ''",
            name="ck_edge_cash_command_receipt_number",
        ),
        CheckConstraint(
            "total_amount >= 0",
            name="ck_edge_cash_command_total_amount",
        ),
        CheckConstraint(
            "currency = 'TJS'",
            name="ck_edge_cash_command_currency",
        ),
        CheckConstraint(
            "(get_byte(uuid_send(operation_id), 6) >> 4) = 4 "
            "AND (get_byte(uuid_send(operation_id), 8) & 192) = 128",
            name="ck_edge_cash_command_operation_uuid4",
        ),
        CheckConstraint(
            "writer_epoch > 0",
            name="ck_edge_cash_command_writer_epoch",
        ),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="ck_edge_cash_command_request_hash",
        ),
        CheckConstraint(
            "result_hash ~ '^[0-9a-f]{64}$'",
            name="ck_edge_cash_command_result_hash",
        ),
        CheckConstraint(
            "jsonb_typeof(result_payload) = 'object' "
            "AND octet_length(result_payload::text) BETWEEN 2 AND 131072 "
            "AND result_payload ->> 'command_type' = command_type "
            "AND result_payload ->> 'operation_id' = operation_id::text "
            "AND result_payload ->> 'sale_id' = sale_id::text "
            "AND result_payload ->> 'receipt_number' = receipt_number "
            "AND result_payload ->> 'total_amount' = "
            "to_char(total_amount, 'FM9999999999990.00') "
            "AND result_payload ->> 'currency' = currency",
            name="ck_edge_cash_command_result_payload",
        ),
        Index(
            "ix_edge_cash_command_node_created",
            "tenant_id",
            "edge_node_id",
            "writer_epoch",
            "created_at",
        ),
    )


class Sale(Base):
    __tablename__ = "sale"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    branch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    register_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    shift_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    sale_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'sale'"))
    parent_sale_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'draft'"))
    receipt_number: Mapped[str | None] = mapped_column(Text)
    receipt_seq: Mapped[int | None] = mapped_column(BigInteger)
    operation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    operation_hash: Mapped[str | None] = mapped_column(Text)
    refund_attempt_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    is_test: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'TJS'"))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_by_sale_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    cashier_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    receipt_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    fiscal_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    marking_codes: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_sale_tenant_id_id"),
        UniqueConstraint(
            "id",
            "tenant_id",
            "branch_id",
            "register_id",
            "cashier_user_id",
            "operation_id",
            "status",
            "receipt_number",
            "total_amount",
            "currency",
            name="uq_sale_edge_cash_scope",
        ),
        CheckConstraint("sale_type IN ('sale','return')", name="ck_sale_type"),
        CheckConstraint("status IN ('draft','completed','voided')", name="ck_sale_status"),
        CheckConstraint("receipt_seq IS NULL OR receipt_seq > 0", name="ck_sale_receipt_seq"),
        CheckConstraint(
            "(operation_id IS NULL) = (operation_hash IS NULL)",
            name="ck_sale_operation_pair",
        ),
        CheckConstraint(
            "operation_hash IS NULL OR operation_hash ~ '^[0-9a-f]{64}$'",
            name="ck_sale_operation_hash",
        ),
        CheckConstraint(
            "refund_attempt_id IS NULL OR sale_type = 'return'",
            name="ck_sale_refund_attempt_return_only",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "refund_attempt_id"],
            ["pos_refund_attempt.tenant_id", "pos_refund_attempt.id"],
            name="fk_sale_tenant_refund_attempt",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_sale_tenant_refund_attempt",
            "tenant_id",
            "refund_attempt_id",
            unique=True,
            postgresql_where=text("refund_attempt_id IS NOT NULL"),
        ),
    )


class SaleItem(Base):
    __tablename__ = "sale_item"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    sale_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sale.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_sale_item_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sale_item.id", deferrable=True, initially="DEFERRED"),
    )
    catalog_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    batch_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    total_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'TJS'"))
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("qty > 0", name="ck_si_qty"),
        CheckConstraint("unit_price >= 0", name="ck_si_unit_price"),
        CheckConstraint("total_price >= 0", name="ck_si_total_price"),
        CheckConstraint(
            "parent_sale_item_id IS NULL OR parent_sale_item_id <> id",
            name="ck_si_parent_not_self",
        ),
    )


class SalePayment(Base):
    __tablename__ = "sale_payment"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    sale_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sale.id", ondelete="CASCADE"),
        nullable=False,
    )
    payment_method: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    operation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    operation_hash: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'TJS'"))
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    payment_attempt_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "payment_method IN ('cash','card','qr','bank_transfer')",
            name="ck_sp_method",
        ),
        CheckConstraint("amount > 0", name="ck_sp_amount"),
        CheckConstraint(
            "(operation_id IS NULL) = (operation_hash IS NULL)",
            name="ck_sp_operation_pair",
        ),
        CheckConstraint(
            "operation_hash IS NULL OR operation_hash ~ '^[0-9a-f]{64}$'",
            name="ck_sp_operation_hash",
        ),
        Index(
            "uq_sale_payment_tenant_operation",
            "tenant_id",
            "operation_id",
            unique=True,
            postgresql_where=text("operation_id IS NOT NULL"),
        ),
        ForeignKeyConstraint(
            ["tenant_id", "payment_attempt_id"],
            ["pos_payment_attempt.tenant_id", "pos_payment_attempt.id"],
            name="fk_sale_payment_tenant_payment_attempt",
            ondelete="RESTRICT",
        ),
        Index(
            "uq_sale_payment_tenant_attempt",
            "tenant_id",
            "payment_attempt_id",
            unique=True,
            postgresql_where=text("payment_attempt_id IS NOT NULL"),
        ),
    )


class POSPaymentAttempt(Base):
    __tablename__ = "pos_payment_attempt"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    sale_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    cashier_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    operation_hash: Mapped[str] = mapped_column(Text, nullable=False)
    payment_method: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'TJS'"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    external_reference: Mapped[str | None] = mapped_column(Text)
    void_reason: Mapped[str | None] = mapped_column(Text)
    void_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "operation_id",
            name="uq_pos_payment_attempt_tenant_operation",
        ),
        UniqueConstraint(
            "tenant_id",
            "id",
            name="uq_pos_payment_attempt_tenant_id_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "sale_id"],
            ["sale.tenant_id", "sale.id"],
            name="fk_pos_payment_attempt_tenant_sale",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "operation_hash ~ '^[0-9a-f]{64}$'",
            name="ck_pos_payment_attempt_operation_hash",
        ),
        CheckConstraint(
            "payment_method IN ('card','qr')",
            name="ck_pos_payment_attempt_method",
        ),
        CheckConstraint("amount > 0", name="ck_pos_payment_attempt_amount"),
        CheckConstraint("currency = 'TJS'", name="ck_pos_payment_attempt_currency"),
        CheckConstraint(
            "status IN ('pending','requires_reconciliation','confirmed','consumed','voided')",
            name="ck_pos_payment_attempt_status",
        ),
    )


class POSRefundAttempt(Base):
    __tablename__ = "pos_refund_attempt"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    parent_sale_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    register_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    confirmed_by_user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    operation_hash: Mapped[str] = mapped_column(Text, nullable=False)
    items_json: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False)
    external_allocations_json: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    external_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'TJS'"))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    void_reason: Mapped[str | None] = mapped_column(Text)
    void_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "operation_id", name="uq_pos_refund_attempt_tenant_operation"
        ),
        UniqueConstraint("tenant_id", "id", name="uq_pos_refund_attempt_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "parent_sale_id"],
            ["sale.tenant_id", "sale.id"],
            name="fk_pos_refund_attempt_tenant_sale",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "operation_hash ~ '^[0-9a-f]{64}$'",
            name="ck_pos_refund_attempt_operation_hash",
        ),
        CheckConstraint("total_amount > 0", name="ck_pos_refund_attempt_total"),
        CheckConstraint("external_amount > 0", name="ck_pos_refund_attempt_external_total"),
        CheckConstraint("external_amount <= total_amount", name="ck_pos_refund_attempt_amounts"),
        CheckConstraint("currency = 'TJS'", name="ck_pos_refund_attempt_currency"),
        CheckConstraint(
            "status IN ('pending','requires_reconciliation','confirmed','consumed','voided')",
            name="ck_pos_refund_attempt_status",
        ),
        Index(
            "uq_pos_refund_attempt_active_sale",
            "tenant_id",
            "parent_sale_id",
            unique=True,
            postgresql_where=text("status IN ('pending','requires_reconciliation','confirmed')"),
        ),
    )


class POSRefundReference(Base):
    __tablename__ = "pos_refund_reference"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    refund_attempt_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    payment_method: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    terminal_id: Mapped[str] = mapped_column(Text, nullable=False)
    document_number: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_by_user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "refund_attempt_id"],
            ["pos_refund_attempt.tenant_id", "pos_refund_attempt.id"],
            name="fk_pos_refund_reference_tenant_attempt",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "tenant_id",
            "refund_attempt_id",
            "payment_method",
            name="uq_pos_refund_reference_attempt_method",
        ),
        UniqueConstraint(
            "tenant_id",
            "terminal_id",
            "document_number",
            name="uq_pos_refund_reference_terminal_document",
        ),
        CheckConstraint(
            "payment_method IN ('card','qr','bank_transfer')",
            name="ck_pos_refund_reference_method",
        ),
        CheckConstraint("amount > 0", name="ck_pos_refund_reference_amount"),
    )


class PrescriptionLog(Base):
    __tablename__ = "prescription_log"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    sale_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sale.id", ondelete="CASCADE"),
        nullable=False,
    )
    sale_item_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    prescription_number: Mapped[str | None] = mapped_column(Text)
    doctor_name: Mapped[str | None] = mapped_column(Text)
    doctor_license: Mapped[str | None] = mapped_column(Text)
    patient_name: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
