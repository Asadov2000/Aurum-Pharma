"""Persist checkout payment facts in the caller's existing transaction."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.unit_checkout_payment_models import UnitCheckoutPaymentComponent


class UnitCheckoutPaymentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def insert(self, component: UnitCheckoutPaymentComponent) -> None:
        self.session.add(component)
        await self.session.flush()

    async def for_checkout(
        self, tenant_id: UUID, checkout_id: UUID, *, mixed: bool
    ) -> tuple[UnitCheckoutPaymentComponent, ...]:
        parent = (
            UnitCheckoutPaymentComponent.mixed_checkout_id
            if mixed
            else UnitCheckoutPaymentComponent.physical_checkout_id
        )
        rows = await self.session.scalars(
            select(UnitCheckoutPaymentComponent).where(
                UnitCheckoutPaymentComponent.tenant_id == tenant_id,
                parent == checkout_id,
            )
        )
        return tuple(rows)
