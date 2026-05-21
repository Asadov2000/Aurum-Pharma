"""Business logic for the foundation domain.

Key invariants enforced here:
- Creating a tenant always seeds its tenant_settings with platform defaults.
- A tenant must keep at least one active branch — soft-deleting / deactivating
  the last one is rejected.
- A register may only point to a branch that belongs to the same tenant.
- TODO(pos): once shifts exist, refuse to delete a branch that has open shifts.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import structlog

from app.core.errors import BusinessRuleError, NotFoundError
from app.core.time import utc_now
from app.domains.foundation.models import Branch, Register, Tenant, TenantSettings
from app.domains.foundation.repository import FoundationRepository

logger = structlog.get_logger("foundation.service")

TRIAL_DURATION = timedelta(days=14)


class FoundationService:
    def __init__(self, repo: FoundationRepository) -> None:
        self.repo = repo

    # -------------------------------------------------------------------------
    # Tenants (support-only)
    # -------------------------------------------------------------------------

    async def create_tenant(self, *, payload: dict[str, object]) -> Tenant:
        tenant = await self.repo.create_tenant(**payload)
        await self.repo.create_default_settings(tenant.id)
        # Onboarding hook — wizard + checklist. Lazy import to avoid
        # circular dependencies; the call is idempotent.
        from app.domains.onboarding.repository import OnboardingRepository
        from app.domains.onboarding.service import OnboardingService

        onboarding = OnboardingService(OnboardingRepository(self.repo.session))
        await onboarding.on_tenant_created(tenant.id)

        logger.info("tenant_created", tenant_id=str(tenant.id), name=tenant.name)
        return tenant

    async def list_tenants(self, *, limit: int = 100, offset: int = 0) -> list[Tenant]:
        return await self.repo.list_tenants(limit=limit, offset=offset)

    async def get_tenant(self, tenant_id: UUID) -> Tenant:
        tenant = await self.repo.get_tenant(tenant_id)
        if tenant is None:
            raise NotFoundError("Tenant not found")
        return tenant

    async def update_tenant(self, tenant_id: UUID, *, fields: dict[str, object]) -> Tenant:
        tenant = await self.get_tenant(tenant_id)
        # Status transitions: enforce just the most painful invariant —
        # moving to 'trial' fills trial_started_at/trial_ends_at if not set.
        new_status = fields.get("status")
        if new_status == "trial" and tenant.trial_started_at is None:
            now = utc_now()
            fields = {
                **fields,
                "trial_started_at": now,
                "trial_ends_at": now + TRIAL_DURATION,
            }
        return await self.repo.update_tenant(tenant, **fields)

    # -------------------------------------------------------------------------
    # Tenant settings (one row per tenant; auto-created on tenant creation)
    # -------------------------------------------------------------------------

    async def get_settings(self, tenant_id: UUID) -> TenantSettings:
        settings = await self.repo.get_settings(tenant_id)
        if settings is None:
            raise NotFoundError("Settings not found for tenant")
        return settings

    async def update_settings(
        self,
        tenant_id: UUID,
        *,
        fields: dict[str, object],
        updated_by: UUID | None = None,
    ) -> TenantSettings:
        settings = await self.get_settings(tenant_id)
        if updated_by is not None:
            fields = {**fields, "updated_by": updated_by}
        return await self.repo.update_settings(settings, **fields)

    # -------------------------------------------------------------------------
    # Branches
    # -------------------------------------------------------------------------

    async def create_branch(
        self,
        *,
        tenant_id: UUID,
        fields: dict[str, object],
        created_by: UUID | None = None,
    ) -> Branch:
        fields = {**fields, "tenant_id": tenant_id}
        if created_by is not None:
            fields["created_by"] = created_by
        return await self.repo.create_branch(**fields)

    async def list_branches(self, *, include_inactive: bool = False) -> list[Branch]:
        return await self.repo.list_branches(include_inactive=include_inactive)

    async def get_branch(self, branch_id: UUID) -> Branch:
        branch = await self.repo.get_branch(branch_id)
        if branch is None:
            raise NotFoundError("Branch not found")
        return branch

    async def update_branch(
        self,
        branch_id: UUID,
        *,
        fields: dict[str, object],
        updated_by: UUID | None = None,
    ) -> Branch:
        branch = await self.get_branch(branch_id)
        # Guard the "last active branch" rule on deactivation only.
        if fields.get("is_active") is False and branch.is_active:
            active = await self.repo.count_active_branches(branch.tenant_id)
            if active <= 1:
                raise BusinessRuleError("Cannot deactivate the last active branch of the tenant")
        if updated_by is not None:
            fields = {**fields, "updated_by": updated_by}
        return await self.repo.update_branch(branch, **fields)

    async def soft_delete_branch(
        self, branch_id: UUID, *, updated_by: UUID | None = None
    ) -> Branch:
        # TODO(pos): forbid deletion when the branch has open shifts.
        return await self.update_branch(
            branch_id, fields={"is_active": False}, updated_by=updated_by
        )

    # -------------------------------------------------------------------------
    # Registers
    # -------------------------------------------------------------------------

    async def create_register(
        self,
        *,
        tenant_id: UUID,
        fields: dict[str, object],
        created_by: UUID | None = None,
    ) -> Register:
        branch_id = fields.get("branch_id")
        if not isinstance(branch_id, UUID):
            raise BusinessRuleError("branch_id is required and must be a UUID")
        branch = await self.repo.get_branch(branch_id)
        if branch is None or branch.tenant_id != tenant_id:
            # Without RLS-bypass the cross-tenant branch would already be
            # invisible (SELECT returns None). The explicit check is here for
            # support-pool callers who can see everything.
            raise BusinessRuleError("Branch does not belong to this tenant")
        if not branch.is_active:
            raise BusinessRuleError("Branch is inactive")
        payload = {**fields, "tenant_id": tenant_id}
        if created_by is not None:
            payload["created_by"] = created_by
        return await self.repo.create_register(**payload)

    async def list_registers(
        self,
        *,
        branch_id: UUID | None = None,
        include_inactive: bool = False,
    ) -> list[Register]:
        return await self.repo.list_registers(
            branch_id=branch_id, include_inactive=include_inactive
        )

    async def get_register(self, register_id: UUID) -> Register:
        register = await self.repo.get_register(register_id)
        if register is None:
            raise NotFoundError("Register not found")
        return register

    async def update_register(
        self,
        register_id: UUID,
        *,
        fields: dict[str, object],
        updated_by: UUID | None = None,
    ) -> Register:
        register = await self.get_register(register_id)
        if updated_by is not None:
            fields = {**fields, "updated_by": updated_by}
        return await self.repo.update_register(register, **fields)

    async def soft_delete_register(
        self, register_id: UUID, *, updated_by: UUID | None = None
    ) -> Register:
        return await self.update_register(
            register_id, fields={"is_active": False}, updated_by=updated_by
        )
