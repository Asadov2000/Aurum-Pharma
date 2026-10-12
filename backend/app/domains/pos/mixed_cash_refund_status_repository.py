"""One scoped, unlocked statement reads the immutable recovery bindings."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.domains.pos.mixed_cash_checkout_models import MixedCashCheckout
from app.domains.pos.mixed_cash_refund_models import MixedCashRefund
from app.domains.pos.mixed_return_journal_models import MixedReturnCommand
from app.domains.pos.models import Sale, SalePayment


@dataclass(frozen=True)
class MixedCashRefundStatusSnapshot:
    parent: Sale
    checkout: MixedCashCheckout | None
    marker: MixedCashRefund | None
    returned_sale: Sale | None
    payment: SalePayment | None
    journal: MixedReturnCommand | None


class MixedCashRefundStatusRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def read(
        self, tenant_id: UUID, parent_sale_id: UUID, operation_id: UUID
    ) -> MixedCashRefundStatusSnapshot | None:
        returned_sale = aliased(Sale)
        row = (
            await self.session.execute(
                select(
                    Sale,
                    MixedCashCheckout,
                    MixedCashRefund,
                    returned_sale,
                    SalePayment,
                    MixedReturnCommand,
                )
                .select_from(Sale)
                .outerjoin(
                    MixedCashCheckout,
                    and_(
                        MixedCashCheckout.tenant_id == Sale.tenant_id,
                        MixedCashCheckout.sale_id == Sale.id,
                    ),
                )
                .outerjoin(
                    MixedCashRefund,
                    and_(
                        MixedCashRefund.tenant_id == Sale.tenant_id,
                        MixedCashRefund.parent_sale_id == Sale.id,
                        MixedCashRefund.id == operation_id,
                    ),
                )
                .outerjoin(
                    returned_sale,
                    and_(
                        returned_sale.tenant_id == MixedCashRefund.tenant_id,
                        returned_sale.id == MixedCashRefund.sale_id,
                    ),
                )
                .outerjoin(
                    SalePayment,
                    and_(
                        SalePayment.tenant_id == MixedCashRefund.tenant_id,
                        SalePayment.id == MixedCashRefund.payment_id,
                        SalePayment.sale_id == MixedCashRefund.sale_id,
                    ),
                )
                .outerjoin(
                    MixedReturnCommand,
                    and_(
                        MixedReturnCommand.tenant_id == MixedCashRefund.tenant_id,
                        MixedReturnCommand.id == MixedCashRefund.id,
                        MixedReturnCommand.parent_sale_id == Sale.id,
                    ),
                )
                .where(Sale.tenant_id == tenant_id, Sale.id == parent_sale_id)
                .execution_options(populate_existing=True, autoflush=False)
            )
        ).one_or_none()
        if row is None:
            return None
        return MixedCashRefundStatusSnapshot(*row)
