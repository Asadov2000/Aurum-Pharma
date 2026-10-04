"""Cash intent persistence in a caller-owned transaction."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionDeniedError
from app.domains.pos.unit_cash_intent_models import UnitCashIntent


class UnitCashIntentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, tenant_id: UUID, operation_id: UUID) -> UnitCashIntent | None:
        intent: UnitCashIntent | None = await self.session.scalar(
            select(UnitCashIntent)
            .where(UnitCashIntent.tenant_id == tenant_id, UnitCashIntent.id == operation_id)
            .execution_options(populate_existing=True)
        )
        return intent

    async def claim(self, intent: UnitCashIntent) -> UnitCashIntent:
        values = {
            column.name: getattr(intent, column.name)
            for column in UnitCashIntent.__table__.columns
            if column.name != "created_at"
        }
        result = await self.session.scalar(
            insert(UnitCashIntent)
            .values(values)
            .on_conflict_do_nothing(index_elements=["tenant_id", "id"])
            .returning(UnitCashIntent)
        )
        if result is not None:
            return result
        existing = await self.get(intent.tenant_id, intent.id)
        if existing is None:
            raise PermissionDeniedError("Cash confirmation operation access denied")
        return existing
