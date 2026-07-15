"""Database access for the roles domain."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select, text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.auth.models import AppUser
from app.domains.roles.models import (
    Permission,
    Role,
    RolePermission,
    RoleTemplate,
    RoleTemplatePermission,
    UserAssignment,
)


@dataclass(frozen=True)
class DirectoryUser:
    id: UUID
    email: str
    email_lower: str
    full_name: str
    phone: str | None
    home_tenant_id: UUID | None
    status: str
    last_login_at: datetime | None


_DIRECTORY_COLUMNS = (
    AppUser.id,
    AppUser.email,
    AppUser.email_lower,
    AppUser.full_name,
    AppUser.phone,
    AppUser.home_tenant_id,
    AppUser.status,
    AppUser.last_login_at,
)


def _directory_user_from_row(row: RowMapping) -> DirectoryUser:
    return DirectoryUser(
        id=cast(UUID, row["id"]),
        email=cast(str, row["email"]),
        email_lower=cast(str, row["email_lower"]),
        full_name=cast(str, row["full_name"]),
        phone=cast(str | None, row["phone"]),
        home_tenant_id=cast(UUID | None, row["home_tenant_id"]),
        status=cast(str, row["status"]),
        last_login_at=cast(datetime | None, row["last_login_at"]),
    )


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
        """Replace the role's permission set wholesale."""
        await self.session.execute(delete(RolePermission).where(RolePermission.role_id == role_id))
        for code in codes:
            self.session.add(RolePermission(role_id=role_id, permission_code=code))
        await self.session.flush()

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
    # user_assignment
    # -------------------------------------------------------------------------

    async def list_assignments_for_user(
        self, user_id: UUID, *, tenant_id: UUID | None = None
    ) -> list[UserAssignment]:
        stmt = select(UserAssignment).where(UserAssignment.user_id == user_id)
        if tenant_id is not None:
            stmt = stmt.where(UserAssignment.tenant_id == tenant_id)
        stmt = stmt.execution_options(populate_existing=True)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

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

    async def get_user(self, user_id: UUID) -> DirectoryUser | None:
        result = await self.session.execute(
            select(*_DIRECTORY_COLUMNS).where(AppUser.id == user_id)
        )
        row = result.mappings().one_or_none()
        return _directory_user_from_row(row) if row is not None else None

    async def get_user_by_email_support(self, email: str) -> AppUser | None:
        """Support-only global identity lookup used during tenant provisioning."""
        stmt = select(AppUser).where(AppUser.email_lower == email.lower())
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def find_invitable_user_id(self, *, tenant_id: UUID, email: str) -> UUID | None:
        result = await self.session.execute(
            text("SELECT public.find_invitable_user_id(:tenant_id, :email)"),
            {"tenant_id": tenant_id, "email": email},
        )
        return cast(UUID | None, result.scalar_one())

    async def insert_user(self, **fields: Any) -> AppUser:
        """Support-only identity creation used for the first tenant owner."""
        u = AppUser(**fields)
        self.session.add(u)
        await self.session.flush()
        await self.session.refresh(u)
        return u

    async def insert_invited_user(self, *, tenant_id: UUID, email: str, full_name: str) -> UUID:
        result = await self.session.execute(
            text("SELECT public.create_invited_app_user(" ":tenant_id, :email, :full_name)"),
            {"tenant_id": tenant_id, "email": email, "full_name": full_name},
        )
        return cast(UUID, result.scalar_one())

    async def update_user_profile(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        full_name: str,
        phone: str | None,
    ) -> DirectoryUser | None:
        result = await self.session.execute(
            text(
                "SELECT public.update_tenant_user_profile("
                ":tenant_id, :user_id, :full_name, :phone)"
            ),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "full_name": full_name,
                "phone": phone,
            },
        )
        if not bool(result.scalar_one()):
            return None
        return await self.get_user(user_id)

    async def set_user_status(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        status: str,
        changed_at: datetime,
    ) -> bool:
        result = await self.session.execute(
            text(
                "SELECT public.set_tenant_user_status("
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

    async def count_users_for_tenant(self, tenant_id: UUID) -> int:
        """Distinct users with at least one assignment in this tenant."""
        stmt = (
            select(func.count(func.distinct(AppUser.id)))
            .select_from(AppUser)
            .join(UserAssignment, UserAssignment.user_id == AppUser.id)
            .where(UserAssignment.tenant_id == tenant_id)
        )
        return int((await self.session.execute(stmt)).scalar_one())

    async def list_users_for_tenant(
        self, tenant_id: UUID, *, limit: int, offset: int
    ) -> list[DirectoryUser]:
        """One page of distinct users with an assignment in this tenant, in a
        stable order (full_name, email) so pages don't shuffle."""
        stmt = (
            select(*_DIRECTORY_COLUMNS)
            .join(UserAssignment, UserAssignment.user_id == AppUser.id)
            .where(UserAssignment.tenant_id == tenant_id)
            .order_by(AppUser.full_name.asc(), AppUser.email.asc())
            .distinct()
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(stmt)
        return [_directory_user_from_row(row) for row in result.mappings().all()]

    # -------------------------------------------------------------------------
    # effective permissions (authoritative database read)
    # -------------------------------------------------------------------------

    async def effective_permissions(self, user_id: UUID, tenant_id: UUID) -> set[str]:
        """Active permissions granted by active roles and assignments."""
        stmt = (
            select(RolePermission.permission_code)
            .join(Role, Role.id == RolePermission.role_id)
            .join(Permission, Permission.code == RolePermission.permission_code)
            .join(UserAssignment, UserAssignment.role_id == RolePermission.role_id)
            .where(
                and_(
                    UserAssignment.user_id == user_id,
                    UserAssignment.tenant_id == tenant_id,
                    UserAssignment.is_active.is_(True),
                    Role.is_active.is_(True),
                    Permission.is_active.is_(True),
                )
            )
            .distinct()
        )
        result = await self.session.execute(stmt)
        return set(result.scalars().all())
