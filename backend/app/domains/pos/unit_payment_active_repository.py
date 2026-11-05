"""One-statement snapshot of a sale and its uncancelled payment preparation."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.models import Sale
from app.domains.pos.unit_payment_binding_models import UnitPaymentBinding, UnitPaymentBindingCancel
from app.domains.pos.unit_payment_intent_models import UnitPaymentIntent


@dataclass(frozen=True)
class ActivePaymentCandidate:
    branch_id: UUID
    cashier_id: UUID
    register_id: UUID
    shift_id: UUID
    intent: UnitPaymentIntent | None
    binding: UnitPaymentBinding | None


class UnitPaymentActiveRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def snapshot(self, tenant_id: UUID, sale_id: UUID) -> list[ActivePaymentCandidate]:
        rows = await self.session.execute(
            select(
                Sale.branch_id,
                Sale.cashier_user_id,
                Sale.register_id,
                Sale.shift_id,
                UnitPaymentIntent,
                UnitPaymentBinding,
            )
            .select_from(Sale)
            .outerjoin(
                UnitPaymentBinding,
                and_(
                    UnitPaymentBinding.tenant_id == Sale.tenant_id,
                    UnitPaymentBinding.sale_id == Sale.id,
                    ~exists()
                    .where(
                        UnitPaymentBindingCancel.tenant_id == UnitPaymentBinding.tenant_id,
                        UnitPaymentBindingCancel.id == UnitPaymentBinding.id,
                    )
                    .correlate(UnitPaymentBinding),
                ),
            )
            .outerjoin(
                UnitPaymentIntent,
                and_(
                    UnitPaymentIntent.tenant_id == UnitPaymentBinding.tenant_id,
                    UnitPaymentIntent.id == UnitPaymentBinding.id,
                ),
            )
            .where(Sale.tenant_id == tenant_id, Sale.id == sale_id)
            .limit(2)
            .execution_options(populate_existing=True)
        )
        # Select scalar sale fields, preserving unsaved ORM edits in the caller's session.
        return [ActivePaymentCandidate(*row) for row in rows]
