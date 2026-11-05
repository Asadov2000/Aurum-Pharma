"""Scoped repeatable-read facts; never writes operational inventory."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.catalog.models import TenantCatalog
from app.domains.catalog.unit_sales import MAX_BASE_UNITS
from app.domains.incoming.models import IncomingDocument, IncomingItem
from app.domains.inventory.unit_ledger_models import (
    UnitStockEntry,
    UnitStockLot,
    UnitStockOpenedPack,
)
from app.domains.inventory.unit_ledger_v2_models import (
    UnitStockLotV2,
    UnitStockMovementV2,
    UnitStockOpenedPackV2,
)
from app.domains.sync.physical_read_snapshot import (
    MAX_PHYSICAL_RECORDS,
    Money,
    PhysicalOpeningBasisRecord,
)


class PhysicalWriteoffCostRecord(BaseModel):
    """Monetary source facts; incomplete evidence stays explicitly unknown."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    tenant_id: UUID
    branch_id: UUID
    catalog_id: UUID
    lot_id: UUID
    units_taken: int = Field(strict=True, gt=0, le=MAX_BASE_UNITS)
    cost_version: int | None = Field(strict=True, ge=1)
    cost_amount: Money | None


@dataclass(frozen=True)
class PhysicalSourceFacts:
    catalog: tuple[TenantCatalog, ...]
    lots: tuple[UnitStockLotV2, ...]
    packs: tuple[UnitStockOpenedPackV2, ...]
    movements: tuple[UnitStockMovementV2, ...]
    legacy_lots: tuple[UnitStockLot, ...]
    legacy_packs: tuple[UnitStockOpenedPack, ...]
    legacy_entries: tuple[UnitStockEntry, ...]
    legacy_sources: dict[UUID, UUID]
    opening_basis: tuple[PhysicalOpeningBasisRecord, ...]
    writeoff_costs: tuple[PhysicalWriteoffCostRecord, ...] = ()


async def assert_physical_source_context(session: AsyncSession) -> None:
    if session.new or session.dirty or session.deleted:
        raise ValueError("Physical snapshot requires a clean read session")
    isolation = await session.scalar(text("SHOW transaction_isolation"))
    if isolation not in ("repeatable read", "serializable"):
        raise ValueError("Physical snapshot requires one repeatable database snapshot")


async def read_physical_source_facts(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    branch_id: UUID,
) -> PhysicalSourceFacts:
    await assert_physical_source_context(session)
    with session.no_autoflush:
        writeoff_rows = (
            (
                await session.execute(
                    text(
                        "SELECT id,tenant_id,branch_id,catalog_id,lot_id,"
                        "units_taken,cost_version,cost_amount "
                        "FROM public.read_physical_writeoff_cost_source(:tenant,:branch,:limit)"
                    ),
                    {"tenant": tenant_id, "branch": branch_id, "limit": MAX_PHYSICAL_RECORDS + 1},
                )
            )
            .mappings()
            .all()
        )
        opening_rows = (
            (
                await session.execute(
                    text(
                        "SELECT id,lot_id,tenant_id,branch_id,catalog_id,"
                        "transition_id,pack_ordinal,"
                        "opened_at,state,remaining_units,cost_amount "
                        "FROM public.unit_stock_opening_pack_v2 "
                        "WHERE tenant_id=:tenant AND branch_id=:branch "
                        "ORDER BY lot_id,pack_ordinal "
                        "LIMIT :limit"
                    ).columns(state=JSONB()),
                    {"tenant": tenant_id, "branch": branch_id, "limit": MAX_PHYSICAL_RECORDS + 1},
                )
            )
            .mappings()
            .all()
        )
        catalog = tuple(
            await session.scalars(
                select(TenantCatalog)
                .where(TenantCatalog.tenant_id == tenant_id)
                .order_by(TenantCatalog.id)
                .limit(MAX_PHYSICAL_RECORDS + 1)
                .execution_options(populate_existing=True)
            )
        )
        lots = tuple(
            await session.scalars(
                select(UnitStockLotV2)
                .where(UnitStockLotV2.tenant_id == tenant_id, UnitStockLotV2.branch_id == branch_id)
                .order_by(UnitStockLotV2.id)
                .limit(MAX_PHYSICAL_RECORDS + 1)
                .execution_options(populate_existing=True)
            )
        )
        packs = tuple(
            await session.scalars(
                select(UnitStockOpenedPackV2)
                .where(
                    UnitStockOpenedPackV2.tenant_id == tenant_id,
                    UnitStockOpenedPackV2.branch_id == branch_id,
                )
                .order_by(UnitStockOpenedPackV2.lot_id, UnitStockOpenedPackV2.pack_ordinal)
                .limit(MAX_PHYSICAL_RECORDS + 1)
                .execution_options(populate_existing=True)
            )
        )
        movements = tuple(
            await session.scalars(
                select(UnitStockMovementV2)
                .where(
                    UnitStockMovementV2.tenant_id == tenant_id,
                    UnitStockMovementV2.branch_id == branch_id,
                )
                .order_by(UnitStockMovementV2.id)
                .limit(MAX_PHYSICAL_RECORDS + 1)
                .execution_options(populate_existing=True)
            )
        )
        legacy_lots = tuple(
            await session.scalars(
                select(UnitStockLot)
                .where(UnitStockLot.tenant_id == tenant_id, UnitStockLot.branch_id == branch_id)
                .order_by(UnitStockLot.id)
                .limit(MAX_PHYSICAL_RECORDS + 1)
                .execution_options(populate_existing=True)
            )
        )
        legacy_packs = tuple(
            await session.scalars(
                select(UnitStockOpenedPack)
                .where(
                    UnitStockOpenedPack.tenant_id == tenant_id,
                    UnitStockOpenedPack.branch_id == branch_id,
                )
                .order_by(UnitStockOpenedPack.id)
                .limit(MAX_PHYSICAL_RECORDS + 1)
                .execution_options(populate_existing=True)
            )
        )
        legacy_entries = tuple(
            await session.scalars(
                select(UnitStockEntry)
                .where(UnitStockEntry.tenant_id == tenant_id, UnitStockEntry.branch_id == branch_id)
                .order_by(UnitStockEntry.id)
                .limit(MAX_PHYSICAL_RECORDS + 1)
                .execution_options(populate_existing=True)
            )
        )
        sources = (
            await session.execute(
                select(IncomingItem.created_unit_lot_id, IncomingItem.id)
                .join(
                    IncomingDocument,
                    (IncomingDocument.id == IncomingItem.document_id)
                    & (IncomingDocument.tenant_id == IncomingItem.tenant_id),
                )
                .where(
                    IncomingItem.tenant_id == tenant_id,
                    IncomingItem.created_unit_lot_id.is_not(None),
                    IncomingDocument.branch_id == branch_id,
                    IncomingDocument.status == "accepted",
                )
                .limit(MAX_PHYSICAL_RECORDS + 1)
                .execution_options(populate_existing=True)
            )
        ).all()
    if any(
        len(rows) > MAX_PHYSICAL_RECORDS
        for rows in (
            catalog,
            lots,
            packs,
            movements,
            legacy_lots,
            legacy_packs,
            legacy_entries,
            sources,
            writeoff_rows,
        )
    ):
        raise ValueError("Physical snapshot source limit exceeded")
    legacy_sources: dict[UUID, UUID] = {}
    for lot_id, item_id in sources:
        if lot_id in legacy_sources:
            raise ValueError("Physical legacy source identity is ambiguous")
        legacy_sources[lot_id] = item_id
    if len(opening_rows) > MAX_PHYSICAL_RECORDS:
        raise ValueError("Physical opening basis source limit exceeded")
    opening_basis = tuple(
        PhysicalOpeningBasisRecord.model_validate(
            {**dict(row), "state": {**row["state"], "id": row["id"], "opened_at": row["opened_at"]}}
        )
        for row in opening_rows
    )
    return PhysicalSourceFacts(
        catalog,
        lots,
        packs,
        movements,
        legacy_lots,
        legacy_packs,
        legacy_entries,
        legacy_sources,
        opening_basis,
        tuple(PhysicalWriteoffCostRecord.model_validate(dict(row)) for row in writeoff_rows),
    )
