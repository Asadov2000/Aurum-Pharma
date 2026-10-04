"""Transaction-scoped persistence; the caller owns commit and rollback."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionDeniedError
from app.domains.catalog.models import TenantCatalog
from app.domains.incoming.models import IncomingDocument, IncomingItem
from app.domains.inventory.unit_ledger_models import (
    UnitStockEntry,
    UnitStockLot,
    UnitStockOpenedPack,
    UnitStockOperation,
)


class UnitLedgerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def assert_received_actor(self, tenant_id: UUID, branch_id: UUID, actor_id: UUID) -> None:
        """Apply actual app actor and scoped POS authorization before any retry."""
        if not isinstance(actor_id, UUID):
            raise PermissionDeniedError("An authenticated actor is required")
        authorized = await self.session.scalar(
            text(
                "SELECT current_user='aurum_app' AND NOT public.is_support_session() "
                "AND public.current_app_user_id()=:actor "
                "AND public.current_tenant_id()=:tenant AND "
                "public.tenant_actor_has_scoped_permission(:tenant,'pos.sell',:branch)"
            ),
            {"tenant": tenant_id, "branch": branch_id, "actor": actor_id},
        )
        if authorized is not True:
            raise PermissionDeniedError("Received stock posting permission denied")

    async def received_entries_are_valid(self, tenant_id: UUID, operation_id: UUID) -> bool:
        count, all_valid = (
            await self.session.execute(
                select(
                    func.count(),
                    func.bool_and(
                        func.public.unit_lot_has_received_source(
                            UnitStockEntry.tenant_id,
                            UnitStockEntry.branch_id,
                            UnitStockEntry.catalog_id,
                            UnitStockEntry.lot_id,
                        )
                    ),
                )
                .select_from(UnitStockEntry)
                .where(
                    UnitStockEntry.tenant_id == tenant_id,
                    UnitStockEntry.operation_id == operation_id,
                )
            )
        ).one()
        return bool(count) and all_valid is True

    async def incoming_source(
        self, item_id: UUID, actor_id: UUID
    ) -> tuple[IncomingItem, IncomingDocument] | None:
        """Read a real source under tenant RLS, with an explicit current actor."""
        if not isinstance(actor_id, UUID):
            raise PermissionDeniedError("An authenticated actor is required")
        actor_matches = await self.session.scalar(
            text("SELECT current_user = 'aurum_app' AND public.current_app_user_id() = :actor"),
            {"actor": actor_id},
        )
        if actor_matches is not True:
            raise PermissionDeniedError("Prepared receiving requires the application actor context")
        row = (
            await self.session.execute(
                select(IncomingItem, IncomingDocument)
                .join(IncomingDocument, IncomingDocument.id == IncomingItem.document_id)
                .where(IncomingItem.id == item_id)
                .with_for_update(of=IncomingItem)
                .execution_options(populate_existing=True)
            )
        ).one_or_none()
        if row is None:
            return None
        item, document = row
        await self.assert_incoming_actor(document.tenant_id, document.branch_id, actor_id)
        return item, document

    async def assert_incoming_actor(self, tenant_id: UUID, branch_id: UUID, actor_id: UUID) -> None:
        if not isinstance(actor_id, UUID):
            raise PermissionDeniedError("An authenticated actor is required")
        authorized = await self.session.scalar(
            text(
                "SELECT current_user = 'aurum_app' AND public.current_app_user_id() = :actor "
                "AND public.current_tenant_id() = :tenant AND "
                "public.tenant_actor_has_scoped_permission(:tenant, 'incoming.finalize', :branch)"
            ),
            {"tenant": tenant_id, "branch": branch_id, "actor": actor_id},
        )
        if authorized is not True:
            raise PermissionDeniedError("Prepared receiving permission denied")

    async def get_operation(self, tenant_id: UUID, operation_id: UUID) -> UnitStockOperation | None:
        return (
            await self.session.scalars(
                select(UnitStockOperation).where(
                    UnitStockOperation.tenant_id == tenant_id,
                    UnitStockOperation.id == operation_id,
                )
            )
        ).one_or_none()

    async def claim_operation(
        self, operation: UnitStockOperation
    ) -> tuple[UnitStockOperation, bool]:
        """Claim before catalog locks: a duplicate waits for the first transaction."""
        existing_statement = select(UnitStockOperation).where(
            UnitStockOperation.tenant_id == operation.tenant_id,
            UnitStockOperation.id == operation.id,
        )
        existing = (await self.session.scalars(existing_statement)).one_or_none()
        if existing is not None:
            return existing, False
        statement = (
            insert(UnitStockOperation)
            .values(
                id=operation.id,
                tenant_id=operation.tenant_id,
                branch_id=operation.branch_id,
                catalog_id=operation.catalog_id,
                kind=operation.kind,
                sale_format=operation.sale_format,
                quantity=operation.quantity,
                base_units=operation.base_units,
                unit=operation.unit,
                units_per_pack=operation.units_per_pack,
                dispense_step=operation.dispense_step,
                request_hash=operation.request_hash,
                stock_source_kind=(
                    "prepared"
                    if operation.stock_source_kind is None
                    else operation.stock_source_kind
                ),
                created_by=operation.created_by,
                updated_by=operation.updated_by,
            )
            # The scoped FK adds a second unique index containing the command
            # identity. Arbitrate both indexes so simultaneous retries cannot
            # raise a uniqueness error on the otherwise non-arbiter index.
            .on_conflict_do_nothing()
            .returning(UnitStockOperation)
        )
        claimed = (await self.session.execute(statement)).scalar_one_or_none()
        if claimed is not None:
            return claimed, True
        existing = (await self.session.scalars(existing_statement)).one_or_none()
        if existing is None:
            raise PermissionDeniedError("Branch access denied")
        return existing, False

    async def insert_lot(self, lot: UnitStockLot) -> None:
        self.session.add(lot)
        await self.session.flush()

    async def insert_pack(self, pack: UnitStockOpenedPack) -> None:
        self.session.add(pack)
        await self.session.flush()

    async def insert_entry(self, entry: UnitStockEntry) -> None:
        self.session.add(entry)
        await self.session.flush()

        await self.session.refresh(entry)

    async def get_entries(self, tenant_id: UUID, operation_id: UUID) -> list[UnitStockEntry]:
        rows = await self.session.scalars(
            select(UnitStockEntry)
            .where(
                UnitStockEntry.tenant_id == tenant_id,
                UnitStockEntry.operation_id == operation_id,
            )
            .order_by(UnitStockEntry.entry_order)
        )
        return list(rows)

    async def lock_catalog(self, tenant_id: UUID, catalog_id: UUID) -> TenantCatalog | None:
        return (
            await self.session.scalars(
                select(TenantCatalog)
                .where(TenantCatalog.tenant_id == tenant_id, TenantCatalog.id == catalog_id)
                # Compatible with FK KEY SHARE locks acquired by command claims.
                .with_for_update(key_share=True)
                .execution_options(populate_existing=True)
            )
        ).one_or_none()

    async def lock_lots(
        self,
        tenant_id: UUID,
        branch_id: UUID,
        catalog_id: UUID,
        *,
        received_only: bool = False,
    ) -> list[UnitStockLot]:
        statement = (
            select(UnitStockLot)
            .where(
                UnitStockLot.tenant_id == tenant_id,
                UnitStockLot.branch_id == branch_id,
                UnitStockLot.catalog_id == catalog_id,
            )
            .order_by(UnitStockLot.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if received_only:
            statement = statement.where(
                func.public.unit_lot_has_received_source(
                    UnitStockLot.tenant_id,
                    UnitStockLot.branch_id,
                    UnitStockLot.catalog_id,
                    UnitStockLot.id,
                )
            )
        rows = await self.session.scalars(statement)
        return list(rows)

    async def lock_packs(self, tenant_id: UUID, lot_ids: list[UUID]) -> list[UnitStockOpenedPack]:
        rows = await self.session.scalars(
            select(UnitStockOpenedPack)
            .where(
                UnitStockOpenedPack.tenant_id == tenant_id,
                UnitStockOpenedPack.lot_id.in_(lot_ids),
                UnitStockOpenedPack.remaining_units > 0,
            )
            .order_by(UnitStockOpenedPack.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return list(rows)
