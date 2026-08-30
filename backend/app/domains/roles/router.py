"""FastAPI endpoints for the roles domain."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    CurrentUser,
    current_user,
    get_db,
    get_redis,
    require_permission,
    require_recent_account_mfa,
    require_recent_mfa_if_support,
    require_recent_owner_mfa,
)
from app.core.errors import BusinessRuleError, PermissionDeniedError
from app.domains.roles.models import Role, UserAssignment
from app.domains.roles.repository import DirectoryUser, OwnershipTransferRecord, RolesRepository
from app.domains.roles.schemas import (
    AssignmentCreate,
    AssignmentRead,
    InviteUserRequest,
    OwnershipTransferActionResponse,
    OwnershipTransferCreate,
    OwnershipTransferListResponse,
    OwnershipTransferRead,
    PermissionRead,
    RoleArchiveRequest,
    RoleArchiveResponse,
    RoleCreate,
    RoleUpdate,
    RoleVersionRead,
    RoleWithPermissions,
    TemplateWithPermissions,
    UserListResponse,
    UserSearchRequest,
    UserSessionRevokeResponse,
    UserUpdate,
    UserWithAssignments,
)
from app.domains.roles.service import RolesService

router = APIRouter(prefix="/api/v1", tags=["roles"])


async def _service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    redis: Annotated[Redis, Depends(get_redis)],
) -> RolesService:
    return RolesService(RolesRepository(db), redis=redis)


def _current_tenant_or_400(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise BusinessRuleError(
            "Request is not scoped to a tenant",
            details={"hint": "Login as a tenant user or pass X-Tenant-Id (phase 2)."},
        )
    return user.tenant_id


def _set_search_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"


def _ownership_transfer_read(record: OwnershipTransferRecord) -> OwnershipTransferRead:
    return OwnershipTransferRead.model_validate(record)


@router.get(
    "/ownership-transfers",
    response_model=OwnershipTransferListResponse,
)
async def list_ownership_transfers(
    response: Response,
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[RolesService, Depends(_service)],
) -> OwnershipTransferListResponse:
    _current_tenant_or_400(user)
    _set_search_no_store(response)
    records = await service.list_ownership_transfers(actor_user_id=user.user_id)
    return OwnershipTransferListResponse(
        items=[_ownership_transfer_read(record) for record in records]
    )


@router.post(
    "/ownership-transfers",
    response_model=OwnershipTransferActionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_ownership_transfer(
    payload: OwnershipTransferCreate,
    user: Annotated[CurrentUser, Depends(require_recent_owner_mfa)],
    service: Annotated[RolesService, Depends(_service)],
) -> OwnershipTransferActionResponse:
    transfer = await service.create_ownership_transfer(
        tenant_id=_current_tenant_or_400(user),
        operation_id=payload.operation_id,
        target_membership_id=payload.target_membership_id,
    )
    return OwnershipTransferActionResponse(transfer=_ownership_transfer_read(transfer))


@router.post(
    "/ownership-transfers/{request_id}/cancel",
    response_model=OwnershipTransferActionResponse,
)
async def cancel_ownership_transfer(
    request_id: UUID,
    user: Annotated[CurrentUser, Depends(require_recent_owner_mfa)],
    service: Annotated[RolesService, Depends(_service)],
) -> OwnershipTransferActionResponse:
    transfer = await service.cancel_ownership_transfer(
        tenant_id=_current_tenant_or_400(user),
        request_id=request_id,
    )
    return OwnershipTransferActionResponse(transfer=_ownership_transfer_read(transfer))


@router.post(
    "/ownership-transfers/{request_id}/accept",
    response_model=OwnershipTransferActionResponse,
)
async def accept_ownership_transfer(
    request_id: UUID,
    user: Annotated[CurrentUser, Depends(require_recent_account_mfa)],
    service: Annotated[RolesService, Depends(_service)],
) -> OwnershipTransferActionResponse:
    transfer = await service.accept_ownership_transfer(
        tenant_id=_current_tenant_or_400(user),
        request_id=request_id,
    )
    return OwnershipTransferActionResponse(
        transfer=_ownership_transfer_read(transfer),
        sessions_revoked=True,
    )


async def _serialize_user_list(
    service: RolesService,
    pairs: list[tuple[DirectoryUser, list[UserAssignment]]],
    *,
    total: int,
    page: int,
    page_size: int,
) -> UserListResponse:
    roles_by_id = await service.repo.roles_by_ids(
        [assignment.role_id for _member, assignments in pairs for assignment in assignments]
    )
    items = [
        UserWithAssignments(
            id=user.id,
            membership_id=user.membership_id,
            is_tenant_owner=user.is_tenant_owner,
            email=user.email,
            full_name=user.full_name,
            phone=user.phone,
            status=user.status,
            last_login_at=user.last_login_at,
            can_require_password=user.can_require_password,
            assignments=[
                AssignmentRead.model_validate(assignment).model_copy(
                    update={
                        "role_name": (
                            roles_by_id[assignment.role_id].name
                            if assignment.role_id in roles_by_id
                            else None
                        )
                    }
                )
                for assignment in assignments
            ],
        )
        for user, assignments in pairs
    ]
    return UserListResponse(items=items, total=total, page=page, page_size=page_size)


def _role_with_permissions(
    role: Role,
    codes: list[str],
    *,
    has_hidden_permissions: bool = False,
    active_assignment_count: int = 0,
) -> RoleWithPermissions:
    return RoleWithPermissions(
        id=role.id,
        tenant_id=role.tenant_id,
        name=role.name,
        description=role.description,
        is_system=role.is_system,
        is_active=role.is_active,
        is_protected=role.is_protected,
        protected_kind=role.protected_kind,
        version=role.version,
        permissions=codes,
        has_hidden_permissions=has_hidden_permissions,
        active_assignment_count=active_assignment_count,
    )


async def require_role_catalog_access(
    user: Annotated[CurrentUser, Depends(current_user)],
) -> CurrentUser:
    if (user.is_developer or user.is_administrator) and user.support_access_session_id is None:
        return user
    if user.support_access_session_id is not None and user.permissions.intersection(
        {"roles.assign", "roles.create", "roles.update"}
    ):
        return user
    if user.is_tenant_owner and user.permissions.intersection(
        {"users.view", "roles.assign", "roles.create", "roles.update"}
    ):
        return user
    raise PermissionDeniedError("Role catalogue access required")


async def require_role_archive_access(
    user: Annotated[CurrentUser, Depends(current_user)],
) -> CurrentUser:
    required = {"roles.update", "roles.assign"}
    if not required.issubset(user.permissions):
        raise PermissionDeniedError("Role update and assignment access required")
    if user.support_access_session_id is not None or user.is_tenant_owner:
        return user
    raise PermissionDeniedError("Tenant owner or scoped support access required")


# -----------------------------------------------------------------------------
# Catalogue
# -----------------------------------------------------------------------------


@router.get("/permissions", response_model=list[PermissionRead])
async def list_permissions(
    user: Annotated[CurrentUser, Depends(require_role_catalog_access)],
    service: Annotated[RolesService, Depends(_service)],
) -> list[PermissionRead]:
    perms = await service.list_permissions(
        actor_id=user.user_id,
        tenant_id=_current_tenant_or_400(user),
        actor_permissions=user.permissions,
        actor_is_developer=user.is_developer,
        actor_is_administrator=user.is_administrator,
    )
    return [PermissionRead.model_validate(p) for p in perms]


@router.get("/roles", response_model=list[RoleWithPermissions])
async def list_roles(
    user: Annotated[CurrentUser, Depends(require_role_catalog_access)],
    service: Annotated[RolesService, Depends(_service)],
) -> list[RoleWithPermissions]:
    pairs = await service.list_roles_with_permissions(
        actor_id=user.user_id,
        tenant_id=_current_tenant_or_400(user),
        actor_permissions=user.permissions,
        actor_is_developer=user.is_developer,
        actor_is_administrator=user.is_administrator,
    )
    out: list[RoleWithPermissions] = []
    for role, codes, has_hidden_permissions, active_assignment_count in pairs:
        out.append(
            _role_with_permissions(
                role,
                codes,
                has_hidden_permissions=has_hidden_permissions,
                active_assignment_count=active_assignment_count,
            )
        )
    return out


@router.post("/roles", response_model=RoleWithPermissions, status_code=status.HTTP_201_CREATED)
async def create_role(
    payload: RoleCreate,
    user: Annotated[CurrentUser, Depends(require_permission("roles.create"))],
    _recent_mfa: Annotated[CurrentUser, Depends(require_recent_account_mfa)],
    service: Annotated[RolesService, Depends(_service)],
) -> RoleWithPermissions:
    role, codes = await service.create_role(
        actor_id=user.user_id,
        actor_permissions=user.permissions,
        actor_is_developer=user.is_developer,
        actor_is_administrator=user.is_administrator,
        tenant_id=_current_tenant_or_400(user),
        name=payload.name,
        description=payload.description,
        permission_codes=payload.permissions,
    )
    return _role_with_permissions(role, codes)


@router.patch("/roles/{role_id}", response_model=RoleWithPermissions)
async def update_role(
    role_id: UUID,
    payload: RoleUpdate,
    user: Annotated[CurrentUser, Depends(require_permission("roles.update"))],
    _recent_mfa: Annotated[CurrentUser, Depends(require_recent_account_mfa)],
    service: Annotated[RolesService, Depends(_service)],
) -> RoleWithPermissions:
    tenant_id = _current_tenant_or_400(user)
    role, codes = await service.update_role(
        actor_id=user.user_id,
        actor_permissions=user.permissions,
        actor_is_developer=user.is_developer,
        actor_is_administrator=user.is_administrator,
        tenant_id=tenant_id,
        role_id=role_id,
        expected_version=payload.expected_version,
        name=payload.name,
        description=payload.description,
        permission_codes=payload.permissions,
    )
    active_assignment_count = await service.repo.active_assignment_count(
        role.id,
        tenant_id=tenant_id,
    )
    return _role_with_permissions(
        role,
        codes,
        active_assignment_count=active_assignment_count,
    )


@router.get("/roles/{role_id}/versions", response_model=list[RoleVersionRead])
async def list_role_versions(
    role_id: UUID,
    user: Annotated[CurrentUser, Depends(require_role_catalog_access)],
    service: Annotated[RolesService, Depends(_service)],
) -> list[RoleVersionRead]:
    versions = await service.list_role_versions(
        actor_id=user.user_id,
        actor_permissions=user.permissions,
        actor_is_developer=user.is_developer,
        actor_is_administrator=user.is_administrator,
        tenant_id=_current_tenant_or_400(user),
        role_id=role_id,
    )
    return [
        RoleVersionRead(
            id=version.id,
            role_id=version.role_id,
            version=version.version,
            name=version.name,
            description=version.description,
            status=version.status,
            permissions=list(version.permissions),
            published_at=version.published_at,
            archived_at=version.archived_at,
            created_at=version.created_at,
            created_by=version.created_by,
            created_by_name=version.created_by_name,
        )
        for version in versions
    ]


@router.post("/roles/{role_id}/archive", response_model=RoleArchiveResponse)
async def archive_role(
    role_id: UUID,
    payload: RoleArchiveRequest,
    user: Annotated[CurrentUser, Depends(require_role_archive_access)],
    _recent_mfa: Annotated[CurrentUser, Depends(require_recent_account_mfa)],
    service: Annotated[RolesService, Depends(_service)],
) -> RoleArchiveResponse:
    result = await service.archive_role_with_replacement(
        actor_id=user.user_id,
        tenant_id=_current_tenant_or_400(user),
        role_id=role_id,
        expected_version=payload.expected_version,
        replacement_role_id=payload.replacement_role_id,
    )
    return RoleArchiveResponse(
        archived_version=result.archived_version,
        affected_memberships=result.affected_memberships,
    )


@router.get("/templates", response_model=list[TemplateWithPermissions])
async def list_templates(
    user: Annotated[CurrentUser, Depends(require_role_catalog_access)],
    service: Annotated[RolesService, Depends(_service)],
) -> list[TemplateWithPermissions]:
    """Global role presets for the builder — same gate as creating a role.
    A template only pre-fills the form; anti-escalation still applies on
    POST /roles, so a preset can never grant reach the actor lacks."""
    pairs = await service.list_templates_with_permissions(
        actor_id=user.user_id,
        tenant_id=_current_tenant_or_400(user),
        actor_permissions=user.permissions,
        actor_is_developer=user.is_developer,
        actor_is_administrator=user.is_administrator,
    )
    return [
        TemplateWithPermissions(
            id=template.id,
            name=template.name,
            slug=template.slug,
            description=template.description,
            is_system=template.is_system,
            is_active=template.is_active,
            permissions=codes,
        )
        for template, codes in pairs
    ]


# -----------------------------------------------------------------------------
# Users
# -----------------------------------------------------------------------------


@router.get("/users", response_model=UserListResponse)
async def list_users(
    user: Annotated[CurrentUser, Depends(require_permission("users.view"))],
    service: Annotated[RolesService, Depends(_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> UserListResponse:
    tenant_id = _current_tenant_or_400(user)
    pairs, total = await service.list_users(tenant_id, page=page, page_size=page_size)
    return await _serialize_user_list(
        service,
        pairs,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/users/search", response_model=UserListResponse)
async def search_users(
    payload: UserSearchRequest,
    response: Response,
    user: Annotated[CurrentUser, Depends(require_permission("users.view"))],
    service: Annotated[RolesService, Depends(_service)],
) -> UserListResponse:
    _set_search_no_store(response)
    tenant_id = _current_tenant_or_400(user)
    pairs, total = await service.search_users(
        tenant_id,
        q=payload.q,
        status=payload.status,
        role_id=payload.role_id,
        branch_id=payload.branch_id,
        visible_branch_ids=user.branch_scope_for("users.view"),
        page=payload.page,
        page_size=payload.page_size,
    )
    return await _serialize_user_list(
        service,
        pairs,
        total=total,
        page=payload.page,
        page_size=payload.page_size,
    )


@router.post(
    "/users/invite",
    response_model=AssignmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def invite_user(
    payload: InviteUserRequest,
    user: Annotated[CurrentUser, Depends(require_permission("users.invite"))],
    _recent_mfa: Annotated[CurrentUser, Depends(require_recent_mfa_if_support)],
    service: Annotated[RolesService, Depends(_service)],
) -> AssignmentRead:
    _, assignment, _ = await service.invite_user(
        actor_id=user.user_id,
        actor_permissions=user.permissions,
        actor_permission_scopes=user.permission_scopes,
        actor_is_developer=user.is_developer,
        actor_is_administrator=user.is_administrator,
        tenant_id=_current_tenant_or_400(user),
        email=str(payload.email),
        full_name=payload.full_name,
        role_id=payload.role_id,
        branch_id=payload.branch_id,
        password_required=payload.password_required,
    )
    return AssignmentRead.model_validate(assignment)


@router.patch("/users/{user_id}")
async def update_user(
    user_id: UUID,
    payload: UserUpdate,
    user: Annotated[CurrentUser, Depends(require_permission("users.update"))],
    _recent_mfa: Annotated[CurrentUser, Depends(require_recent_mfa_if_support)],
    service: Annotated[RolesService, Depends(_service)],
) -> dict[str, object]:
    fields = payload.model_dump(exclude_none=True, exclude={"status"})
    tenant_id = _current_tenant_or_400(user)
    if payload.status == "active":
        await service.activate_membership(
            actor_id=user.user_id,
            tenant_id=tenant_id,
            target_user_id=user_id,
        )
    updated = await service.update_user_profile(
        tenant_id=tenant_id,
        target_user_id=user_id,
        fields=fields,
    )
    return {"id": str(updated.id), "full_name": updated.full_name, "phone": updated.phone}


@router.post("/users/{user_id}/block")
async def block_user(
    user_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permission("users.block"))],
    _recent_mfa: Annotated[CurrentUser, Depends(require_recent_mfa_if_support)],
    service: Annotated[RolesService, Depends(_service)],
) -> dict[str, str]:
    await service.block_user(
        actor_id=user.user_id,
        actor_is_developer=user.is_developer,
        tenant_id=_current_tenant_or_400(user),
        target_user_id=user_id,
    )
    return {"status": "suspended"}


@router.post(
    "/users/{user_id}/sessions/revoke",
    response_model=UserSessionRevokeResponse,
)
async def revoke_user_sessions(
    user_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permission("users.block"))],
    _recent_mfa: Annotated[CurrentUser, Depends(require_recent_mfa_if_support)],
    service: Annotated[RolesService, Depends(_service)],
) -> UserSessionRevokeResponse:
    revoked_count = await service.revoke_user_sessions(
        actor_id=user.user_id,
        tenant_id=_current_tenant_or_400(user),
        target_user_id=user_id,
    )
    return UserSessionRevokeResponse(revoked_count=revoked_count)


@router.delete("/users/{user_id}")
async def soft_delete_user(
    user_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permission("users.delete"))],
    _recent_mfa: Annotated[CurrentUser, Depends(require_recent_mfa_if_support)],
    service: Annotated[RolesService, Depends(_service)],
) -> dict[str, str]:
    await service.soft_delete_user(
        actor_id=user.user_id,
        actor_is_developer=user.is_developer,
        tenant_id=_current_tenant_or_400(user),
        target_user_id=user_id,
    )
    return {"status": "offboarded"}


# -----------------------------------------------------------------------------
# Assignments
# -----------------------------------------------------------------------------


@router.post(
    "/users/{user_id}/assignments",
    response_model=AssignmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_assignment(
    user_id: UUID,
    payload: AssignmentCreate,
    user: Annotated[CurrentUser, Depends(require_permission("roles.assign"))],
    _recent_mfa: Annotated[CurrentUser, Depends(require_recent_mfa_if_support)],
    service: Annotated[RolesService, Depends(_service)],
) -> AssignmentRead:
    assignment = await service.assign_role(
        actor_id=user.user_id,
        actor_permissions=user.permissions,
        actor_permission_scopes=user.permission_scopes,
        actor_is_developer=user.is_developer,
        actor_is_administrator=user.is_administrator,
        tenant_id=_current_tenant_or_400(user),
        target_user_id=user_id,
        role_id=payload.role_id,
        branch_id=payload.branch_id,
        password_required=payload.password_required,
    )
    return AssignmentRead.model_validate(assignment)


@router.delete("/users/{user_id}/assignments/{assignment_id}")
async def revoke_assignment(
    user_id: UUID,
    assignment_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permission("roles.assign"))],
    _recent_mfa: Annotated[CurrentUser, Depends(require_recent_mfa_if_support)],
    service: Annotated[RolesService, Depends(_service)],
) -> dict[str, str]:
    await service.revoke_assignment(
        actor_id=user.user_id,
        actor_permissions=user.permissions,
        actor_permission_scopes=user.permission_scopes,
        actor_is_developer=user.is_developer,
        actor_is_administrator=user.is_administrator,
        tenant_id=_current_tenant_or_400(user),
        target_user_id=user_id,
        assignment_id=assignment_id,
    )
    return {"status": "revoked"}
