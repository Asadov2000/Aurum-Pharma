"""FastAPI endpoints for the roles domain."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, get_redis, require_any_permission, require_permission
from app.core.errors import BusinessRuleError
from app.domains.roles.models import Role
from app.domains.roles.repository import RolesRepository
from app.domains.roles.schemas import (
    AssignmentCreate,
    AssignmentRead,
    InviteUserRequest,
    PermissionRead,
    RoleCreate,
    RoleUpdate,
    RoleWithPermissions,
    TemplateWithPermissions,
    UserListResponse,
    UserUpdate,
    UserWithAssignments,
)
from app.domains.roles.service import RolesService

router = APIRouter(prefix="/api/v1", tags=["roles"])


async def _service(
    db: Annotated[AsyncSession, Depends(get_db)],
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


def _role_with_permissions(role: Role, codes: list[str]) -> RoleWithPermissions:
    return RoleWithPermissions(
        id=role.id,
        tenant_id=role.tenant_id,
        name=role.name,
        description=role.description,
        level=role.level,
        is_system=role.is_system,
        is_active=role.is_active,
        permissions=codes,
    )


# -----------------------------------------------------------------------------
# Catalogue
# -----------------------------------------------------------------------------


@router.get("/permissions", response_model=list[PermissionRead])
async def list_permissions(
    _user: Annotated[
        CurrentUser,
        Depends(
            require_any_permission("users.view", "roles.assign", "roles.create", "roles.update")
        ),
    ],
    service: Annotated[RolesService, Depends(_service)],
) -> list[PermissionRead]:
    perms = await service.list_permissions()
    return [PermissionRead.model_validate(p) for p in perms]


@router.get("/roles", response_model=list[RoleWithPermissions])
async def list_roles(
    _user: Annotated[
        CurrentUser,
        Depends(
            require_any_permission("users.view", "roles.assign", "roles.create", "roles.update")
        ),
    ],
    service: Annotated[RolesService, Depends(_service)],
) -> list[RoleWithPermissions]:
    pairs = await service.list_roles_with_permissions()
    out: list[RoleWithPermissions] = []
    for role, codes in pairs:
        out.append(_role_with_permissions(role, codes))
    return out


@router.post("/roles", response_model=RoleWithPermissions, status_code=status.HTTP_201_CREATED)
async def create_role(
    payload: RoleCreate,
    user: Annotated[CurrentUser, Depends(require_permission("roles.create"))],
    service: Annotated[RolesService, Depends(_service)],
) -> RoleWithPermissions:
    role, codes = await service.create_role(
        actor_level=user.level,
        actor_id=user.user_id,
        actor_permissions=user.permissions,
        actor_is_support=user.is_developer or user.is_administrator,
        tenant_id=_current_tenant_or_400(user),
        name=payload.name,
        description=payload.description,
        level=payload.level,
        permission_codes=payload.permissions,
    )
    return _role_with_permissions(role, codes)


@router.patch("/roles/{role_id}", response_model=RoleWithPermissions)
async def update_role(
    role_id: UUID,
    payload: RoleUpdate,
    user: Annotated[CurrentUser, Depends(require_permission("roles.update"))],
    service: Annotated[RolesService, Depends(_service)],
) -> RoleWithPermissions:
    role, codes = await service.update_role(
        actor_level=user.level,
        actor_id=user.user_id,
        actor_permissions=user.permissions,
        actor_is_support=user.is_developer or user.is_administrator,
        tenant_id=_current_tenant_or_400(user),
        role_id=role_id,
        name=payload.name,
        description=payload.description,
        level=payload.level,
        permission_codes=payload.permissions,
    )
    return _role_with_permissions(role, codes)


@router.get("/templates", response_model=list[TemplateWithPermissions])
async def list_templates(
    _user: Annotated[CurrentUser, Depends(require_permission("roles.create"))],
    service: Annotated[RolesService, Depends(_service)],
) -> list[TemplateWithPermissions]:
    """Global role presets for the builder — same gate as creating a role.
    A template only pre-fills the form; anti-escalation still applies on
    POST /roles, so a preset can never grant reach the actor lacks."""
    pairs = await service.list_templates_with_permissions()
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
    items = [
        UserWithAssignments(
            id=u.id,
            email=u.email,
            full_name=u.full_name,
            phone=u.phone,
            status=u.status,
            last_login_at=u.last_login_at,
            assignments=[AssignmentRead.model_validate(a) for a in assignments],
        )
        for u, assignments in pairs
    ]
    return UserListResponse(items=items, total=total, page=page, page_size=page_size)


@router.post(
    "/users/invite",
    response_model=AssignmentRead,
    status_code=status.HTTP_201_CREATED,
)
async def invite_user(
    payload: InviteUserRequest,
    user: Annotated[CurrentUser, Depends(require_permission("users.invite"))],
    service: Annotated[RolesService, Depends(_service)],
) -> AssignmentRead:
    _, assignment, _ = await service.invite_user(
        actor_level=user.level,
        actor_id=user.user_id,
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
    service: Annotated[RolesService, Depends(_service)],
) -> dict[str, object]:
    fields = payload.model_dump(exclude_none=True)
    updated = await service.update_user_profile(
        tenant_id=_current_tenant_or_400(user),
        target_user_id=user_id,
        fields=fields,
    )
    return {"id": str(updated.id), "full_name": updated.full_name, "phone": updated.phone}


@router.post("/users/{user_id}/block")
async def block_user(
    user_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permission("users.block"))],
    service: Annotated[RolesService, Depends(_service)],
) -> dict[str, str]:
    await service.block_user(
        actor_level=user.level,
        tenant_id=_current_tenant_or_400(user),
        target_user_id=user_id,
    )
    return {"status": "blocked"}


@router.delete("/users/{user_id}")
async def soft_delete_user(
    user_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permission("users.delete"))],
    service: Annotated[RolesService, Depends(_service)],
) -> dict[str, str]:
    await service.soft_delete_user(
        actor_level=user.level,
        tenant_id=_current_tenant_or_400(user),
        target_user_id=user_id,
    )
    return {"status": "archived"}


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
    service: Annotated[RolesService, Depends(_service)],
) -> AssignmentRead:
    assignment = await service.assign_role(
        actor_level=user.level,
        actor_id=user.user_id,
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
    service: Annotated[RolesService, Depends(_service)],
) -> dict[str, str]:
    await service.revoke_assignment(
        actor_level=user.level,
        tenant_id=_current_tenant_or_400(user),
        target_user_id=user_id,
        assignment_id=assignment_id,
    )
    return {"status": "revoked"}
