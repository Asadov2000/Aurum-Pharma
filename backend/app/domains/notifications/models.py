"""SQLAlchemy models for the notifications domain."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Local declarative base for the notifications domain."""


class Notification(Base):
    __tablename__ = "notification"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str | None] = mapped_column(Text)
    data: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    severity: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'info'"))
    dedupe_key: Mapped[str | None] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "severity IN ('info','warning','error','critical')",
            name="ck_notification_severity",
        ),
    )


class NotificationSubscription(Base):
    __tablename__ = "notification_subscription"

    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    event_type: Mapped[str] = mapped_column(Text, primary_key=True)
    channels: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[\"in_app\"]'::jsonb")
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class NotificationDelivery(Base):
    __tablename__ = "notification_delivery"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    notification_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("notification.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(Text, nullable=False)
    recipient: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    error_message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    claim_token: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    last_error_code: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "channel IN ('email','telegram','sms')",
            name="notification_delivery_channel_check",
        ),
        CheckConstraint(
            "status IN ('pending','processing','sent','failed','bounced')",
            name="notification_delivery_status_check",
        ),
        CheckConstraint(
            "(status = 'processing' AND claimed_at IS NOT NULL "
            "AND claim_token IS NOT NULL) OR "
            "(status <> 'processing' AND claimed_at IS NULL "
            "AND claim_token IS NULL)",
            name="notification_delivery_claim_state_check",
        ),
        CheckConstraint(
            "last_error_code IS NULL OR (length(last_error_code) <= 64 "
            "AND last_error_code ~ '^[a-z0-9_]+$')",
            name="notification_delivery_last_error_code_check",
        ),
    )
