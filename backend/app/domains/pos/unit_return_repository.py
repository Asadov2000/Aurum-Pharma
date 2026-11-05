"""Scoped persistence for immutable quarantine returns; caller owns commit."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionDeniedError
from app.domains.inventory.unit_ledger_models import UnitStockEntry
from app.domains.pos.unit_line_models import UnitSaleCommand, UnitSaleLine
from app.domains.pos.unit_return_models import UnitReturnCommand, UnitReturnSlice


class UnitReturnRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def assert_actor(self, tenant_id: UUID, branch_id: UUID, actor_id: UUID) -> None:
        allowed = await self.session.scalar(
            text(
                "SELECT current_user='aurum_app' AND session_user='aurum_app' "
                "AND NOT public.is_support_session() "
                "AND public.current_app_user_id()=:actor "
                "AND public.current_tenant_id()=:tenant "
                "AND public.tenant_actor_has_scoped_permission(:tenant,'pos.refund',:branch)"
            ),
            {"tenant": tenant_id, "branch": branch_id, "actor": actor_id},
        )
        if allowed is not True:
            raise PermissionDeniedError("Branch refund access denied")

    async def lock_operation(self, tenant_id: UUID, operation_id: UUID) -> None:
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity,0))"),
            {"identity": f"aurum-unit-return:{tenant_id}:{operation_id}"},
        )

    async def get_command(self, tenant_id: UUID, operation_id: UUID) -> UnitReturnCommand | None:
        command: UnitReturnCommand | None = await self.session.scalar(
            select(UnitReturnCommand).where(
                UnitReturnCommand.tenant_id == tenant_id, UnitReturnCommand.id == operation_id
            )
        )
        return command

    async def lock_original_line(self, tenant_id: UUID, line_id: UUID) -> UnitSaleLine | None:
        line: UnitSaleLine | None = await self.session.scalar(
            select(UnitSaleLine)
            .where(UnitSaleLine.tenant_id == tenant_id, UnitSaleLine.id == line_id)
            .with_for_update(key_share=True)
        )
        return line

    async def get_original_command(
        self, tenant_id: UUID, command_id: UUID
    ) -> UnitSaleCommand | None:
        command: UnitSaleCommand | None = await self.session.scalar(
            select(UnitSaleCommand).where(
                UnitSaleCommand.tenant_id == tenant_id, UnitSaleCommand.id == command_id
            )
        )
        return command

    async def returned_quantity(self, tenant_id: UUID, line_id: UUID) -> int:
        result = await self.session.scalar(
            select(func.coalesce(func.sum(UnitReturnCommand.quantity), 0)).where(
                UnitReturnCommand.tenant_id == tenant_id,
                UnitReturnCommand.original_line_id == line_id,
            )
        )
        return int(result or 0)

    async def get_original_entries(
        self, tenant_id: UUID, operation_id: UUID
    ) -> tuple[UnitStockEntry, ...]:
        rows = await self.session.scalars(
            select(UnitStockEntry)
            .where(
                UnitStockEntry.tenant_id == tenant_id,
                UnitStockEntry.operation_id == operation_id,
                UnitStockEntry.kind.in_(("pack_take", "unit_take", "unit_open")),
            )
            .order_by(UnitStockEntry.entry_order)
        )
        return tuple(rows)

    async def get_slices(self, tenant_id: UUID, command_id: UUID) -> tuple[UnitReturnSlice, ...]:
        rows = await self.session.scalars(
            select(UnitReturnSlice)
            .where(UnitReturnSlice.tenant_id == tenant_id, UnitReturnSlice.command_id == command_id)
            .order_by(UnitReturnSlice.position)
        )
        return tuple(rows)

    async def insert_command(self, command: UnitReturnCommand) -> None:
        self.session.add(command)
        await self.session.flush()

    async def insert_slices(self, slices: tuple[UnitReturnSlice, ...]) -> None:
        self.session.add_all(slices)
        await self.session.flush()
