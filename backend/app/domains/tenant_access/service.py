"""Policy revisions and bounded temporary restrictions."""

from datetime import UTC, datetime
from uuid import UUID

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.domains.tenant_access.models import TenantAccessPolicy
from app.domains.tenant_access.repository import TenantAccessRepository
from app.domains.tenant_access.schemas import AccessPolicyUpdate


class TenantAccessService:
    def __init__(self, repository: TenantAccessRepository) -> None:
        self.repository = repository

    async def get(self, tenant_id: UUID) -> TenantAccessPolicy:
        policy = await self.repository.get(tenant_id)
        if policy is None:
            raise NotFoundError("Политика доступа аптеки не найдена")
        return policy

    async def update(self, tenant_id: UUID, payload: AccessPolicyUpdate) -> TenantAccessPolicy:
        if payload.expires_at is not None and payload.expires_at <= datetime.now(UTC):
            raise ValidationError("Срок ограничения должен находиться в будущем")
        await self.repository.lock(tenant_id)
        policy = await self.get(tenant_id)
        if policy.version != payload.expected_version:
            raise ConflictError("Ограничения уже изменены. Обновите страницу.")
        supplied = payload.model_fields_set
        if "mode" in supplied:
            policy.mode = payload.mode
        if "blocked_modules" in supplied:
            policy.blocked_modules = list(payload.blocked_modules)
        if "max_branches" in supplied:
            policy.max_branches = payload.max_branches
        if "max_users" in supplied:
            policy.max_users = payload.max_users
        if "max_registers" in supplied:
            policy.max_registers = payload.max_registers
        policy.reason = payload.reason
        if "expires_at" in supplied:
            policy.expires_at = payload.expires_at
        policy.version += 1
        return await self.repository.save(policy)
