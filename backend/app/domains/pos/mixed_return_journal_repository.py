"""Scoped mixed history persistence; caller owns transaction and commit."""

from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionDeniedError
from app.domains.inventory.unit_ledger_v2_models import UnitStockCartLineV2, UnitStockMovementV2
from app.domains.pos.mixed_cash_checkout_models import MixedCashCheckout
from app.domains.pos.mixed_return_journal_models import (
    MixedReturnCommand,
    MixedReturnLine,
    MixedReturnSlice,
)
from app.domains.pos.models import Sale, SaleItem
from app.domains.pos.physical_return_journal_repository import PhysicalReturnJournalRepository


class MixedReturnJournalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.physical = PhysicalReturnJournalRepository(session)

    async def assert_actor(self, tenant_id: UUID, actor_id: UUID, branch_id: UUID | None) -> None:
        await self.physical.assert_actor(tenant_id, actor_id, branch_id)

    async def assert_identity(self, tenant_id: UUID, actor_id: UUID) -> None:
        allowed = await self.session.scalar(
            text(
                "SELECT current_user='aurum_app' AND session_user='aurum_app' "
                "AND NOT public.is_support_session() "
                "AND public.current_app_user_id()=:actor AND public.current_tenant_id()=:tenant "
                "AND EXISTS(SELECT 1 FROM public.app_user WHERE id=:actor AND status='active')"
            ),
            {"tenant": tenant_id, "actor": actor_id},
        )
        if allowed is not True:
            raise PermissionDeniedError("Active tenant actor is required")

    async def lock_operation(self, tenant_id: UUID, operation_id: UUID) -> None:
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity,0))"),
            {"identity": f"aurum-mixed-return:{tenant_id}:{operation_id}"},
        )

    async def get_command(self, tenant_id: UUID, operation_id: UUID) -> MixedReturnCommand | None:
        result: MixedReturnCommand | None = await self.session.scalar(
            select(MixedReturnCommand).where(
                MixedReturnCommand.tenant_id == tenant_id, MixedReturnCommand.id == operation_id
            )
        )
        return result

    async def get_checkout(self, tenant_id: UUID, sale_id: UUID) -> MixedCashCheckout | None:
        result: MixedCashCheckout | None = await self.session.scalar(
            select(MixedCashCheckout).where(
                MixedCashCheckout.tenant_id == tenant_id, MixedCashCheckout.sale_id == sale_id
            )
        )
        return result

    async def lock_sale(self, tenant_id: UUID, sale_id: UUID) -> Sale | None:
        return await self.physical.lock_sale(tenant_id, sale_id)

    async def cursors(
        self, tenant_id: UUID, parent_sale_id: UUID
    ) -> tuple[dict[UUID, Decimal], dict[UUID, int]]:
        rows = await self.session.execute(
            select(
                MixedReturnLine.line_id,
                MixedReturnLine.inventory_kind,
                func.sum(MixedReturnLine.legacy_quantity),
                func.sum(MixedReturnLine.base_units),
            )
            .join(
                MixedReturnCommand,
                (MixedReturnCommand.tenant_id == MixedReturnLine.tenant_id)
                & (MixedReturnCommand.id == MixedReturnLine.command_id),
            )
            .where(
                MixedReturnLine.tenant_id == tenant_id,
                MixedReturnCommand.parent_sale_id == parent_sale_id,
            )
            .group_by(MixedReturnLine.line_id, MixedReturnLine.inventory_kind)
        )
        legacy: dict[UUID, Decimal] = {}
        physical: dict[UUID, int] = {}
        for line_id, kind, packs, units in rows:
            if kind == "legacy_pack":
                legacy[UUID(str(line_id))] = Decimal(packs)
            else:
                physical[UUID(str(line_id))] = int(units)
        return legacy, physical

    async def legacy_source(self, tenant_id: UUID, sale_id: UUID, line_id: UUID) -> SaleItem | None:
        result: SaleItem | None = await self.session.scalar(
            select(SaleItem).where(
                SaleItem.tenant_id == tenant_id, SaleItem.sale_id == sale_id, SaleItem.id == line_id
            )
        )
        return result

    async def source_line(
        self, tenant_id: UUID, command_id: UUID, line_id: UUID
    ) -> UnitStockCartLineV2 | None:
        return await self.physical.source_line(tenant_id, command_id, line_id)

    async def movements(
        self, tenant_id: UUID, command_id: UUID, line_id: UUID
    ) -> tuple[UnitStockMovementV2, ...]:
        return await self.physical.movements(tenant_id, command_id, line_id)

    async def insert(
        self,
        command: MixedReturnCommand,
        lines: tuple[MixedReturnLine, ...],
        slices: tuple[MixedReturnSlice, ...],
    ) -> None:
        self.session.add(command)
        await self.session.flush()
        self.session.add_all(lines)
        await self.session.flush()
        self.session.add_all(slices)
        await self.session.flush()
        await self.session.execute(text("SET CONSTRAINTS mixed_return_complete IMMEDIATE"))
        await self.session.execute(text("SET CONSTRAINTS mixed_return_complete DEFERRED"))
