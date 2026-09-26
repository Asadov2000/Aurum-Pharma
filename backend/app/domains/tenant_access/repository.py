"""Access policy persistence."""

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.tenant_access.models import TenantAccessPolicy


class TenantAccessRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def lock(self, tenant_id: UUID) -> None:
        await self.db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended('tenant-access:' || :id, 0))"),
            {"id": str(tenant_id)},
        )

    async def get(self, tenant_id: UUID) -> TenantAccessPolicy | None:
        result = await self.db.scalar(
            select(TenantAccessPolicy)
            .where(TenantAccessPolicy.tenant_id == tenant_id)
            .execution_options(populate_existing=True)
        )
        return result

    async def save(self, policy: TenantAccessPolicy) -> TenantAccessPolicy:
        await self.db.flush()
        await self.db.refresh(policy)
        return policy
