"""Compose the caller's identity and their current tenant assignments."""

from uuid import UUID

from app.core.errors import NotFoundError
from app.domains.auth.profile_repository import ProfileRepository
from app.domains.auth.profile_schemas import ProfileAssignment, ProfileBranch, ProfileResponse
from app.domains.auth.repository import AuthRepository


class ProfileService:
    def __init__(self, repository: ProfileRepository, auth_repository: AuthRepository) -> None:
        self.repo = repository
        self.auth_repo = auth_repository

    async def get_profile(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID | None,
        branch_assignments: dict[str, str],
        is_tenant_owner: bool,
        is_support_access: bool,
    ) -> ProfileResponse:
        account = await self.auth_repo.get_user_by_id(user_id)
        if account is None:
            raise NotFoundError("User not found")
        profile = ProfileResponse(
            id=account.id,
            full_name=account.full_name,
            email=account.email,
            status=account.status,
            tenant_id=tenant_id,
            is_tenant_owner=is_tenant_owner and not is_support_access,
            platform_role=(
                "developer"
                if account.is_developer
                else "administrator" if account.is_administrator else None
            ),
        )
        if tenant_id is None or is_support_access:
            return profile
        records = await self.repo.assignment_names(
            user_id=user_id,
            tenant_id=tenant_id,
            assignments=[
                (None if branch == "tenant" else UUID(branch), UUID(role))
                for branch, role in branch_assignments.items()
            ],
        )
        profile.access_assignments = [
            ProfileAssignment(
                role_id=record.role_id,
                role_name=record.role_name,
                scope="tenant" if record.branch_id is None else "branch",
                branch=(
                    ProfileBranch(id=record.branch_id, name=record.branch_name)
                    if record.branch_id is not None and record.branch_name is not None
                    else None
                ),
            )
            for record in records
        ]
        return profile
