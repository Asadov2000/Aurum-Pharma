"""Database access for the roles domain."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, case, delete, func, or_, select, text, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.domains.auth.models import AppUser
from app.domains.foundation.models import Branch
from app.domains.roles.models import (
    AccessRoleVersion,
    AccessRoleVersionPermission,
    Permission,
    Role,
    RolePermission,
    RoleTemplate,
    RoleTemplatePermission,
    TenantInvitation,
    TenantMembership,
    TenantOwnership,
    UserAssignment,
)


@dataclass(frozen=True)
class DirectoryUser:
    id: UUID
    membership_id: UUID
    is_tenant_owner: bool
    email: str
    email_lower: str
    full_name: str
    phone: str | None
    home_tenant_id: UUID | None
    status: str
    last_login_at: datetime | None
    can_require_password: bool
    invited_at: datetime | None
    invitation_expires_at: datetime | None
    invitation_status: str | None


@dataclass(frozen=True)
class InvitationRecord:
    id: UUID
    status: str
    issued_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class EmployeeInvitationCreation:
    user_id: UUID
    membership_id: UUID
    invitation_id: UUID
    invited_at: datetime
    invitation_expires_at: datetime
    created: bool


@dataclass(frozen=True)
class UserSessionRevocationResult:
    result: str
    revoked_count: int


@dataclass(frozen=True)
class OwnershipTransferRecord:
    id: UUID
    tenant_id: UUID
    initiator_membership_id: UUID
    initiator_user_id: UUID
    initiator_full_name: str
    target_membership_id: UUID
    target_user_id: UUID
    target_full_name: str
    status: str
    expires_at: datetime
    completed_at: datetime | None
    cancelled_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class AuthorizationSnapshot:
    """One statement-level authorization view with its revision coordinates."""

    policy_revision: int
    subject_revision: int
    permissions: frozenset[str]
    permission_scopes: dict[str, frozenset[UUID] | None]


@dataclass(frozen=True)
class RoleVersionRecord:
    id: UUID
    role_id: UUID
    version: int
    name: str
    description: str | None
    status: str
    permissions: tuple[str, ...]
    published_at: datetime | None
    archived_at: datetime | None
    created_at: datetime
    created_by: UUID | None
    created_by_name: str | None


@dataclass(frozen=True)
class RolePublicationResult:
    role_version_id: UUID
    published_version: int
    affected_memberships: int


@dataclass(frozen=True)
class RoleArchiveResult:
    archived_version: int
    affected_memberships: int


_DIRECTORY_COLUMNS = (
    AppUser.id,
    TenantMembership.id.label("membership_id"),
    select(TenantOwnership.id)
    .where(
        TenantOwnership.tenant_id == TenantMembership.tenant_id,
        TenantOwnership.membership_id == TenantMembership.id,
        TenantOwnership.is_active.is_(True),
    )
    .exists()
    .label("is_tenant_owner"),
    AppUser.email,
    AppUser.email_lower,
    TenantMembership.full_name,
    TenantMembership.phone,
    AppUser.home_tenant_id,
    TenantMembership.status,
    AppUser.last_login_at,
    AppUser.password_configured.label("can_require_password"),
    select(TenantInvitation.issued_at)
    .where(TenantInvitation.membership_id == TenantMembership.id)
    .order_by(TenantInvitation.version.desc())
    .limit(1)
    .scalar_subquery()
    .label("invited_at"),
    select(TenantInvitation.expires_at)
    .where(TenantInvitation.membership_id == TenantMembership.id)
    .order_by(TenantInvitation.version.desc())
    .limit(1)
    .scalar_subquery()
    .label("invitation_expires_at"),
    select(
        case(
            (
                and_(
                    TenantInvitation.status == "pending",
                    TenantInvitation.expires_at <= func.statement_timestamp(),
                ),
                "expired",
            ),
            else_=TenantInvitation.status,
        )
    )
    .where(TenantInvitation.membership_id == TenantMembership.id)
    .order_by(TenantInvitation.version.desc())
    .limit(1)
    .scalar_subquery()
    .label("invitation_status"),
)


def _directory_user_from_row(row: RowMapping) -> DirectoryUser:
    return DirectoryUser(
        id=cast(UUID, row["id"]),
        membership_id=cast(UUID, row["membership_id"]),
        is_tenant_owner=bool(row["is_tenant_owner"]),
        email=cast(str, row["email"]),
        email_lower=cast(str, row["email_lower"]),
        full_name=cast(str, row["full_name"]),
        phone=cast(str | None, row["phone"]),
        home_tenant_id=cast(UUID | None, row["home_tenant_id"]),
        status=cast(str, row["status"]),
        last_login_at=cast(datetime | None, row["last_login_at"]),
        can_require_password=bool(row["can_require_password"]),
        invited_at=cast(datetime | None, row["invited_at"]),
        invitation_expires_at=cast(datetime | None, row["invitation_expires_at"]),
        invitation_status=cast(str | None, row["invitation_status"]),
    )


def _ownership_transfer_from_row(row: RowMapping) -> OwnershipTransferRecord:
    return OwnershipTransferRecord(
        id=cast(UUID, row["id"]),
        tenant_id=cast(UUID, row["tenant_id"]),
        initiator_membership_id=cast(UUID, row["initiator_membership_id"]),
        initiator_user_id=cast(UUID, row["initiator_user_id"]),
        initiator_full_name=cast(str, row["initiator_full_name"]),
        target_membership_id=cast(UUID, row["target_membership_id"]),
        target_user_id=cast(UUID, row["target_user_id"]),
        target_full_name=cast(str, row["target_full_name"]),
        status=cast(str, row["effective_status"]),
        expires_at=cast(datetime, row["expires_at"]),
        completed_at=cast(datetime | None, row["completed_at"]),
        cancelled_at=cast(datetime | None, row["cancelled_at"]),
        created_at=cast(datetime, row["created_at"]),
        updated_at=cast(datetime, row["updated_at"]),
    )


_OWNERSHIP_TRANSFER_SELECT = """
SELECT
  transfer.id,
  transfer.tenant_id,
  transfer.initiator_membership_id,
  initiator.user_id AS initiator_user_id,
  initiator.full_name AS initiator_full_name,
  transfer.target_membership_id,
  target.user_id AS target_user_id,
  target.full_name AS target_full_name,
  CASE
    WHEN transfer.status = 'pending'
      AND transfer.expires_at <= pg_catalog.statement_timestamp()
    THEN 'expired'
    ELSE transfer.status
  END AS effective_status,
  transfer.expires_at,
  transfer.completed_at,
  transfer.cancelled_at,
  transfer.created_at,
  transfer.updated_at
FROM public.tenant_ownership_transfer AS transfer
JOIN public.tenant_membership AS initiator
  ON initiator.id = transfer.initiator_membership_id
 AND initiator.tenant_id = transfer.tenant_id
JOIN public.tenant_membership AS target
  ON target.id = transfer.target_membership_id
 AND target.tenant_id = transfer.tenant_id
"""


class RolesRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # -------------------------------------------------------------------------
    # permission
    # -------------------------------------------------------------------------

    async def list_permissions(self) -> list[Permission]:
        stmt = select(Permission).order_by(Permission.group_code, Permission.code)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def actor_has_scoped_permission(
        self,
        *,
        tenant_id: UUID,
        permission_code: str,
        branch_id: UUID | None,
    ) -> bool:
        result = await self.session.execute(
            text(
                "SELECT public.tenant_actor_has_scoped_permission("
                ":tenant_id, :permission_code, :branch_id)"
            ),
            {
                "tenant_id": tenant_id,
                "permission_code": permission_code,
                "branch_id": branch_id,
            },
        )
        return bool(result.scalar_one())

    # -------------------------------------------------------------------------
    # role
    # -------------------------------------------------------------------------

    async def list_roles(self, *, tenant_id: UUID | None = None) -> list[Role]:
        stmt = select(Role).order_by(Role.level, Role.name)
        if tenant_id is None:
            stmt = stmt.where(Role.tenant_id.is_(None))
        else:
            stmt = stmt.where(or_(Role.tenant_id.is_(None), Role.tenant_id == tenant_id))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_role(self, role_id: UUID) -> Role | None:
        return await self.session.get(Role, role_id)

    async def get_role_for_update(self, role_id: UUID) -> Role | None:
        stmt = (
            select(Role)
            .where(Role.id == role_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def roles_by_ids(self, role_ids: list[UUID]) -> dict[UUID, Role]:
        if not role_ids:
            return {}
        stmt = select(Role).where(Role.id.in_(role_ids))
        result = await self.session.execute(stmt)
        return {role.id: role for role in result.scalars().all()}

    async def get_role_by_name(self, name: str, *, tenant_id: UUID | None = None) -> Role | None:
        stmt = select(Role).where(
            and_(
                Role.name == name,
                Role.tenant_id.is_(None) if tenant_id is None else Role.tenant_id == tenant_id,
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_role_permissions(self, role_id: UUID) -> list[str]:
        stmt = (
            select(RolePermission.permission_code)
            .where(RolePermission.role_id == role_id)
            .order_by(RolePermission.permission_code)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def permissions_for_roles(self, role_ids: list[UUID]) -> dict[UUID, list[str]]:
        """All permission codes for many roles in one query → {role_id: [codes]}."""
        if not role_ids:
            return {}
        stmt = (
            select(RolePermission.role_id, RolePermission.permission_code)
            .where(RolePermission.role_id.in_(role_ids))
            .order_by(RolePermission.permission_code)
        )
        out: dict[UUID, list[str]] = {}
        for role_id, code in (await self.session.execute(stmt)).all():
            out.setdefault(role_id, []).append(code)
        return out

    async def insert_role(self, **fields: Any) -> Role:
        role = Role(**fields)
        self.session.add(role)
        await self.session.flush()
        await self.session.refresh(role)
        return role

    async def update_role(self, role: Role, **fields: Any) -> Role:
        for key, value in fields.items():
            setattr(role, key, value)
        await self.session.flush()
        await self.session.refresh(role)
        return role

    async def set_role_permissions(self, role_id: UUID, codes: list[str]) -> None:
        """Replace one tenant role's permissions and audit the exact set diff."""
        before = await self.get_role_permissions(role_id)
        after = sorted(set(codes))
        if before == after:
            return
        await self.session.execute(delete(RolePermission).where(RolePermission.role_id == role_id))
        for code in after:
            self.session.add(RolePermission(role_id=role_id, permission_code=code))
        await self.session.flush()
        await self.session.execute(
            text(
                "SELECT public.record_role_permission_change("
                ":role_id, CAST(:before AS TEXT[]), CAST(:after AS TEXT[]))"
            ),
            {"role_id": role_id, "before": before, "after": after},
        )

    async def initialize_role_version(self, role_id: UUID) -> UUID:
        result = await self.session.execute(
            text("SELECT public.initialize_tenant_role_version(:role_id)"),
            {"role_id": role_id},
        )
        return cast(UUID, result.scalar_one())

    async def get_published_role_version_id(self, role_id: UUID) -> UUID | None:
        stmt = select(AccessRoleVersion.id).where(
            AccessRoleVersion.role_id == role_id,
            AccessRoleVersion.status == "published",
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def publish_role_version(
        self,
        *,
        role_id: UUID,
        expected_version: int,
        name: str,
        description: str | None,
        permission_codes: list[str],
    ) -> RolePublicationResult:
        result = await self.session.execute(
            text("""
                SELECT role_version_id, published_version, affected_memberships
                FROM public.publish_tenant_role_version(
                  :role_id,
                  :expected_version,
                  :name,
                  :description,
                  CAST(:permission_codes AS TEXT[])
                )
                """),
            {
                "role_id": role_id,
                "expected_version": expected_version,
                "name": name,
                "description": description,
                "permission_codes": permission_codes,
            },
        )
        row = result.mappings().one()
        return RolePublicationResult(
            role_version_id=cast(UUID, row["role_version_id"]),
            published_version=int(row["published_version"]),
            affected_memberships=int(row["affected_memberships"]),
        )

    async def archive_role_with_replacement(
        self,
        *,
        role_id: UUID,
        expected_version: int,
        replacement_role_id: UUID,
    ) -> RoleArchiveResult:
        result = await self.session.execute(
            text("""
                SELECT archived_version, affected_memberships
                FROM public.archive_tenant_role_with_replacement(
                  :role_id,
                  :expected_version,
                  :replacement_role_id
                )
                """),
            {
                "role_id": role_id,
                "expected_version": expected_version,
                "replacement_role_id": replacement_role_id,
            },
        )
        row = result.mappings().one()
        return RoleArchiveResult(
            archived_version=int(row["archived_version"]),
            affected_memberships=int(row["affected_memberships"]),
        )

    async def list_role_versions(self, role_id: UUID) -> list[RoleVersionRecord]:
        stmt = (
            select(
                AccessRoleVersion,
                AppUser.full_name.label("created_by_name"),
                func.coalesce(
                    func.array_agg(AccessRoleVersionPermission.permission_code).filter(
                        AccessRoleVersionPermission.permission_code.is_not(None)
                    ),
                    [],
                ).label("permissions"),
            )
            .outerjoin(
                AccessRoleVersionPermission,
                AccessRoleVersionPermission.role_version_id == AccessRoleVersion.id,
            )
            .outerjoin(AppUser, AppUser.id == AccessRoleVersion.created_by)
            .where(AccessRoleVersion.role_id == role_id)
            .group_by(AccessRoleVersion.id, AppUser.full_name)
            .order_by(AccessRoleVersion.version.desc())
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            RoleVersionRecord(
                id=version.id,
                role_id=version.role_id,
                version=version.version,
                name=version.name,
                description=version.description,
                status=version.status,
                permissions=tuple(sorted(cast(list[str], permissions))),
                published_at=version.published_at,
                archived_at=version.archived_at,
                created_at=version.created_at,
                created_by=version.created_by,
                created_by_name=created_by_name,
            )
            for version, created_by_name, permissions in rows
        ]

    async def active_assignment_count(self, role_id: UUID, *, tenant_id: UUID) -> int:
        stmt = select(func.count(UserAssignment.id)).where(
            UserAssignment.role_id == role_id,
            UserAssignment.tenant_id == tenant_id,
            UserAssignment.is_active.is_(True),
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def active_assignment_counts(
        self, role_ids: list[UUID], *, tenant_id: UUID
    ) -> dict[UUID, int]:
        if not role_ids:
            return {}
        stmt = (
            select(UserAssignment.role_id, func.count(UserAssignment.id))
            .where(
                UserAssignment.role_id.in_(role_ids),
                UserAssignment.tenant_id == tenant_id,
                UserAssignment.is_active.is_(True),
            )
            .group_by(UserAssignment.role_id)
        )
        return {role_id: int(count) for role_id, count in (await self.session.execute(stmt)).all()}

    async def active_user_ids_for_role(self, role_id: UUID, *, tenant_id: UUID) -> list[UUID]:
        stmt = (
            select(UserAssignment.user_id)
            .where(
                UserAssignment.role_id == role_id,
                UserAssignment.tenant_id == tenant_id,
                UserAssignment.is_active.is_(True),
            )
            .distinct()
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def user_has_active_role(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        role_id: UUID,
    ) -> bool:
        stmt = select(
            select(UserAssignment.id)
            .where(
                UserAssignment.tenant_id == tenant_id,
                UserAssignment.user_id == user_id,
                UserAssignment.role_id == role_id,
                UserAssignment.is_active.is_(True),
            )
            .exists()
        )
        return bool((await self.session.execute(stmt)).scalar_one())

    async def existing_active_permission_codes(self, codes: list[str]) -> set[str]:
        if not codes:
            return set()
        stmt = select(Permission.code).where(
            Permission.code.in_(codes), Permission.is_active.is_(True)
        )
        result = await self.session.execute(stmt)
        return set(result.scalars().all())

    async def active_permission_levels(self, codes: list[str]) -> dict[str, int]:
        if not codes:
            return {}
        stmt = select(Permission.code, Permission.min_level_required).where(
            Permission.code.in_(codes), Permission.is_active.is_(True)
        )
        result = await self.session.execute(stmt)
        return dict(result.tuples().all())

    # -------------------------------------------------------------------------
    # role_template (global recommendation library)
    # -------------------------------------------------------------------------

    async def list_templates(self) -> list[RoleTemplate]:
        stmt = (
            select(RoleTemplate).where(RoleTemplate.is_active.is_(True)).order_by(RoleTemplate.name)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_template_by_slug(self, slug: str) -> RoleTemplate | None:
        stmt = select(RoleTemplate).where(
            RoleTemplate.slug == slug, RoleTemplate.is_active.is_(True)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_template_permissions(self, template_id: UUID) -> list[str]:
        stmt = (
            select(RoleTemplatePermission.permission_code)
            .where(RoleTemplatePermission.template_id == template_id)
            .order_by(RoleTemplatePermission.permission_code)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def permissions_for_templates(self, template_ids: list[UUID]) -> dict[UUID, list[str]]:
        """All permission codes for many templates in one query → {tpl_id: [codes]}."""
        if not template_ids:
            return {}
        stmt = (
            select(RoleTemplatePermission.template_id, RoleTemplatePermission.permission_code)
            .where(RoleTemplatePermission.template_id.in_(template_ids))
            .order_by(RoleTemplatePermission.permission_code)
        )
        out: dict[UUID, list[str]] = {}
        for template_id, code in (await self.session.execute(stmt)).all():
            out.setdefault(template_id, []).append(code)
        return out

    # -------------------------------------------------------------------------
    # tenant membership / ownership
    # -------------------------------------------------------------------------

    async def lock_tenant_for_owner_provisioning(self, tenant_id: UUID) -> bool:
        result = await self.session.execute(
            text("SELECT id FROM public.tenant " "WHERE id = :tenant_id FOR UPDATE"),
            {"tenant_id": tenant_id},
        )
        return result.scalar_one_or_none() is not None

    async def insert_membership(self, **fields: Any) -> TenantMembership:
        membership = TenantMembership(**fields)
        self.session.add(membership)
        await self.session.flush()
        await self.session.refresh(membership)
        return membership

    async def insert_invitation(self, **fields: Any) -> TenantInvitation:
        invitation = TenantInvitation(**fields)
        self.session.add(invitation)
        await self.session.flush()
        await self.session.refresh(invitation)
        return invitation

    async def create_employee_invitation(
        self,
        *,
        tenant_id: UUID,
        email: str,
        full_name: str,
        phone: str | None,
        operation_id: UUID,
        issued_at: datetime,
    ) -> EmployeeInvitationCreation:
        row = (
            (
                await self.session.execute(
                    text(
                        "SELECT * FROM public.create_tenant_employee_invitation("
                        ":tenant_id, :email, :full_name, :phone, :operation_id, :issued_at)"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "email": email,
                        "full_name": full_name,
                        "phone": phone,
                        "operation_id": operation_id,
                        "issued_at": issued_at,
                    },
                )
            )
            .mappings()
            .one()
        )
        return EmployeeInvitationCreation(
            user_id=cast(UUID, row["employee_user_id"]),
            membership_id=cast(UUID, row["employee_membership_id"]),
            invitation_id=cast(UUID, row["employee_invitation_id"]),
            invited_at=cast(datetime, row["invited_at"]),
            invitation_expires_at=cast(datetime, row["invitation_expires_at"]),
            created=bool(row["employee_created"]),
        )

    async def latest_invitation_for_membership(
        self, membership_id: UUID
    ) -> InvitationRecord | None:
        invitation = (
            await self.session.execute(
                select(TenantInvitation)
                .where(TenantInvitation.membership_id == membership_id)
                .order_by(TenantInvitation.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if invitation is None:
            return None
        status = invitation.status
        if status == "pending" and invitation.expires_at <= datetime.now(
            invitation.expires_at.tzinfo
        ):
            status = "expired"
        return InvitationRecord(
            id=invitation.id,
            status=status,
            issued_at=invitation.issued_at,
            expires_at=invitation.expires_at,
        )

    async def reissue_invitation(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        operation_id: UUID,
        issued_at: datetime,
    ) -> InvitationRecord:
        row = (
            (
                await self.session.execute(
                    text(
                        "SELECT * FROM public.reissue_tenant_invitation("
                        ":tenant_id, :user_id, :operation_id, :issued_at)"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "user_id": user_id,
                        "operation_id": operation_id,
                        "issued_at": issued_at,
                    },
                )
            )
            .mappings()
            .one()
        )
        return InvitationRecord(
            id=cast(UUID, row["invitation_id"]),
            status=cast(str, row["invitation_status"]),
            issued_at=cast(datetime, row["invited_at"]),
            expires_at=cast(datetime, row["invitation_expires_at"]),
        )

    async def get_membership(self, membership_id: UUID) -> TenantMembership | None:
        return await self.session.get(
            TenantMembership,
            membership_id,
            populate_existing=True,
        )

    async def get_membership_for_user(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
    ) -> TenantMembership | None:
        stmt = (
            select(TenantMembership)
            .where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.user_id == user_id,
            )
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def create_ownership_transfer(
        self,
        *,
        operation_id: UUID,
        target_membership_id: UUID,
        expires_at: datetime,
    ) -> OwnershipTransferRecord | None:
        await self.session.execute(
            text(
                "SELECT public.create_tenant_ownership_transfer("
                ":operation_id, :target_membership_id, :expires_at)"
            ),
            {
                "operation_id": operation_id,
                "target_membership_id": target_membership_id,
                "expires_at": expires_at,
            },
        )
        return await self.get_ownership_transfer(operation_id)

    async def cancel_ownership_transfer(
        self,
        *,
        request_id: UUID,
    ) -> OwnershipTransferRecord | None:
        await self.session.execute(
            text("SELECT public.cancel_tenant_ownership_transfer(:request_id)"),
            {"request_id": request_id},
        )
        return await self.get_ownership_transfer(request_id)

    async def accept_ownership_transfer(
        self,
        *,
        request_id: UUID,
    ) -> OwnershipTransferRecord | None:
        await self.session.execute(
            text("SELECT public.accept_tenant_ownership_transfer(:request_id)"),
            {"request_id": request_id},
        )
        return await self.get_ownership_transfer(request_id)

    async def get_ownership_transfer(
        self,
        request_id: UUID,
    ) -> OwnershipTransferRecord | None:
        row = (
            (
                await self.session.execute(
                    text(
                        _OWNERSHIP_TRANSFER_SELECT + " WHERE transfer.id = :request_id "
                        "AND transfer.tenant_id = public.current_tenant_id()"
                    ),
                    {"request_id": request_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        return _ownership_transfer_from_row(row) if row is not None else None

    async def list_ownership_transfers(
        self,
        *,
        actor_user_id: UUID,
    ) -> list[OwnershipTransferRecord]:
        rows = (
            (
                await self.session.execute(
                    text(
                        _OWNERSHIP_TRANSFER_SELECT
                        + " WHERE transfer.tenant_id = public.current_tenant_id() "
                        "AND (initiator.user_id = :actor_user_id "
                        "OR target.user_id = :actor_user_id) "
                        "ORDER BY transfer.created_at DESC, transfer.id DESC"
                    ),
                    {"actor_user_id": actor_user_id},
                )
            )
            .mappings()
            .all()
        )
        return [_ownership_transfer_from_row(row) for row in rows]

    async def revoke_user_sessions(
        self,
        *,
        tenant_id: UUID,
        target_user_id: UUID,
    ) -> UserSessionRevocationResult:
        row = (
            (
                await self.session.execute(
                    text(
                        "SELECT * FROM public.revoke_tenant_user_auth_sessions("
                        ":tenant_id, :target_user_id)"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "target_user_id": target_user_id,
                    },
                )
            )
            .mappings()
            .one()
        )
        return UserSessionRevocationResult(
            result=str(row["result"]),
            revoked_count=int(row["revoked_count"]),
        )

    async def find_membership_by_email(
        self,
        *,
        tenant_id: UUID,
        email: str,
    ) -> TenantMembership | None:
        """Resolve an email only inside the caller's tenant membership set."""
        stmt = (
            select(TenantMembership)
            .join(AppUser, AppUser.id == TenantMembership.user_id)
            .where(
                TenantMembership.tenant_id == tenant_id,
                AppUser.email_lower == email.lower().strip(),
            )
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def insert_ownership(self, **fields: Any) -> TenantOwnership:
        ownership = TenantOwnership(**fields)
        self.session.add(ownership)
        await self.session.flush()
        await self.session.refresh(ownership)
        return ownership

    async def get_active_ownership(
        self,
        *,
        tenant_id: UUID,
        membership_id: UUID,
    ) -> TenantOwnership | None:
        stmt = (
            select(TenantOwnership)
            .where(
                TenantOwnership.tenant_id == tenant_id,
                TenantOwnership.membership_id == membership_id,
                TenantOwnership.is_active.is_(True),
            )
            .execution_options(populate_existing=True)
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def has_active_ownership(self, *, tenant_id: UUID, user_id: UUID) -> bool:
        stmt = select(
            select(TenantOwnership.id)
            .join(
                TenantMembership,
                TenantMembership.id == TenantOwnership.membership_id,
            )
            .where(
                TenantOwnership.tenant_id == tenant_id,
                TenantOwnership.is_active.is_(True),
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.user_id == user_id,
                TenantMembership.status == "active",
            )
            .exists()
        )
        return bool((await self.session.execute(stmt)).scalar_one())

    async def count_active_owners(self, tenant_id: UUID) -> int:
        stmt = (
            select(func.count(TenantOwnership.id))
            .join(
                TenantMembership,
                TenantMembership.id == TenantOwnership.membership_id,
            )
            .where(
                TenantOwnership.tenant_id == tenant_id,
                TenantOwnership.is_active.is_(True),
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.status == "active",
            )
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def deactivate_ownership(
        self,
        *,
        tenant_id: UUID,
        membership_id: UUID,
        actor_id: UUID,
        revoked_at: datetime,
    ) -> bool:
        result = await self.session.execute(
            update(TenantOwnership)
            .where(
                TenantOwnership.tenant_id == tenant_id,
                TenantOwnership.membership_id == membership_id,
                TenantOwnership.is_active.is_(True),
            )
            .values(
                is_active=False,
                revoked_at=revoked_at,
                updated_by=actor_id,
            )
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]

    async def update_membership_profile(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        full_name: str,
        phone: str | None,
    ) -> bool:
        result = await self.session.execute(
            text(
                "SELECT public.update_tenant_membership_profile("
                ":tenant_id, :user_id, :full_name, :phone)"
            ),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "full_name": full_name,
                "phone": phone,
            },
        )
        return bool(result.scalar_one())

    async def set_membership_status(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        status: str,
        changed_at: datetime,
    ) -> bool:
        result = await self.session.execute(
            text(
                "SELECT public.set_tenant_membership_status("
                ":tenant_id, :user_id, :status, :changed_at)"
            ),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "status": status,
                "changed_at": changed_at,
            },
        )
        return bool(result.scalar_one())

    # -------------------------------------------------------------------------
    # user_assignment
    # -------------------------------------------------------------------------

    async def list_assignments_for_user(
        self,
        user_id: UUID,
        *,
        tenant_id: UUID | None = None,
    ) -> list[UserAssignment]:
        stmt = select(UserAssignment).where(UserAssignment.user_id == user_id)
        if tenant_id is not None:
            stmt = stmt.where(UserAssignment.tenant_id == tenant_id)
        stmt = stmt.execution_options(populate_existing=True)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def lock_user_assignments(self, tenant_id: UUID, user_id: UUID) -> None:
        await self.session.execute(
            text(
                "SELECT pg_catalog.pg_advisory_xact_lock("
                "pg_catalog.hashtextextended(:lock_key, 0))"
            ),
            {"lock_key": f"user-assignment:{tenant_id}:{user_id}"},
        )

    async def active_branch_ids(self, tenant_id: UUID, branch_ids: set[UUID]) -> set[UUID]:
        if not branch_ids:
            return set()
        result = await self.session.execute(
            select(Branch.id).where(
                Branch.tenant_id == tenant_id,
                Branch.id.in_(branch_ids),
                Branch.is_active.is_(True),
            )
        )
        return set(result.scalars().all())

    async def assignments_for_users(
        self, user_ids: list[UUID], *, tenant_id: UUID
    ) -> list[UserAssignment]:
        """Assignments for many users in one query (kills the per-user N+1)."""
        if not user_ids:
            return []
        stmt = (
            select(UserAssignment)
            .where(
                UserAssignment.user_id.in_(user_ids),
                UserAssignment.tenant_id == tenant_id,
            )
            .execution_options(populate_existing=True)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_assignments_for_tenant(self, tenant_id: UUID) -> list[UserAssignment]:
        stmt = (
            select(UserAssignment)
            .where(UserAssignment.tenant_id == tenant_id)
            .order_by(UserAssignment.created_at.asc())
            .execution_options(populate_existing=True)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_assignment(self, assignment_id: UUID) -> UserAssignment | None:
        return await self.session.get(
            UserAssignment,
            assignment_id,
            populate_existing=True,
        )

    async def insert_assignment(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        branch_id: UUID | None,
        role_id: UUID,
        password_required: bool,
    ) -> UserAssignment:
        stmt = select(UserAssignment).from_statement(
            text(
                "SELECT * FROM public.create_tenant_user_assignment("
                ":tenant_id, :user_id, :branch_id, :role_id, :password_required)"
            )
        )
        result = await self.session.execute(
            stmt,
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "branch_id": branch_id,
                "role_id": role_id,
                "password_required": password_required,
            },
        )
        return cast(UserAssignment, result.scalar_one())

    async def reactivate_assignment(
        self,
        assignment_id: UUID,
        *,
        tenant_id: UUID,
        role_id: UUID,
        password_required: bool,
    ) -> UserAssignment | None:
        stmt = (
            select(UserAssignment)
            .from_statement(
                text(
                    "SELECT * FROM public.reactivate_tenant_user_assignment("
                    ":tenant_id, :assignment_id, :role_id, :password_required)"
                )
            )
            .execution_options(populate_existing=True)
        )
        result = await self.session.execute(
            stmt,
            {
                "tenant_id": tenant_id,
                "assignment_id": assignment_id,
                "role_id": role_id,
                "password_required": password_required,
            },
        )
        return result.scalar_one_or_none()

    async def deactivate_assignment(self, assignment_id: UUID, *, tenant_id: UUID) -> int:
        result = await self.session.execute(
            text("SELECT public.deactivate_tenant_user_assignment(" ":tenant_id, :assignment_id)"),
            {"tenant_id": tenant_id, "assignment_id": assignment_id},
        )
        return int(result.scalar_one())

    async def hard_delete_assignment(self, assignment_id: UUID) -> int:
        result = await self.session.execute(
            delete(UserAssignment).where(UserAssignment.id == assignment_id)
        )
        return result.rowcount or 0  # type: ignore[attr-defined]

    # -------------------------------------------------------------------------
    # users (lives in auth domain but the roles domain owns the queries that
    # join through user_assignment — keeping them here avoids a circular
    # repository pile-up in auth)
    # -------------------------------------------------------------------------

    async def get_user(self, user_id: UUID, *, tenant_id: UUID) -> DirectoryUser | None:
        result = await self.session.execute(
            select(*_DIRECTORY_COLUMNS)
            .join(
                TenantMembership,
                and_(
                    TenantMembership.user_id == AppUser.id,
                    TenantMembership.tenant_id == tenant_id,
                ),
            )
            .where(AppUser.id == user_id)
        )
        row = result.mappings().one_or_none()
        return _directory_user_from_row(row) if row is not None else None

    async def get_user_by_email_support(self, email: str) -> AppUser | None:
        """Support-only global identity lookup used during tenant provisioning."""
        stmt = select(AppUser).where(AppUser.email_lower == email.lower())
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def insert_user(self, **fields: Any) -> AppUser:
        """Support-only global identity creation."""
        u = AppUser(**fields)
        self.session.add(u)
        await self.session.flush()
        await self.session.refresh(u)
        return u

    async def count_users_for_tenant(self, tenant_id: UUID) -> int:
        """All tenant memberships, including pending users without a role."""
        stmt = (
            select(func.count(TenantMembership.id))
            .select_from(TenantMembership)
            .where(TenantMembership.tenant_id == tenant_id)
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def list_users_for_tenant(
        self, tenant_id: UUID, *, limit: int, offset: int
    ) -> list[DirectoryUser]:
        """One page of memberships in this tenant, in a
        stable order (full_name, email) so pages don't shuffle."""
        stmt = (
            select(*_DIRECTORY_COLUMNS)
            .join(
                TenantMembership,
                and_(
                    TenantMembership.user_id == AppUser.id,
                    TenantMembership.tenant_id == tenant_id,
                ),
            )
            .order_by(
                TenantMembership.full_name.asc(),
                AppUser.email.asc(),
                AppUser.id.asc(),
            )
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return [_directory_user_from_row(row) for row in result.mappings().all()]

    async def search_users_for_tenant(
        self,
        tenant_id: UUID,
        *,
        q: str | None = None,
        status: str | None = None,
        role_id: UUID | None = None,
        branch_id: UUID | None = None,
        visible_branch_ids: set[UUID] | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[list[DirectoryUser], int]:
        """Search tenant memberships without exposing rows outside branch scope.

        A tenant-wide active assignment (``branch_id IS NULL``) applies to every
        selected branch. When role and branch filters are combined, both must
        match the same active assignment.
        """
        if visible_branch_ids is not None:
            if not visible_branch_ids:
                return [], 0
            if branch_id is not None and branch_id not in visible_branch_ids:
                return [], 0

        clauses: list[ColumnElement[bool]] = [TenantMembership.tenant_id == tenant_id]
        term = q.strip() if q is not None else ""
        if term:
            clauses.append(
                or_(
                    TenantMembership.full_name.icontains(term, autoescape=True),
                    AppUser.email.icontains(term, autoescape=True),
                    TenantMembership.phone.icontains(term, autoescape=True),
                )
            )
        if status is not None:
            clauses.append(TenantMembership.status == status)

        if role_id is not None or branch_id is not None or visible_branch_ids is not None:
            assignment_clauses: list[ColumnElement[bool]] = [
                UserAssignment.tenant_id == tenant_id,
                UserAssignment.user_id == AppUser.id,
                UserAssignment.membership_id == TenantMembership.id,
                UserAssignment.is_active.is_(True),
            ]
            if role_id is not None:
                assignment_clauses.append(UserAssignment.role_id == role_id)
            if branch_id is not None:
                assignment_clauses.append(
                    or_(
                        UserAssignment.branch_id == branch_id,
                        UserAssignment.branch_id.is_(None),
                    )
                )
            if visible_branch_ids is not None:
                assignment_clauses.append(
                    or_(
                        UserAssignment.branch_id.in_(
                            sorted(visible_branch_ids, key=str),
                        ),
                        UserAssignment.branch_id.is_(None),
                    )
                )
            clauses.append(
                select(UserAssignment.id)
                .where(*assignment_clauses)
                .correlate(AppUser, TenantMembership)
                .exists()
            )

        count_stmt = (
            select(func.count(TenantMembership.id))
            .select_from(AppUser)
            .join(
                TenantMembership,
                and_(
                    TenantMembership.user_id == AppUser.id,
                    TenantMembership.tenant_id == tenant_id,
                ),
            )
            .where(*clauses)
        )
        total = int((await self.session.execute(count_stmt)).scalar_one())

        stmt = (
            select(*_DIRECTORY_COLUMNS)
            .join(
                TenantMembership,
                and_(
                    TenantMembership.user_id == AppUser.id,
                    TenantMembership.tenant_id == tenant_id,
                ),
            )
            .where(*clauses)
            .order_by(
                func.lower(TenantMembership.full_name).asc(),
                AppUser.email_lower.asc(),
                AppUser.id.asc(),
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.session.execute(stmt)
        users = [_directory_user_from_row(row) for row in result.mappings().all()]
        return users, total

    # -------------------------------------------------------------------------
    # effective permissions (authoritative database read)
    # -------------------------------------------------------------------------

    async def effective_permissions(self, user_id: UUID, tenant_id: UUID) -> set[str]:
        """Active permissions granted by active roles and assignments."""
        snapshot = await self.authorization_snapshot(user_id, tenant_id)
        return set(snapshot.permissions)

    async def authorization_snapshot(self, user_id: UUID, tenant_id: UUID) -> AuthorizationSnapshot:
        """Read revisions and capability/scope pairs from one PostgreSQL snapshot."""
        result = await self.session.execute(
            text("""
                SELECT
                  policy.revision AS policy_revision,
                  COALESCE(subject.revision, 1::BIGINT) AS subject_revision,
                  granted_permission.code AS permission_code,
                  granted_permission.scope_type,
                  assignment.branch_id
                FROM public.authorization_policy_revision AS policy
                LEFT JOIN public.authorization_subject_revision AS subject
                  ON subject.tenant_id = policy.tenant_id
                 AND subject.user_id = :user_id
                LEFT JOIN public.tenant_membership AS membership
                  ON membership.tenant_id = policy.tenant_id
                 AND membership.user_id = :user_id
                 AND membership.status = 'active'
                LEFT JOIN public.user_assignment AS assignment
                  ON assignment.tenant_id = policy.tenant_id
                 AND assignment.user_id = :user_id
                 AND assignment.membership_id = membership.id
                 AND assignment.is_active
                LEFT JOIN public.role AS assigned_role
                  ON assigned_role.id = assignment.role_id
                 AND assigned_role.is_active
                 AND (
                   assigned_role.tenant_id IS NULL
                   OR assigned_role.tenant_id = :tenant_id
                 )
                LEFT JOIN public.access_role_version AS role_version
                  ON role_version.id = assignment.role_version_id
                 AND role_version.role_id = assigned_role.id
                 AND role_version.status = 'published'
                LEFT JOIN public.access_role_version_permission AS role_permission
                  ON role_permission.role_version_id = role_version.id
                LEFT JOIN public.permission AS granted_permission
                  ON granted_permission.code = role_permission.permission_code
                 AND granted_permission.is_active
                WHERE policy.tenant_id = :tenant_id
                ORDER BY granted_permission.code, assignment.branch_id
                """),
            {"tenant_id": tenant_id, "user_id": user_id},
        )
        rows = result.mappings().all()
        if not rows:
            raise RuntimeError("Authorization revision ledger is missing for tenant")

        mutable_scopes: dict[str, set[UUID] | None] = {}
        for row in rows:
            raw_code = row["permission_code"]
            if raw_code is None:
                continue
            code = str(raw_code)
            branch_id = cast(UUID | None, row["branch_id"])
            scope_type = str(row["scope_type"])

            # Tenant/platform capabilities are ineffective when they only come
            # from a branch assignment. Branch-aware capabilities retain the
            # assignment that granted them.
            if branch_id is not None and scope_type in {"PLATFORM", "TENANT_ALL"}:
                continue
            if code in mutable_scopes and mutable_scopes[code] is None:
                continue
            if branch_id is None:
                mutable_scopes[code] = None
                continue
            mutable_scopes.setdefault(code, set())
            scoped_branches = mutable_scopes[code]
            if scoped_branches is not None:
                scoped_branches.add(branch_id)

        permission_scopes = {
            code: None if branches is None else frozenset(branches)
            for code, branches in mutable_scopes.items()
        }
        first = rows[0]
        return AuthorizationSnapshot(
            policy_revision=int(first["policy_revision"]),
            subject_revision=int(first["subject_revision"]),
            permissions=frozenset(permission_scopes),
            permission_scopes=permission_scopes,
        )
