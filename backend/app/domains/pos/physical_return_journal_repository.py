"""Scoped history persistence; nested transaction and commit belong to caller."""

from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionDeniedError
from app.domains.inventory.unit_ledger_v2_models import UnitStockCartLineV2, UnitStockMovementV2
from app.domains.pos.models import Sale
from app.domains.pos.physical_cash_checkout_models import PhysicalCashCheckout
from app.domains.pos.physical_return_journal_models import (
    PhysicalReturnCommand,
    PhysicalReturnLine,
    PhysicalReturnSlice,
)


class PhysicalReturnJournalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def assert_actor(self, tenant_id: UUID, actor_id: UUID, branch_id: UUID | None) -> None:
        allowed = await self.session.scalar(
            text(
                "SELECT current_user='aurum_app' AND session_user='aurum_app' "
                "AND NOT public.is_support_session() "
                "AND public.current_app_user_id()=:actor AND public.current_tenant_id()=:tenant "
                "AND EXISTS(SELECT 1 FROM public.app_user WHERE id=:actor AND status='active') "
                "AND public.tenant_actor_has_scoped_permission(:tenant,'pos.refund',:branch)"
            ),
            {"tenant": tenant_id, "actor": actor_id, "branch": branch_id},
        )
        if allowed is not True:
            raise PermissionDeniedError("Branch refund access denied")

    async def lock_operation(self, tenant_id: UUID, operation_id: UUID) -> None:
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity,0))"),
            {"identity": f"aurum-physical-return:{tenant_id}:{operation_id}"},
        )

    async def get_command(
        self, tenant_id: UUID, operation_id: UUID
    ) -> PhysicalReturnCommand | None:
        result: PhysicalReturnCommand | None = await self.session.scalar(
            select(PhysicalReturnCommand).where(
                PhysicalReturnCommand.tenant_id == tenant_id,
                PhysicalReturnCommand.id == operation_id,
            )
        )
        return result

    async def get_checkout(self, tenant_id: UUID, sale_id: UUID) -> PhysicalCashCheckout | None:
        result: PhysicalCashCheckout | None = await self.session.scalar(
            select(PhysicalCashCheckout).where(
                PhysicalCashCheckout.tenant_id == tenant_id, PhysicalCashCheckout.sale_id == sale_id
            )
        )
        return result

    async def lock_sale(self, tenant_id: UUID, sale_id: UUID) -> Sale | None:
        result: Sale | None = await self.session.scalar(
            select(Sale)
            .where(Sale.tenant_id == tenant_id, Sale.id == sale_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return result

    async def cursors(self, tenant_id: UUID, stock_command_id: UUID) -> dict[UUID, int]:
        rows = await self.session.execute(
            select(PhysicalReturnLine.line_id, func.sum(PhysicalReturnLine.base_units))
            .where(
                PhysicalReturnLine.tenant_id == tenant_id,
                PhysicalReturnLine.stock_command_id == stock_command_id,
            )
            .group_by(PhysicalReturnLine.line_id)
        )
        return {UUID(str(line_id)): int(units) for line_id, units in rows}

    async def source_line(
        self, tenant_id: UUID, command_id: UUID, line_id: UUID
    ) -> UnitStockCartLineV2 | None:
        result: UnitStockCartLineV2 | None = await self.session.scalar(
            select(UnitStockCartLineV2).where(
                UnitStockCartLineV2.tenant_id == tenant_id,
                UnitStockCartLineV2.command_id == command_id,
                UnitStockCartLineV2.line_id == line_id,
            )
        )
        return result

    async def movements(
        self, tenant_id: UUID, command_id: UUID, line_id: UUID
    ) -> tuple[UnitStockMovementV2, ...]:
        rows = await self.session.scalars(
            select(UnitStockMovementV2)
            .where(
                UnitStockMovementV2.tenant_id == tenant_id,
                UnitStockMovementV2.command_id == command_id,
                UnitStockMovementV2.line_id == line_id,
                UnitStockMovementV2.units_taken > 0,
            )
            .order_by(UnitStockMovementV2.entry_order, UnitStockMovementV2.id)
        )
        return tuple(rows)

    async def insert(
        self,
        command: PhysicalReturnCommand,
        lines: tuple[PhysicalReturnLine, ...],
        slices: tuple[PhysicalReturnSlice, ...],
    ) -> None:
        self.session.add(command)
        await self.session.flush()
        self.session.add_all(lines)
        await self.session.flush()
        self.session.add_all(slices)
        await self.session.flush()
        await self.session.execute(text("SET CONSTRAINTS physical_return_complete IMMEDIATE"))
        await self.session.execute(text("SET CONSTRAINTS physical_return_complete DEFERRED"))
