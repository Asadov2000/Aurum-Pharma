"""SQLAlchemy models for the onboarding domain."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Local declarative base for the onboarding domain."""


class WizardState(Base):
    __tablename__ = "wizard_state"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    current_step: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    steps_completed: Mapped[list[int]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    wizard_data: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    is_completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (CheckConstraint("current_step BETWEEN 1 AND 8", name="ck_wizard_step"),)


class OnboardingChecklist(Base):
    __tablename__ = "onboarding_checklist"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    completed_tasks: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    catalog_items_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    trial_eligible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    trial_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    setup_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class TrialActivation(Base):
    """Immutable proof that a tenant has consumed its one free trial."""

    __tablename__ = "trial_activation"

    tenant_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
    )
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, unique=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    actor_session_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    subscription_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        unique=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    trial_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )

    __table_args__ = (
        CheckConstraint(
            "source IN ('manual','automatic','migration')",
            name="ck_trial_activation_source",
        ),
        CheckConstraint(
            "(source = 'manual' AND actor_user_id IS NOT NULL "
            "AND actor_session_id IS NOT NULL) OR "
            "(source IN ('automatic','migration') AND actor_user_id IS NULL "
            "AND actor_session_id IS NULL)",
            name="ck_trial_activation_actor",
        ),
        CheckConstraint("trial_ends_at > started_at", name="ck_trial_activation_period"),
    )
