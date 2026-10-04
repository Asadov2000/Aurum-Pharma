"""Prepared line persistence, scoped to a caller-owned transaction."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionDeniedError
from app.domains.catalog.models import TenantCatalog
from app.domains.pos.unit_line_models import UnitSaleCommand, UnitSaleLine


class UnitSaleLineRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def claim_command(self, command: UnitSaleCommand) -> tuple[UnitSaleCommand, bool]:
        statement = select(UnitSaleCommand).where(
            UnitSaleCommand.tenant_id == command.tenant_id,
            UnitSaleCommand.id == command.id,
        )
        existing = (await self.session.scalars(statement)).one_or_none()
        if existing is not None:
            return existing, False
        claimed = (
            await self.session.execute(
                insert(UnitSaleCommand)
                .values(
                    id=command.id,
                    tenant_id=command.tenant_id,
                    branch_id=command.branch_id,
                    catalog_id=command.catalog_id,
                    request_hash=command.request_hash,
                    sale_format=command.sale_format,
                    quantity=command.quantity,
                    discount_amount=command.discount_amount,
                    stock_source_kind=(
                        "prepared"
                        if command.stock_source_kind is None
                        else command.stock_source_kind
                    ),
                )
                .on_conflict_do_nothing()
                .returning(UnitSaleCommand)
            )
        ).scalar_one_or_none()
        if claimed is not None:
            return claimed, True
        existing = (await self.session.scalars(statement)).one_or_none()
        if existing is None:
            raise PermissionDeniedError("Branch access denied")
        return existing, False

    async def get_catalog(self, tenant_id: UUID, catalog_id: UUID) -> TenantCatalog | None:
        return (
            await self.session.scalars(
                select(TenantCatalog).where(
                    TenantCatalog.tenant_id == tenant_id,
                    TenantCatalog.id == catalog_id,
                )
            )
        ).one_or_none()

    async def get_line(self, tenant_id: UUID, command_id: UUID) -> UnitSaleLine | None:
        return (
            await self.session.scalars(
                select(UnitSaleLine).where(
                    UnitSaleLine.tenant_id == tenant_id,
                    UnitSaleLine.command_id == command_id,
                )
            )
        ).one_or_none()

    async def insert_line(self, line: UnitSaleLine) -> None:
        self.session.add(line)
        await self.session.flush()
