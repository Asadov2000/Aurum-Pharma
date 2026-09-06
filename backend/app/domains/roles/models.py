"""SQLAlchemy 2.0 models for the roles domain."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Local declarative base for the roles domain."""


class Permission(Base):
    __tablename__ = "permission"

    code: Mapped[str] = mapped_column(Text, primary_key=True)
    group_code: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    min_level_required: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("4")
    )
    is_dangerous: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    scope_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'TENANT_ALL'")
    )
    target_role_type: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'tenant'")
    )
    risk_level: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'normal'"))
    developer_grantable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    administrator_grantable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    owner_grantable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    developer_delegable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    administrator_delegable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    owner_delegable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    requires_step_up: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    requires_confirmation: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint(
            "min_level_required BETWEEN 1 AND 4",
            name="ck_permission_min_level",
        ),
        CheckConstraint(
            "scope_type IN ('PLATFORM','TENANT_ALL','BRANCH_SET','OWN')",
            name="ck_permission_scope_type",
        ),
        CheckConstraint(
            "target_role_type IN ('platform','tenant')",
            name="ck_permission_target_role_type",
        ),
        CheckConstraint(
            "risk_level IN ('normal','sensitive','critical')",
            name="ck_permission_risk_level",
        ),
        CheckConstraint(
            "risk_level <> 'critical' OR requires_step_up",
            name="ck_permission_critical_requires_step_up",
        ),
        CheckConstraint(
            "NOT is_dangerous OR requires_confirmation",
            name="ck_permission_dangerous_requires_confirmation",
        ),
    )


class AuthorizationPolicyRevision(Base):
    """Tenant policy version maintained by database authorization triggers."""

    __tablename__ = "authorization_policy_revision"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class AuthorizationSubjectRevision(Base):
    """Per-user authorization version maintained by database triggers."""

    __tablename__ = "authorization_subject_revision"

    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class Role(Base):
    __tablename__ = "role"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_protected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    protected_kind: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))

    __table_args__ = (
        CheckConstraint("level BETWEEN 1 AND 4", name="ck_role_level"),
        CheckConstraint("version >= 1", name="ck_role_version"),
        CheckConstraint(
            "(is_protected AND protected_kind IN "
            "('developer','administrator','tenant_owner')) "
            "OR (NOT is_protected AND protected_kind IS NULL)",
            name="ck_role_protected_kind",
        ),
        UniqueConstraint("tenant_id", "name", name="uq_role_tenant_name"),
    )


class AccessRoleVersion(Base):
    """Immutable role definition published through protected database commands."""

    __tablename__ = "access_role_version"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    role_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("role.id", ondelete="RESTRICT"), nullable=False
    )
    tenant_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    creation_xid: Mapped[int] = mapped_column(BigInteger, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))


class AccessRoleVersionPermission(Base):
    __tablename__ = "access_role_version_permission"

    role_version_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("access_role_version.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    permission_code: Mapped[str] = mapped_column(
        Text,
        ForeignKey("permission.code", ondelete="RESTRICT"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TenantMembership(Base):
    __tablename__ = "tenant_membership"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    invited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    offboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','active','suspended','offboarded')",
            name="ck_tenant_membership_status",
        ),
        UniqueConstraint("tenant_id", "user_id", name="uq_tenant_membership_tenant_user"),
    )


class TenantInvitation(Base):
    __tablename__ = "tenant_invitation"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    membership_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenant_membership.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    operation_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))

    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_tenant_invitation_version"),
        CheckConstraint(
            "status IN ('pending','accepted','revoked')",
            name="ck_tenant_invitation_status",
        ),
        CheckConstraint("expires_at > issued_at", name="ck_tenant_invitation_expiry"),
        UniqueConstraint(
            "membership_id", "version", name="uq_tenant_invitation_membership_version"
        ),
        UniqueConstraint("tenant_id", "operation_id", name="uq_tenant_invitation_tenant_operation"),
    )


class TenantOwnership(Base):
    __tablename__ = "tenant_ownership"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    membership_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenant_membership.id", ondelete="RESTRICT"),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    granted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
            "membership_id",
            name="uq_tenant_ownership_tenant_membership",
        ),
    )


class TenantOwnershipTransfer(Base):
    __tablename__ = "tenant_ownership_transfer"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    initiator_membership_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenant_membership.id", ondelete="RESTRICT"),
        nullable=False,
    )
    target_membership_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenant_membership.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completion_xid: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))

    __table_args__ = (
        CheckConstraint(
            "initiator_membership_id <> target_membership_id",
            name="ck_tenant_ownership_transfer_distinct_memberships",
        ),
        CheckConstraint(
            "status IN ('pending','completed','cancelled','expired')",
            name="ck_tenant_ownership_transfer_status",
        ),
    )


class RolePermission(Base):
    __tablename__ = "role_permission"

    role_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("role.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission_code: Mapped[str] = mapped_column(
        Text, ForeignKey("permission.code"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class RoleTemplate(Base):
    """A reusable role preset (recommendation library). Global — like the
    permission catalogue, every tenant sees the same templates, no RLS. A
    template is owned by no one and nothing is assigned to it; it only
    pre-fills the role builder. Creating a real role still goes through
    POST /roles, where anti-escalation applies."""

    __tablename__ = "role_template"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    # Stable lookup key (slug), e.g. 'owner' / 'cashier' — survives renaming the
    # display name. Backfilled + made NOT NULL in migration 0023.
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq_role_template_name"),
        UniqueConstraint("slug", name="uq_role_template_slug"),
    )


class RoleTemplatePermission(Base):
    __tablename__ = "role_template_permission"

    template_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("role_template.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission_code: Mapped[str] = mapped_column(
        Text, ForeignKey("permission.code"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class UserAssignment(Base):
    __tablename__ = "user_assignment"

    id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    membership_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("tenant_membership.id", ondelete="RESTRICT"),
        nullable=False,
    )
    branch_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    role_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("role.id"), nullable=False
    )
    role_version_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    password_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    created_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    updated_by: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))

    __table_args__ = (
        ForeignKeyConstraint(
            ["role_version_id", "role_id"],
            ["access_role_version.id", "access_role_version.role_id"],
            name="fk_user_assignment_role_version_role",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "user_id", "tenant_id", "branch_id", name="uq_user_assignment_user_tenant_branch"
        ),
    )
