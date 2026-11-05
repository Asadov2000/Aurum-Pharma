"""Locked received-stock snapshots; quantities and financial history stay unchanged."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.catalog.models import TenantCatalog
from app.domains.inventory.unit_stock import OpenedPack, UnitPackaging, UnitStockBatch
from app.domains.inventory.unit_stock_v2 import UnitStockBatchV2


class UnitCheckoutRepository:
    def __init__(self, session: AsyncSession) -> None:
        # The incoming package eagerly loads its router and imports the ledger;
        # construct this dependency lazily to avoid a module import cycle.
        from app.domains.inventory.unit_ledger_repository import UnitLedgerRepository

        self.session = session
        self.stock = UnitLedgerRepository(session)

    async def lock_physical_batches(
        self, tenant_id: UUID, branch_id: UUID, catalog_ids: list[UUID]
    ) -> tuple[UnitStockBatchV2, ...]:
        from app.domains.inventory.unit_dispensing_v2_repository import UnitDispensingV2Repository

        return await UnitDispensingV2Repository(self.session).lock_stock_snapshots(
            tenant_id, branch_id, catalog_ids
        )

    async def lock_checkout_catalog(
        self, tenant_id: UUID, catalog_id: UUID
    ) -> TenantCatalog | None:
        """Fail fast on contention, including inherited quote SHARE locks.

        Retrying requires a new caller transaction so earlier SHARE locks are
        released. NOWAIT prevents two composed quote/prepare calls from waiting
        for each other's catalog lock upgrade.
        """
        catalog: TenantCatalog | None = await self.session.scalar(
            select(TenantCatalog)
            .where(TenantCatalog.tenant_id == tenant_id, TenantCatalog.id == catalog_id)
            .with_for_update(key_share=True, nowait=True)
            .execution_options(populate_existing=True)
        )
        return catalog

    async def lock_received_batches(
        self,
        tenant_id: UUID,
        branch_id: UUID,
        catalog_id: UUID,
    ) -> tuple[UnitStockBatch, ...]:
        lots = await self.stock.lock_lots(tenant_id, branch_id, catalog_id, received_only=True)
        packs = await self.stock.lock_packs(tenant_id, [lot.id for lot in lots])
        opened: dict[UUID, list[OpenedPack]] = {}
        for pack in packs:
            opened.setdefault(pack.lot_id, []).append(
                OpenedPack(
                    id=pack.id,
                    opened_at=pack.opened_at,
                    remaining_units=pack.remaining_units,
                )
            )
        return tuple(
            UnitStockBatch(
                id=lot.id,
                tenant_id=lot.tenant_id,
                branch_id=lot.branch_id,
                catalog_id=lot.catalog_id,
                expires_at=lot.expires_at,
                created_at=lot.created_at,
                packaging=UnitPackaging(
                    version=lot.version,
                    unit=lot.unit,
                    units_per_pack=lot.units_per_pack,
                    dispense_step=lot.dispense_step,
                ),
                closed_packs=lot.closed_packs,
                is_blocked=lot.is_blocked,
                opened_packs=tuple(opened.get(lot.id, ())),
            )
            for lot in lots
        )
