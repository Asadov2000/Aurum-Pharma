"""SQLAlchemy 2.0 mapped models for the auth domain.

These mirror the columns created in migration 0002. The mapped Base lives in
this module — there is no central declarative base in the project yet; each
domain owns its own and the metadatas are stitched together in alembic/env.py
when needed (in this phase the migration is hand-written, no autogenerate).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Local declarative base for the auth domain."""


class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    email: Mapped[str] = mapped_column(Text, nullable=False)
    # email_lower is GENERATED ALWAYS in Postgres — declare it as Computed so
    # SQLAlchemy omits it from INSERT/UPDATE statements.
    email_lower: Mapped[str] = mapped_column(
        Text,
        Computed("lower(email)", persisted=True),
        nullable=False,
    )
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str | None] = mapped_column(Text)
    password_hash: Mapped[str | None] = mapped_column(Text)
    mfa_prompt_dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_configured: Mapped[bool] = mapped_column(
        Boolean,
        Computed("password_hash IS NOT NULL", persisted=True),
        nullable=False,
    )
    is_developer: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_administrator: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    home_tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'invited'"))
    invited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('invited','active','blocked','archived')",
            name="ck_app_user_status",
        ),
    )


class UserPreferences(Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="CASCADE"),
        primary_key=True,
    )
    theme: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'system'"))
    density: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'comfortable'"))
    contrast: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'standard'"))
    reduce_motion: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    accent: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'teal'"))
    workspace_preferences: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("theme IN ('system','light','dark')", name="ck_user_preferences_theme"),
        CheckConstraint(
            "density IN ('auto','compact','comfortable','touch')",
            name="ck_user_preferences_density",
        ),
        CheckConstraint("contrast IN ('standard','high')", name="ck_user_preferences_contrast"),
        CheckConstraint(
            "accent IN ('teal','blue','violet','green','amber','rose')",
            name="ck_user_preferences_accent",
        ),
        CheckConstraint("version >= 1", name="ck_user_preferences_version"),
        CheckConstraint(
            "jsonb_typeof(workspace_preferences) = 'object'",
            name="ck_user_preferences_workspaces_object",
        ),
        CheckConstraint(
            "pg_column_size(workspace_preferences) <= 65536",
            name="ck_user_preferences_workspaces_size",
        ),
    )


class PlatformAccessGrant(Base):
    """Protected Developer/Administrator grant history.

    ``AppUser.is_developer`` and ``is_administrator`` remain temporary
    compatibility projections maintained by database triggers.
    """

    __tablename__ = "platform_access_grant"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="RESTRICT"),
        nullable=False,
    )
    access_kind: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="SET NULL"),
    )
    request_reason_code: Mapped[str] = mapped_column(Text, nullable=False)
    request_reason: Mapped[str] = mapped_column(Text, nullable=False)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    requires_approval: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    approval_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="SET NULL"),
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_reason_code: Mapped[str | None] = mapped_column(Text)
    approval_reason: Mapped[str | None] = mapped_column(Text)
    revoked_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="SET NULL"),
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason_code: Mapped[str | None] = mapped_column(Text)
    revoke_reason: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("statement_timestamp()")
    )

    __table_args__ = (
        CheckConstraint(
            "access_kind IN ('developer','administrator')",
            name="ck_platform_access_grant_kind",
        ),
        CheckConstraint(
            "status IN ('pending','active','revoked','expired')",
            name="ck_platform_access_grant_status",
        ),
        CheckConstraint("version >= 1", name="ck_platform_access_grant_version"),
    )


class Session(Base):
    __tablename__ = "session"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="CASCADE"),
        nullable=False,
    )
    refresh_token_hash: Mapped[str] = mapped_column(Text, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(INET)
    device_id_hash: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    last_used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(Text)
    rotation_operation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    rotated_from_session_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("session.id", ondelete="SET NULL"),
    )
    mfa_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SupportMfa(Base):
    __tablename__ = "support_mfa"

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="CASCADE"),
        primary_key=True,
    )
    active_secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    pending_secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    active_key_version: Mapped[int | None] = mapped_column(SmallInteger)
    pending_key_version: Mapped[int | None] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    active_generation: Mapped[int | None] = mapped_column(SmallInteger)
    pending_generation: Mapped[int | None] = mapped_column(SmallInteger)
    last_used_counter: Mapped[int | None] = mapped_column(BigInteger)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','active','recovery_pending')",
            name="ck_support_mfa_status",
        ),
    )


class SupportMfaRecoveryCode(Base):
    __tablename__ = "support_mfa_recovery_code"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("support_mfa.user_id", ondelete="CASCADE"),
        nullable=False,
    )
    generation: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    code_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "generation",
            "code_hash",
            name="uq_support_mfa_recovery_code",
        ),
    )


class AuthMfaChallenge(Base):
    __tablename__ = "auth_mfa_challenge"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    initiating_session_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("session.id", ondelete="CASCADE")
    )
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("app_user.id", ondelete="CASCADE"),
        nullable=False,
    )
    recovery_code_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("support_mfa_recovery_code.id", ondelete="SET NULL"),
    )
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    failed_attempts: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    ip_address: Mapped[str] = mapped_column(INET, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "purpose IN ('verify','enroll','recover','recovery_enroll')",
            name="ck_auth_mfa_challenge_purpose",
        ),
        CheckConstraint(
            "failed_attempts BETWEEN 0 AND 5",
            name="ck_auth_mfa_challenge_failed_attempts",
        ),
    )


class EmailCode(Base):
    __tablename__ = "email_code"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    email_lower: Mapped[str] = mapped_column(Text, nullable=False)
    code_hash: Mapped[str] = mapped_column(Text, nullable=False)
    code_salt: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'login'"))
    tenant_invitation_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    ip_address: Mapped[str | None] = mapped_column(INET)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "purpose IN ('login','password_reset','email_verification')",
            name="ck_email_code_purpose",
        ),
    )


class LoginAttempt(Base):
    __tablename__ = "login_attempt"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    email_lower: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    ip_address: Mapped[str] = mapped_column(INET, nullable=False)
    user_agent: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "outcome IN ('code_requested','code_failed','code_expired',"
            "'password_failed','totp_failed','success','rate_limited','blocked')",
            name="ck_login_attempt_outcome",
        ),
    )


# Suppress unused-import warning for symbols that show up only in __table_args__
_ = (Index, String)
