"""Database access for isolated unit cart drafts; caller owns the transaction."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import exists, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionDeniedError
from app.domains.catalog.models import TenantCatalog
from app.domains.pos.models import POSPaymentAttempt, Sale, SaleItem, SalePayment, Shift
from app.domains.pos.unit_cart_models import UnitCartItem


class UnitCartRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def assert_actor(
        self, tenant_id: UUID, actor_id: UUID, branch_id: UUID | None = None
    ) -> None:
        branch_clause = (
            ""
            if branch_id is None
            else " AND public.tenant_actor_has_scoped_permission(:tenant,'pos.sell',:branch)"
        )
        allowed = await self.session.scalar(
            text(
                "SELECT current_user='aurum_app' AND session_user='aurum_app' "
                "AND NOT public.is_support_session() "
                "AND public.current_app_user_id()=:actor "
                "AND public.current_tenant_id()=:tenant" + branch_clause
            ),
            {"tenant": tenant_id, "actor": actor_id, "branch": branch_id},
        )
        if allowed is not True:
            raise PermissionDeniedError("Unit cart access denied")

    async def lock_sale(self, tenant_id: UUID, sale_id: UUID) -> Sale | None:
        sale: Sale | None = await self.session.scalar(
            select(Sale)
            .where(Sale.tenant_id == tenant_id, Sale.id == sale_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return sale

    async def lock_shift(self, tenant_id: UUID, shift_id: UUID) -> Shift | None:
        shift: Shift | None = await self.session.scalar(
            select(Shift)
            .where(Shift.tenant_id == tenant_id, Shift.id == shift_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return shift

    async def active_register(self, tenant_id: UUID, branch_id: UUID, register_id: UUID) -> bool:
        result = await self.session.scalar(
            text(
                "SELECT is_active FROM public.register WHERE tenant_id=:tenant "
                "AND branch_id=:branch AND id=:register FOR SHARE"
            ),
            {"tenant": tenant_id, "branch": branch_id, "register": register_id},
        )
        return result is True

    async def has_payment_activity(self, tenant_id: UUID, sale_id: UUID) -> bool:
        result = await self.session.scalar(
            select(
                exists().where(SalePayment.tenant_id == tenant_id, SalePayment.sale_id == sale_id)
                | exists().where(
                    POSPaymentAttempt.tenant_id == tenant_id, POSPaymentAttempt.sale_id == sale_id
                )
            )
        )
        return result is True

    async def lock_catalog(self, tenant_id: UUID, catalog_id: UUID) -> TenantCatalog | None:
        catalog: TenantCatalog | None = await self.session.scalar(
            select(TenantCatalog)
            .where(TenantCatalog.tenant_id == tenant_id, TenantCatalog.id == catalog_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        return catalog

    async def get_item(self, tenant_id: UUID, item_id: UUID) -> UnitCartItem | None:
        item: UnitCartItem | None = await self.session.scalar(
            select(UnitCartItem)
            .where(UnitCartItem.tenant_id == tenant_id, UnitCartItem.id == item_id)
            .execution_options(populate_existing=True)
        )
        return item

    async def list_items(self, tenant_id: UUID, sale_id: UUID) -> tuple[UnitCartItem, ...]:
        return tuple(
            await self.session.scalars(
                select(UnitCartItem)
                .where(UnitCartItem.tenant_id == tenant_id, UnitCartItem.sale_id == sale_id)
                .order_by(UnitCartItem.position)
                .execution_options(populate_existing=True)
            )
        )

    async def save_item(self, item: UnitCartItem) -> None:
        self.session.add(item)
        await self.session.flush()

    async def list_legacy_items(self, tenant_id: UUID, sale_id: UUID) -> tuple[SaleItem, ...]:
        # The finalized-sale child guard locks Sale FOR SHARE for every write.
        # The caller's Sale FOR UPDATE therefore protects both rows and inserts.
        return tuple(
            await self.session.scalars(
                select(SaleItem)
                .where(SaleItem.tenant_id == tenant_id, SaleItem.sale_id == sale_id)
                .order_by(SaleItem.position, SaleItem.id)
                .execution_options(populate_existing=True)
            )
        )

    async def delete_item(self, item: UnitCartItem) -> None:
        await self.session.delete(item)
        await self.session.flush()
