"""Persistence for version-two receiving; the caller owns its transaction."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory.unit_ledger_repository import UnitLedgerRepository
from app.domains.inventory.unit_ledger_v2_models import (
    UnitStockEntryV2,
    UnitStockLotV2,
    UnitStockOperationV2,
)


class UnitLedgerV2Repository(UnitLedgerRepository):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get_operation_v2(
        self, tenant_id: UUID, operation_id: UUID
    ) -> UnitStockOperationV2 | None:
        return (
            await self.session.scalars(
                select(UnitStockOperationV2).where(
                    UnitStockOperationV2.tenant_id == tenant_id,
                    UnitStockOperationV2.id == operation_id,
                )
            )
        ).one_or_none()

    async def claim_operation_v2(
        self, operation: UnitStockOperationV2
    ) -> tuple[UnitStockOperationV2, bool]:
        statement = (
            insert(UnitStockOperationV2)
            .values(
                id=operation.id,
                tenant_id=operation.tenant_id,
                branch_id=operation.branch_id,
                catalog_id=operation.catalog_id,
                source_item_id=operation.source_item_id,
                packaging=operation.packaging,
                kind=operation.kind,
                sale_format=operation.sale_format,
                quantity=operation.quantity,
                base_units=operation.base_units,
                request_hash=operation.request_hash,
                created_by=operation.created_by,
            )
            .on_conflict_do_nothing()
            .returning(UnitStockOperationV2.id)
        )
        created = await self.session.scalar(statement) is not None
        result = await self.get_operation_v2(operation.tenant_id, operation.id)
        if result is None:
            raise RuntimeError("Version-two operation claim did not resolve")
        return result, created

    async def has_entries_v2(self, tenant_id: UUID, operation_id: UUID) -> bool:
        return (
            await self.session.scalar(
                select(UnitStockEntryV2.id).where(
                    UnitStockEntryV2.tenant_id == tenant_id,
                    UnitStockEntryV2.operation_id == operation_id,
                )
            )
            is not None
        )

    async def insert_lot_v2(self, lot: UnitStockLotV2) -> None:
        self.session.add(lot)
        await self.session.flush()

    async def insert_entry_v2(self, entry: UnitStockEntryV2) -> None:
        self.session.add(entry)
        await self.session.flush()
