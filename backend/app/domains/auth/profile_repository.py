"""RLS-scoped display data for already authorized personal assignments."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.foundation.models import Branch
from app.domains.roles.models import Role, TenantMembership, UserAssignment


@dataclass(frozen=True)
class ProfileAssignmentRecord:
    role_id: UUID
    role_name: str
    branch_id: UUID | None
    branch_name: str | None


class ProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def assignment_names(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        assignments: list[tuple[UUID | None, UUID]],
    ) -> list[ProfileAssignmentRecord]:
        if not assignments:
            return []
        selected = or_(
            *(
                and_(
                    UserAssignment.role_id == role_id,
                    UserAssignment.branch_id == branch_id,
                )
                for branch_id, role_id in assignments
            )
        )
        statement = (
            select(Role.id, Role.name, Branch.id, Branch.name)
            .select_from(UserAssignment)
            .join(Role, Role.id == UserAssignment.role_id)
            .join(
                TenantMembership,
                and_(
                    TenantMembership.id == UserAssignment.membership_id,
                    TenantMembership.tenant_id == tenant_id,
                    TenantMembership.user_id == user_id,
                ),
            )
            .outerjoin(
                Branch,
                and_(Branch.id == UserAssignment.branch_id, Branch.tenant_id == tenant_id),
            )
            .where(
                UserAssignment.tenant_id == tenant_id,
                UserAssignment.user_id == user_id,
                UserAssignment.is_active.is_(True),
                TenantMembership.status == "active",
                Role.is_active.is_(True),
                or_(Role.tenant_id == tenant_id, Role.tenant_id.is_(None)),
                or_(UserAssignment.branch_id.is_(None), Branch.is_active.is_(True)),
                selected,
            )
            .order_by(Role.name, Role.id, Branch.name, Branch.id)
        )
        rows = (await self.session.execute(statement)).all()
        return [
            ProfileAssignmentRecord(
                role_id=role_id,
                role_name=role_name,
                branch_id=branch_id,
                branch_name=branch_name,
            )
            for role_id, role_name, branch_id, branch_name in rows
        ]
