"""Caller-owned persistence for exclusive refund review preparation and cancellation."""

from typing import TypeVar
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionDeniedError
from app.domains.pos.unit_electronic_refund_binding_models import (
    UnitElectronicRefundBinding,
    UnitElectronicRefundBindingCancel,
)
from app.domains.pos.unit_electronic_refund_intent_models import UnitElectronicRefundIntent
from app.domains.pos.unit_electronic_refund_intent_repository import (
    UnitElectronicRefundIntentRepository,
)

Binding = TypeVar("Binding", UnitElectronicRefundBinding, UnitElectronicRefundBindingCancel)


class UnitElectronicRefundBindingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def is_read_committed(self) -> bool:
        return await UnitElectronicRefundIntentRepository(self.session).is_read_committed()

    async def lock_operation(self, tenant_id: UUID, operation_id: UUID) -> None:
        await UnitElectronicRefundIntentRepository(self.session).lock_operation(
            tenant_id, operation_id
        )

    async def get_state(
        self, tenant_id: UUID, operation_id: UUID
    ) -> tuple[UnitElectronicRefundBinding | None, UnitElectronicRefundBindingCancel | None]:
        row = (
            await self.session.execute(
                select(UnitElectronicRefundBinding, UnitElectronicRefundBindingCancel)
                .select_from(UnitElectronicRefundIntent)
                .outerjoin(
                    UnitElectronicRefundBinding,
                    and_(
                        UnitElectronicRefundBinding.tenant_id
                        == UnitElectronicRefundIntent.tenant_id,
                        UnitElectronicRefundBinding.id == UnitElectronicRefundIntent.id,
                    ),
                )
                .outerjoin(
                    UnitElectronicRefundBindingCancel,
                    and_(
                        UnitElectronicRefundBindingCancel.tenant_id
                        == UnitElectronicRefundIntent.tenant_id,
                        UnitElectronicRefundBindingCancel.id == UnitElectronicRefundIntent.id,
                    ),
                )
                .where(
                    UnitElectronicRefundIntent.tenant_id == tenant_id,
                    UnitElectronicRefundIntent.id == operation_id,
                )
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        return (row[0], row[1]) if row is not None else (None, None)

    async def _get(
        self, model: type[Binding], tenant_id: UUID, operation_id: UUID
    ) -> Binding | None:
        result: Binding | None = await self.session.scalar(
            select(model)
            .where(model.tenant_id == tenant_id, model.id == operation_id)
            .execution_options(populate_existing=True)
        )
        return result

    async def claim_binding(
        self, value: UnitElectronicRefundBinding
    ) -> UnitElectronicRefundBinding:
        return await self._claim(UnitElectronicRefundBinding, value)

    async def claim_cancel(
        self, value: UnitElectronicRefundBindingCancel
    ) -> UnitElectronicRefundBindingCancel:
        return await self._claim(UnitElectronicRefundBindingCancel, value)

    async def _claim(self, model: type[Binding], value: Binding) -> Binding:
        result: Binding | None = await self.session.scalar(
            insert(model)
            .values(
                tenant_id=value.tenant_id,
                id=value.id,
                parent_sale_id=value.parent_sale_id,
                branch_id=value.branch_id,
                actor_id=value.actor_id,
                created_by=value.created_by,
            )
            .on_conflict_do_nothing(index_elements=["tenant_id", "id"])
            .returning(model)
        )
        if result is not None:
            return result
        existing = await self._get(model, value.tenant_id, value.id)
        if existing is None:
            raise PermissionDeniedError("Refund preparation operation access denied")
        return existing
