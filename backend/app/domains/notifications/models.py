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
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("channel IN ('email','telegram','sms')", name="ck_nd_channel"),
        CheckConstraint("status IN ('pending','sent','failed','bounced')", name="ck_nd_status"),
    )
