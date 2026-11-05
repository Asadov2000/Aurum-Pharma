"""One statement snapshot for cash recovery; no row locks or financial writes."""

from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.mixed_cash_checkout_models import MixedCashCheckout
from app.domains.pos.models import Sale
from app.domains.pos.unit_cash_intent_models import UnitCashIntent


class MixedCashCheckoutStatusRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def read(
        self, tenant_id: UUID, sale_id: UUID, operation_id: UUID
    ) -> tuple[Sale, UnitCashIntent | None, MixedCashCheckout | None] | None:
        row = (
            await self.session.execute(
                select(Sale, UnitCashIntent, MixedCashCheckout)
                .outerjoin(
                    UnitCashIntent,
                    and_(
                        UnitCashIntent.tenant_id == Sale.tenant_id,
                        UnitCashIntent.sale_id == Sale.id,
                        UnitCashIntent.id == operation_id,
                    ),
                )
                .outerjoin(
                    MixedCashCheckout,
                    and_(
                        MixedCashCheckout.tenant_id == Sale.tenant_id,
                        MixedCashCheckout.sale_id == Sale.id,
                        MixedCashCheckout.id == operation_id,
                    ),
                )
                .where(Sale.tenant_id == tenant_id, Sale.id == sale_id)
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        if row is None:
            return None
        return row[0], row[1], row[2]
