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

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import (
    CurrentUser,
    current_user,
    ensure_platform_capability,
    get_db,
    require_any_permission,
    require_permission,
    require_platform_capability,
    require_recent_platform_capability,
)
from app.core.errors import AuthenticationError, BusinessRuleError, PermissionDeniedError
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.schemas import (
    BranchCreate,
    BranchRead,
    BranchSearchRequest,
    BranchSearchResponse,
    BranchUpdate,
    OwnerCreate,
    OwnerProvisionRead,
    RegisterCreate,
    RegisterRead,
    RegisterSearchRequest,
    RegisterSearchResponse,
    RegisterUpdate,
    TenantCreate,
    TenantRead,
    TenantSettingsRead,
    TenantSettingsUpdate,
    TenantUpdate,
)
from app.domains.foundation.service import FoundationService
from app.domains.roles.repository import RolesRepository
from app.domains.roles.schemas import TenantAccountCreate, TenantMembershipRead
from app.domains.roles.service import RolesService

BRANCH_DISCOVERY_PERMISSIONS = (
    "branches.view",
    "registers.view",
    "pos.shift_open",
    "pos.shift_close",
    "pos.sell",
    # Incoming operators need to resolve the points assigned to their
    # incoming capability when creating or reviewing a document.
    "incoming.view",
    "incoming.create",
    "batches.view",
    "reports.view",
)
REGISTER_DISCOVERY_PERMISSIONS = (
    "registers.view",
    "pos.shift_open",
    "pos.shift_close",
    "pos.sell",
    "reports.view",
)


async def _service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> FoundationService:
    return FoundationService(FoundationRepository(db))


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


def _set_search_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Pragma"] = "no-cache"


# =============================================================================
# Admin router — /api/v1/admin/...
# =============================================================================

admin_router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


@admin_router.post(
    "/tenants",
    response_model=TenantRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_recent_platform_capability("platform.tenants.manage"))],
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
    dependencies=[Depends(require_platform_capability("platform.tenants.view"))],
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
    dependencies=[Depends(require_platform_capability("platform.tenants.view"))],
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
)
async def update_tenant(
    tenant_id: UUID,
    payload: TenantUpdate,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.tenants.manage")),
    ],
    service: Annotated[FoundationService, Depends(_service)],
) -> TenantRead:
    if payload.status is not None:
        ensure_platform_capability(user, "platform.billing.manage")
    tenant = await service.update_tenant(tenant_id, fields=payload.model_dump(exclude_none=True))
    return TenantRead.model_validate(tenant)


@admin_router.post(
    "/tenants/{tenant_id}/members",
    response_model=TenantMembershipRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_tenant_membership(
    tenant_id: UUID,
    payload: TenantAccountCreate,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.memberships.manage")),
    ],
    service: Annotated[FoundationService, Depends(_service)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> TenantMembershipRead:
    """Create a pending tenant account and membership without a role."""
    tenant = await service.get_tenant(tenant_id)
    if tenant.status == "archived":
        raise BusinessRuleError(
            "Аптека в архиве — нельзя добавить сотрудника",
            details={"status": tenant.status},
        )
    roles = RolesService(RolesRepository(db))
    account, membership = await roles.create_tenant_account(
        tenant_id=tenant_id,
        email=str(payload.email),
        full_name=payload.full_name,
        phone=payload.phone,
        actor_id=user.user_id,
    )
    return TenantMembershipRead(
        membership_id=membership.id,
        user_id=account.id,
        tenant_id=tenant_id,
        email=account.email,
        full_name=membership.full_name,
        phone=membership.phone,
        status=membership.status,
    )


@admin_router.post(
    "/tenants/{tenant_id}/owner",
    response_model=OwnerProvisionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_tenant_owner(
    tenant_id: UUID,
    payload: OwnerCreate,
    user: Annotated[
        CurrentUser,
        Depends(require_recent_platform_capability("platform.ownership.provision")),
    ],
    service: Annotated[FoundationService, Depends(_service)],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
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
    owner, membership, ownership, role = await roles.provision_owner(
        tenant_id=tenant_id,
        email=str(payload.email),
        full_name=payload.full_name,
        actor_id=user.user_id,
    )
    return OwnerProvisionRead(
        user_id=owner.id,
        membership_id=membership.id,
        ownership_id=ownership.id,
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
    user: Annotated[
        CurrentUser,
        Depends(
            require_any_permission(
                *BRANCH_DISCOVERY_PERMISSIONS,
            )
        ),
    ],
    service: Annotated[FoundationService, Depends(_service)],
    include_inactive: bool = Query(False),
) -> list[BranchRead]:
    branches = await service.list_branches(include_inactive=include_inactive)
    branch_scope = user.branch_scope_for_any(*BRANCH_DISCOVERY_PERMISSIONS)
    if branch_scope is not None:
        branches = [b for b in branches if b.id in branch_scope]
    return [BranchRead.model_validate(b) for b in branches]


@tenant_router.post("/branches/search", response_model=BranchSearchResponse)
async def search_branches(
    payload: BranchSearchRequest,
    response: Response,
    user: Annotated[CurrentUser, Depends(require_permission("branches.view"))],
    service: Annotated[FoundationService, Depends(_service)],
) -> BranchSearchResponse:
    _set_search_no_store(response)
    items, total = await service.search_branches(
        tenant_id=_current_tenant_or_400(user),
        q=payload.q,
        branch_type=payload.branch_type,
        is_active=payload.is_active,
        allowed_branch_ids=user.branch_scope_for("branches.view"),
        page=payload.page,
        page_size=payload.page_size,
    )
    return BranchSearchResponse(
        items=[BranchRead.model_validate(item) for item in items],
        total=total,
        page=payload.page,
        page_size=payload.page_size,
    )


@tenant_router.post("/branches", response_model=BranchRead, status_code=status.HTTP_201_CREATED)
async def create_branch(
    payload: BranchCreate,
    user: Annotated[CurrentUser, Depends(require_permission("branches.create"))],
    service: Annotated[FoundationService, Depends(_service)],
) -> BranchRead:
    if not user.has_tenant_scope("branches.create"):
        raise PermissionDeniedError("Tenant branch access required")
    branch = await service.create_branch(
        tenant_id=_current_tenant_or_400(user),
        fields=payload.model_dump(exclude_none=True),
        created_by=user.user_id,
    )
    return BranchRead.model_validate(branch)


@tenant_router.get("/branches/{branch_id}", response_model=BranchRead)
async def get_branch(
    branch_id: UUID,
    user: Annotated[
        CurrentUser,
        Depends(
            require_any_permission(
                *BRANCH_DISCOVERY_PERMISSIONS,
            )
        ),
    ],
    service: Annotated[FoundationService, Depends(_service)],
) -> BranchRead:
    branch = await service.get_branch(branch_id)
    if not user.can_access_branch_for_any(branch.id, *BRANCH_DISCOVERY_PERMISSIONS):
        raise PermissionDeniedError("Branch access denied")
    return BranchRead.model_validate(branch)


@tenant_router.patch("/branches/{branch_id}", response_model=BranchRead)
async def update_branch(
    branch_id: UUID,
    payload: BranchUpdate,
    user: Annotated[CurrentUser, Depends(require_permission("branches.update"))],
    service: Annotated[FoundationService, Depends(_service)],
) -> BranchRead:
    if not user.can_access_branch("branches.update", branch_id):
        raise PermissionDeniedError("Branch access denied")
    branch = await service.update_branch(
        branch_id,
        fields=payload.model_dump(exclude_unset=True),
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
    if not user.can_access_branch("branches.delete", branch_id):
        raise PermissionDeniedError("Branch access denied")
    branch = await service.soft_delete_branch(branch_id, updated_by=user.user_id)
    return BranchRead.model_validate(branch)


# ---- registers --------------------------------------------------------------


@tenant_router.get("/registers", response_model=list[RegisterRead])
async def list_registers(
    user: Annotated[
        CurrentUser,
        Depends(
            require_any_permission(
                *REGISTER_DISCOVERY_PERMISSIONS,
            )
        ),
    ],
    service: Annotated[FoundationService, Depends(_service)],
    branch_id: Annotated[UUID | None, Query()] = None,
    include_inactive: Annotated[bool, Query()] = False,
) -> list[RegisterRead]:
    branch_scope = user.branch_scope_for_any(*REGISTER_DISCOVERY_PERMISSIONS)
    if branch_scope is not None and branch_id is not None and branch_id not in branch_scope:
        return []
    registers = await service.list_registers(branch_id=branch_id, include_inactive=include_inactive)
    if branch_scope is not None:
        registers = [r for r in registers if r.branch_id in branch_scope]
    return [RegisterRead.model_validate(r) for r in registers]


@tenant_router.post("/registers/search", response_model=RegisterSearchResponse)
async def search_registers(
    payload: RegisterSearchRequest,
    response: Response,
    user: Annotated[CurrentUser, Depends(require_permission("registers.view"))],
    service: Annotated[FoundationService, Depends(_service)],
) -> RegisterSearchResponse:
    _set_search_no_store(response)
    items, total = await service.search_registers(
        tenant_id=_current_tenant_or_400(user),
        q=payload.q,
        branch_id=payload.branch_id,
        printer_type=payload.printer_type,
        is_active=payload.is_active,
        allowed_branch_ids=user.branch_scope_for("registers.view"),
        page=payload.page,
        page_size=payload.page_size,
    )
    return RegisterSearchResponse(
        items=[RegisterRead.model_validate(item) for item in items],
        total=total,
        page=payload.page,
        page_size=payload.page_size,
    )


@tenant_router.post("/registers", response_model=RegisterRead, status_code=status.HTTP_201_CREATED)
async def create_register(
    payload: RegisterCreate,
    user: Annotated[CurrentUser, Depends(require_permission("registers.create"))],
    service: Annotated[FoundationService, Depends(_service)],
) -> RegisterRead:
    if not user.can_access_branch("registers.create", payload.branch_id):
        raise PermissionDeniedError("Branch access denied")
    register = await service.create_register(
        tenant_id=_current_tenant_or_400(user),
        fields=payload.model_dump(exclude_none=True),
        created_by=user.user_id,
    )
    return RegisterRead.model_validate(register)


@tenant_router.get("/registers/{register_id}", response_model=RegisterRead)
async def get_register(
    register_id: UUID,
    user: Annotated[
        CurrentUser,
        Depends(
            require_any_permission(
                *REGISTER_DISCOVERY_PERMISSIONS,
            )
        ),
    ],
    service: Annotated[FoundationService, Depends(_service)],
) -> RegisterRead:
    register = await service.get_register(register_id)
    if not user.can_access_branch_for_any(
        register.branch_id,
        *REGISTER_DISCOVERY_PERMISSIONS,
    ):
        raise PermissionDeniedError("Register access denied")
    return RegisterRead.model_validate(register)


@tenant_router.patch("/registers/{register_id}", response_model=RegisterRead)
async def update_register(
    register_id: UUID,
    payload: RegisterUpdate,
    user: Annotated[CurrentUser, Depends(require_permission("registers.update"))],
    service: Annotated[FoundationService, Depends(_service)],
) -> RegisterRead:
    existing = await service.get_register(register_id)
    if not user.can_access_branch("registers.update", existing.branch_id):
        raise PermissionDeniedError("Register access denied")
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
    existing = await service.get_register(register_id)
    if not user.can_access_branch("registers.delete", existing.branch_id):
        raise PermissionDeniedError("Register access denied")
    register = await service.soft_delete_register(register_id, updated_by=user.user_id)
    return RegisterRead.model_validate(register)


# Suppress unused-import warning for symbols referenced via Depends() factories
_ = AuthenticationError
