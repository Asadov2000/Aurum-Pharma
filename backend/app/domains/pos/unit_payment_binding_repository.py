"""Caller-owned persistence for plan activation and terminal cancellation."""

from typing import TypeVar
from uuid import UUID

from sqlalchemy import and_, exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionDeniedError
from app.domains.pos.models import POSPaymentAttempt, SalePayment
from app.domains.pos.unit_payment_binding_models import UnitPaymentBinding, UnitPaymentBindingCancel
from app.domains.pos.unit_payment_intent_models import UnitPaymentIntent
from app.domains.pos.unit_payment_intent_repository import UnitPaymentIntentRepository

Binding = TypeVar("Binding", UnitPaymentBinding, UnitPaymentBindingCancel)


class UnitPaymentBindingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lock_operation(self, tenant_id: UUID, operation_id: UUID) -> None:
        await UnitPaymentIntentRepository(self.session).lock_operation(tenant_id, operation_id)

    async def get_state(
        self, tenant_id: UUID, operation_id: UUID
    ) -> tuple[UnitPaymentBinding | None, UnitPaymentBindingCancel | None]:
        row = (
            await self.session.execute(
                select(UnitPaymentBinding, UnitPaymentBindingCancel)
                .select_from(UnitPaymentIntent)
                .outerjoin(
                    UnitPaymentBinding,
                    and_(
                        UnitPaymentBinding.tenant_id == UnitPaymentIntent.tenant_id,
                        UnitPaymentBinding.id == UnitPaymentIntent.id,
                    ),
                )
                .outerjoin(
                    UnitPaymentBindingCancel,
                    and_(
                        UnitPaymentBindingCancel.tenant_id == UnitPaymentIntent.tenant_id,
                        UnitPaymentBindingCancel.id == UnitPaymentIntent.id,
                    ),
                )
                .where(
                    UnitPaymentIntent.tenant_id == tenant_id, UnitPaymentIntent.id == operation_id
                )
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        return (row[0], row[1]) if row is not None else (None, None)

    async def has_financial_activity(self, tenant_id: UUID, sale_id: UUID) -> bool:
        result = await self.session.scalar(
            select(
                exists().where(SalePayment.tenant_id == tenant_id, SalePayment.sale_id == sale_id)
                | exists().where(
                    POSPaymentAttempt.tenant_id == tenant_id, POSPaymentAttempt.sale_id == sale_id
                )
            )
        )
        return result is True

    async def get_binding(self, tenant_id: UUID, operation_id: UUID) -> UnitPaymentBinding | None:
        return await self._get(UnitPaymentBinding, tenant_id, operation_id)

    async def get_cancel(
        self, tenant_id: UUID, operation_id: UUID
    ) -> UnitPaymentBindingCancel | None:
        return await self._get(UnitPaymentBindingCancel, tenant_id, operation_id)

    async def _get(
        self, model: type[Binding], tenant_id: UUID, operation_id: UUID
    ) -> Binding | None:
        result: Binding | None = await self.session.scalar(
            select(model)
            .where(model.tenant_id == tenant_id, model.id == operation_id)
            .execution_options(populate_existing=True)
        )
        return result

    async def claim_binding(self, value: UnitPaymentBinding) -> UnitPaymentBinding:
        return await self._claim(UnitPaymentBinding, value)

    async def claim_cancel(self, value: UnitPaymentBindingCancel) -> UnitPaymentBindingCancel:
        return await self._claim(UnitPaymentBindingCancel, value)

    async def _claim(self, model: type[Binding], value: Binding) -> Binding:
        result: Binding | None = await self.session.scalar(
            insert(model)
            .values(
                tenant_id=value.tenant_id,
                id=value.id,
                sale_id=value.sale_id,
                branch_id=value.branch_id,
                cashier_user_id=value.cashier_user_id,
                created_by=value.created_by,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "id"])
            .returning(model)
        )
        if result is not None:
            return result
        existing = await self._get(model, value.tenant_id, value.id)
        if existing is None:
            raise PermissionDeniedError("Payment binding operation access denied")
        return existing
