"""Locked database facts for physical cart posting; no transaction ownership."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.domains.inventory.unit_ledger_repository import UnitLedgerRepository
from app.domains.inventory.unit_ledger_v2_models import (
    UnitStockCartCommandV2,
    UnitStockCartLineV2,
    UnitStockLotV2,
    UnitStockMovementV2,
    UnitStockOpenedPackV2,
)


class UnitDispensingV2Repository(UnitLedgerRepository):
    async def cart_command(
        self, tenant_id: UUID, command_id: UUID
    ) -> UnitStockCartCommandV2 | None:
        return (
            await self.session.scalars(
                select(UnitStockCartCommandV2).where(
                    UnitStockCartCommandV2.tenant_id == tenant_id,
                    UnitStockCartCommandV2.id == command_id,
                )
            )
        ).one_or_none()

    async def claim_cart(
        self, command: UnitStockCartCommandV2
    ) -> tuple[UnitStockCartCommandV2, bool]:
        # A concurrent replay can collide with either composite identity index.
        claimed = await self.session.scalar(
            insert(UnitStockCartCommandV2)
            .values(
                tenant_id=command.tenant_id,
                id=command.id,
                branch_id=command.branch_id,
                actor_id=command.actor_id,
                request_hash=command.request_hash,
                created_by=command.created_by,
            )
            .on_conflict_do_nothing()
            .returning(UnitStockCartCommandV2.id)
        )
        result = (
            await self.session.scalars(
                select(UnitStockCartCommandV2).where(
                    UnitStockCartCommandV2.tenant_id == command.tenant_id,
                    UnitStockCartCommandV2.id == command.id,
                )
            )
        ).one_or_none()
        if result is None:
            raise RuntimeError("Physical cart claim did not resolve")
        return result, claimed is not None

    async def cart_lines(self, tenant_id: UUID, command_id: UUID) -> list[UnitStockCartLineV2]:
        return list(
            await self.session.scalars(
                select(UnitStockCartLineV2)
                .where(
                    UnitStockCartLineV2.tenant_id == tenant_id,
                    UnitStockCartLineV2.command_id == command_id,
                )
                .order_by(UnitStockCartLineV2.position)
            )
        )

    async def movements(self, tenant_id: UUID, command_id: UUID) -> list[UnitStockMovementV2]:
        return list(
            await self.session.scalars(
                select(UnitStockMovementV2)
                .where(
                    UnitStockMovementV2.tenant_id == tenant_id,
                    UnitStockMovementV2.command_id == command_id,
                )
                .order_by(UnitStockMovementV2.entry_order)
                .execution_options(populate_existing=True)
            )
        )

    async def lock_physical_lots(
        self, tenant_id: UUID, branch_id: UUID, catalog_ids: list[UUID]
    ) -> list[UnitStockLotV2]:
        return list(
            await self.session.scalars(
                select(UnitStockLotV2)
                .where(
                    UnitStockLotV2.tenant_id == tenant_id,
                    UnitStockLotV2.branch_id == branch_id,
                    UnitStockLotV2.catalog_id.in_(catalog_ids),
                )
                .order_by(UnitStockLotV2.id)
                .with_for_update(key_share=True)
                .execution_options(populate_existing=True)
            )
        )

    async def lock_physical_packs(
        self, tenant_id: UUID, lot_ids: list[UUID]
    ) -> list[UnitStockOpenedPackV2]:
        if not lot_ids:
            return []
        return list(
            await self.session.scalars(
                select(UnitStockOpenedPackV2)
                .where(
                    UnitStockOpenedPackV2.tenant_id == tenant_id,
                    UnitStockOpenedPackV2.lot_id.in_(lot_ids),
                    UnitStockOpenedPackV2.remaining_units > 0,
                )
                .order_by(UnitStockOpenedPackV2.id)
                .with_for_update(key_share=True)
                .execution_options(populate_existing=True)
            )
        )

    async def insert_cart_lines(self, lines: list[UnitStockCartLineV2]) -> None:
        self.session.add_all(lines)
        await self.session.flush()

    async def insert_movement(self, movement: UnitStockMovementV2) -> None:
        self.session.add(movement)
        await self.session.flush()
