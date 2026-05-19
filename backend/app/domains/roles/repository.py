"""Database access for the roles domain."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.auth.models import AppUser
from app.domains.roles.models import Permission, Role, RolePermission, UserAssignment


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

    async def list_roles(self) -> list[Role]:
        stmt = select(Role).order_by(Role.level, Role.name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_role(self, role_id: UUID) -> Role | None:
        return await self.session.get(Role, role_id)

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

    # -------------------------------------------------------------------------
    # user_assignment
    # -------------------------------------------------------------------------

    async def list_assignments_for_user(
        self, user_id: UUID, *, tenant_id: UUID | None = None
    ) -> list[UserAssignment]:
        stmt = select(UserAssignment).where(UserAssignment.user_id == user_id)
        if tenant_id is not None:
            stmt = stmt.where(UserAssignment.tenant_id == tenant_id)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_assignments_for_tenant(self, tenant_id: UUID) -> list[UserAssignment]:
        stmt = (
            select(UserAssignment)
            .where(UserAssignment.tenant_id == tenant_id)
            .order_by(UserAssignment.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_assignment(self, assignment_id: UUID) -> UserAssignment | None:
        return await self.session.get(UserAssignment, assignment_id)

    async def insert_assignment(self, **fields: Any) -> UserAssignment:
        a = UserAssignment(**fields)
        self.session.add(a)
        await self.session.flush()
        await self.session.refresh(a)
        return a

    async def reactivate_assignment(
        self,
        assignment_id: UUID,
        *,
        role_id: UUID,
        password_required: bool,
        updated_by: UUID | None = None,
    ) -> UserAssignment | None:
        await self.session.execute(
            update(UserAssignment)
            .where(UserAssignment.id == assignment_id)
            .values(
                is_active=True,
                role_id=role_id,
                password_required=password_required,
                updated_by=updated_by,
            )
        )
        return await self.session.get(UserAssignment, assignment_id)

    async def deactivate_assignment(self, assignment_id: UUID) -> int:
        result = await self.session.execute(
            update(UserAssignment).where(UserAssignment.id == assignment_id).values(is_active=False)
        )
        return result.rowcount or 0  # type: ignore[attr-defined]

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

    async def get_user(self, user_id: UUID) -> AppUser | None:
        return await self.session.get(AppUser, user_id)

    async def get_user_by_email(self, email: str) -> AppUser | None:
        stmt = select(AppUser).where(AppUser.email_lower == email.lower())
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def insert_user(self, **fields: Any) -> AppUser:
        u = AppUser(**fields)
        self.session.add(u)
        await self.session.flush()
        await self.session.refresh(u)
        return u

    async def update_user(self, user: AppUser, **fields: Any) -> AppUser:
        for key, value in fields.items():
            setattr(user, key, value)
        await self.session.flush()
        await self.session.refresh(user)
        return user

    async def list_users_for_tenant(self, tenant_id: UUID) -> list[AppUser]:
        """Distinct users that have at least one assignment in this tenant."""
        stmt = (
            select(AppUser)
            .join(UserAssignment, UserAssignment.user_id == AppUser.id)
            .where(UserAssignment.tenant_id == tenant_id)
            .order_by(AppUser.created_at.asc())
            .distinct()
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    # -------------------------------------------------------------------------
    # effective permissions (loaded from DB; cached in Redis by the service)
    # -------------------------------------------------------------------------

    async def effective_permissions(self, user_id: UUID, tenant_id: UUID) -> set[str]:
        """Union of permissions across all active assignments of this user in
        this tenant."""
        stmt = (
            select(RolePermission.permission_code)
            .join(UserAssignment, UserAssignment.role_id == RolePermission.role_id)
            .where(
                and_(
                    UserAssignment.user_id == user_id,
                    UserAssignment.tenant_id == tenant_id,
                    UserAssignment.is_active.is_(True),
                )
            )
            .distinct()
        )
        result = await self.session.execute(stmt)
        return set(result.scalars().all())
