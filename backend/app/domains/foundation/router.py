"""FastAPI endpoints for the foundation domain.

Two routers:
- admin_router (`/api/v1/admin/...`) — support-only operations like tenant
  CRUD; gated by `require_support` so only is_developer / is_administrator
  can touch them.
- tenant_router (`/api/v1/...`) — in-tenant operations: settings, branches,
  registers. Gated by domain permissions (`require_permission`) which today
  pass for support and will pass for owner/seller once roles is wired up.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, current_user, get_db, require_permission
from app.core.errors import AuthenticationError, BusinessRuleError, PermissionDeniedError
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.schemas import (
    BranchCreate,
    BranchRead,
    BranchUpdate,
    OwnerCreate,
    OwnerProvisionRead,
    RegisterCreate,
    RegisterRead,
    RegisterUpdate,
    TenantCreate,
    TenantRead,
    TenantSettingsRead,
    TenantSettingsUpdate,
    TenantUpdate,
)
from app.domains.foundation.service import FoundationService
from app.domains.roles.repository import RolesRepository
from app.domains.roles.service import RolesService


async def _service(db: Annotated[AsyncSession, Depends(get_db)]) -> FoundationService:
    return FoundationService(FoundationRepository(db))


async def require_support(
    user: Annotated[CurrentUser, Depends(current_user)],
) -> CurrentUser:
    if not (user.is_developer or user.is_administrator):
        raise PermissionDeniedError("Support privileges required")
    return user


def _current_tenant_or_400(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        # Support-pool users with no home_tenant_id pick which tenant they act
        # on via X-Tenant-Id; in phase 1 that header is not implemented yet,
        # so we just refuse. Once roles lands the dependency can be smarter.
        raise BusinessRuleError(
            "Request is not scoped to a tenant",
            details={"hint": "Login as a tenant user or pass X-Tenant-Id (phase 2)."},
        )
    return user.tenant_id


# =============================================================================
# Admin router — /api/v1/admin/...
# =============================================================================

admin_router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@admin_router.post(
    "/tenants",
    response_model=TenantRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_support)],
)
async def create_tenant(
    payload: TenantCreate,
    service: Annotated[FoundationService, Depends(_service)],
) -> TenantRead:
    tenant = await service.create_tenant(payload=payload.model_dump(exclude_none=True))
    return TenantRead.model_validate(tenant)


@admin_router.get(
    "/tenants",
    response_model=list[TenantRead],
    dependencies=[Depends(require_support)],
)
async def list_tenants(
    service: Annotated[FoundationService, Depends(_service)],
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> list[TenantRead]:
    tenants = await service.list_tenants(limit=limit, offset=offset)
    return [TenantRead.model_validate(t) for t in tenants]


@admin_router.get(
    "/tenants/{tenant_id}",
    response_model=TenantRead,
    dependencies=[Depends(require_support)],
)
async def get_tenant(
    tenant_id: UUID,
    service: Annotated[FoundationService, Depends(_service)],
) -> TenantRead:
    tenant = await service.get_tenant(tenant_id)
    return TenantRead.model_validate(tenant)


@admin_router.patch(
    "/tenants/{tenant_id}",
    response_model=TenantRead,
    dependencies=[Depends(require_support)],
)
async def update_tenant(
    tenant_id: UUID,
    payload: TenantUpdate,
    service: Annotated[FoundationService, Depends(_service)],
) -> TenantRead:
    tenant = await service.update_tenant(tenant_id, fields=payload.model_dump(exclude_none=True))
    return TenantRead.model_validate(tenant)


@admin_router.post(
    "/tenants/{tenant_id}/owner",
    response_model=OwnerProvisionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_tenant_owner(
    tenant_id: UUID,
    payload: OwnerCreate,
    user: Annotated[CurrentUser, Depends(require_support)],
    service: Annotated[FoundationService, Depends(_service)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OwnerProvisionRead:
    """Onboard the first owner of a tenant: create the owner account, instantiate
    the tenant «Владелец» role from the global template, and assign it — all in
    one transaction (get_db wraps the request; any error rolls everything back,
    so there is never an owner without a role). Support-only."""
    tenant = await service.get_tenant(tenant_id)  # NotFoundError → 404
    if tenant.status == "archived":
        raise BusinessRuleError(
            "Аптека в архиве — нельзя добавить владельца",
            details={"status": tenant.status},
        )
    roles = RolesService(RolesRepository(db))
    owner, role = await roles.provision_owner(
        tenant_id=tenant_id,
        email=str(payload.email),
        full_name=payload.full_name,
        actor_id=user.user_id,
    )
    return OwnerProvisionRead(
        user_id=owner.id,
        email=owner.email,
        home_tenant_id=tenant_id,
        role_id=role.id,
    )


# =============================================================================
# Tenant router — /api/v1/...
# =============================================================================

tenant_router = APIRouter(prefix="/api/v1", tags=["tenant"])


# ---- settings ---------------------------------------------------------------


@tenant_router.get("/tenant/settings", response_model=TenantSettingsRead)
async def get_tenant_settings(
    user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[FoundationService, Depends(_service)],
) -> TenantSettingsRead:
    settings = await service.get_settings(_current_tenant_or_400(user))
    return TenantSettingsRead.model_validate(settings)


@tenant_router.patch("/tenant/settings", response_model=TenantSettingsRead)
async def update_tenant_settings(
    payload: TenantSettingsUpdate,
    user: Annotated[CurrentUser, Depends(require_permission("settings.update"))],
    service: Annotated[FoundationService, Depends(_service)],
) -> TenantSettingsRead:
    raw = payload.model_dump(exclude_none=True)
    # Pydantic returned ExpiryThresholds as a nested dict — flatten to plain JSON.
    if "expiry_thresholds" in raw:
        raw["expiry_thresholds"] = dict(raw["expiry_thresholds"])
    settings = await service.update_settings(
        _current_tenant_or_400(user), fields=raw, updated_by=user.user_id
    )
    return TenantSettingsRead.model_validate(settings)


# ---- branches ---------------------------------------------------------------


@tenant_router.get("/branches", response_model=list[BranchRead])
async def list_branches(
    _user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[FoundationService, Depends(_service)],
    include_inactive: bool = Query(False),
) -> list[BranchRead]:
    branches = await service.list_branches(include_inactive=include_inactive)
    return [BranchRead.model_validate(b) for b in branches]


@tenant_router.post("/branches", response_model=BranchRead, status_code=status.HTTP_201_CREATED)
async def create_branch(
    payload: BranchCreate,
    user: Annotated[CurrentUser, Depends(require_permission("branches.create"))],
    service: Annotated[FoundationService, Depends(_service)],
) -> BranchRead:
    branch = await service.create_branch(
        tenant_id=_current_tenant_or_400(user),
        fields=payload.model_dump(exclude_none=True),
        created_by=user.user_id,
    )
    return BranchRead.model_validate(branch)


@tenant_router.get("/branches/{branch_id}", response_model=BranchRead)
async def get_branch(
    branch_id: UUID,
    _user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[FoundationService, Depends(_service)],
) -> BranchRead:
    branch = await service.get_branch(branch_id)
    return BranchRead.model_validate(branch)


@tenant_router.patch("/branches/{branch_id}", response_model=BranchRead)
async def update_branch(
    branch_id: UUID,
    payload: BranchUpdate,
    user: Annotated[CurrentUser, Depends(require_permission("branches.update"))],
    service: Annotated[FoundationService, Depends(_service)],
) -> BranchRead:
    branch = await service.update_branch(
        branch_id,
        fields=payload.model_dump(exclude_none=True),
        updated_by=user.user_id,
    )
    return BranchRead.model_validate(branch)


@tenant_router.delete(
    "/branches/{branch_id}",
    response_model=BranchRead,
)
async def delete_branch(
    branch_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permission("branches.delete"))],
    service: Annotated[FoundationService, Depends(_service)],
) -> BranchRead:
    branch = await service.soft_delete_branch(branch_id, updated_by=user.user_id)
    return BranchRead.model_validate(branch)


# ---- registers --------------------------------------------------------------


@tenant_router.get("/registers", response_model=list[RegisterRead])
async def list_registers(
    _user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[FoundationService, Depends(_service)],
    branch_id: Annotated[UUID | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> list[RegisterRead]:
    registers = await service.list_registers(branch_id=branch_id, include_inactive=include_inactive)
    return [RegisterRead.model_validate(r) for r in registers]


@tenant_router.post("/registers", response_model=RegisterRead, status_code=status.HTTP_201_CREATED)
async def create_register(
    payload: RegisterCreate,
    user: Annotated[CurrentUser, Depends(require_permission("registers.create"))],
    service: Annotated[FoundationService, Depends(_service)],
) -> RegisterRead:
    register = await service.create_register(
        tenant_id=_current_tenant_or_400(user),
        fields=payload.model_dump(exclude_none=True),
        created_by=user.user_id,
    )
    return RegisterRead.model_validate(register)


@tenant_router.get("/registers/{register_id}", response_model=RegisterRead)
async def get_register(
    register_id: UUID,
    _user: Annotated[CurrentUser, Depends(current_user)],
    service: Annotated[FoundationService, Depends(_service)],
) -> RegisterRead:
    register = await service.get_register(register_id)
    return RegisterRead.model_validate(register)


@tenant_router.patch("/registers/{register_id}", response_model=RegisterRead)
async def update_register(
    register_id: UUID,
    payload: RegisterUpdate,
    user: Annotated[CurrentUser, Depends(require_permission("registers.update"))],
    service: Annotated[FoundationService, Depends(_service)],
) -> RegisterRead:
    register = await service.update_register(
        register_id,
        fields=payload.model_dump(exclude_none=True),
        updated_by=user.user_id,
    )
    return RegisterRead.model_validate(register)


@tenant_router.delete("/registers/{register_id}", response_model=RegisterRead)
async def delete_register(
    register_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permission("registers.delete"))],
    service: Annotated[FoundationService, Depends(_service)],
) -> RegisterRead:
    register = await service.soft_delete_register(register_id, updated_by=user.user_id)
    return RegisterRead.model_validate(register)


# Suppress unused-import warning for symbols referenced via Depends() factories
_ = AuthenticationError
