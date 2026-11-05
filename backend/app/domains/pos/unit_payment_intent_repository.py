"""Payment plan persistence in a caller-owned transaction."""

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionDeniedError
from app.domains.pos.unit_payment_intent_models import UnitPaymentIntent


class UnitPaymentIntentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_operation(self, tenant_id: UUID, operation_id: UUID) -> None:
        await self.session.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtextextended("
                "'unit-payment-plan:' || CAST(:tenant AS TEXT) || ':' || "
                "CAST(:operation AS TEXT),0))"
            ),
            {"tenant": str(tenant_id), "operation": str(operation_id)},
        )

    async def get(self, tenant_id: UUID, operation_id: UUID) -> UnitPaymentIntent | None:
        intent: UnitPaymentIntent | None = await self.session.scalar(
            select(UnitPaymentIntent)
            .where(UnitPaymentIntent.tenant_id == tenant_id, UnitPaymentIntent.id == operation_id)
            .execution_options(populate_existing=True)
        )
        return intent

    async def claim(self, intent: UnitPaymentIntent) -> UnitPaymentIntent:
        values = {
            column.name: getattr(intent, column.name)
            for column in UnitPaymentIntent.__table__.columns
            if column.name != "created_at"
        }
        result = await self.session.scalar(
            insert(UnitPaymentIntent)
            .values(values)
            .on_conflict_do_nothing(index_elements=["tenant_id", "id"])
            .returning(UnitPaymentIntent)
        )
        if result is not None:
            return result
        existing = await self.get(intent.tenant_id, intent.id)
        if existing is None:
            raise PermissionDeniedError("Payment plan operation access denied")
        return existing
