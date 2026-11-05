"""Training journal persistence and scalar recovery; the caller owns commit."""

from datetime import date, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import Table, and_, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.catalog.models import TenantCatalog
from app.domains.inventory.repository import InventoryRepository
from app.domains.inventory.unit_dispensing_v2_snapshots import physical_stock_snapshots_v2
from app.domains.inventory.unit_stock_v2 import UnitStockBatchV2
from app.domains.pos.models import Sale
from app.domains.pos.repository import POSRepository
from app.domains.pos.training_checkout_models import TrainingCheckout
from app.domains.pos.training_checkout_schemas import TrainingCheckoutResultV1
from app.domains.pos.training_simulation import TrainingLegacyBatchSnapshot
from app.domains.pos.unit_cart_quote import MixedCartQuoteV2, PhysicalUnitDraftQuoteLineV2
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_contracts import LegacyResultLineV2

# Declarative exposes FromClause, but both models own concrete, non-inherited tables.
_SALE_TABLE = cast(Table, Sale.__table__)
_TRAINING_TABLE = cast(Table, TrainingCheckout.__table__)


class TrainingSaleState(BaseModel):
    """Column snapshot only; reading cannot refresh a caller's ORM objects."""

    model_config = ConfigDict(extra="ignore", frozen=True, from_attributes=True)
    id: UUID
    tenant_id: UUID
    branch_id: UUID
    register_id: UUID
    shift_id: UUID
    cashier_user_id: UUID
    sale_type: str
    status: str
    is_test: bool
    currency: str
    total_amount: Decimal
    receipt_number: str | None
    receipt_seq: int | None
    completed_at: datetime | None
    receipt_snapshot: dict[str, object] | None


class TrainingSavedState(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)
    id: UUID
    tenant_id: UUID
    branch_id: UUID
    register_id: UUID
    shift_id: UUID
    cashier_user_id: UUID
    sale_id: UUID
    request_hash: str
    quote_revision: str
    total_amount: Decimal
    receipt_number: str
    receipt_seq: int
    result_payload: dict[str, object]
    receipt_payload: dict[str, object]
    created_at: datetime
    created_by: UUID


class TrainingCheckoutRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, tenant_id: UUID, operation_id: UUID) -> TrainingSavedState | None:
        payload = await self.session.scalar(
            select(func.to_jsonb(_TRAINING_TABLE.table_valued())).where(
                TrainingCheckout.tenant_id == tenant_id, TrainingCheckout.id == operation_id
            )
        )
        return None if payload is None else TrainingSavedState.model_validate(payload)

    async def read(
        self, tenant_id: UUID, sale_id: UUID, operation_id: UUID
    ) -> tuple[TrainingSaleState, TrainingSavedState | None] | None:
        row = (
            await self.session.execute(
                select(
                    func.to_jsonb(_SALE_TABLE.table_valued()),
                    func.to_jsonb(_TRAINING_TABLE.table_valued()),
                )
                .select_from(Sale)
                .outerjoin(
                    TrainingCheckout,
                    and_(
                        TrainingCheckout.tenant_id == Sale.tenant_id,
                        TrainingCheckout.sale_id == Sale.id,
                        TrainingCheckout.id == operation_id,
                    ),
                )
                .where(Sale.tenant_id == tenant_id, Sale.id == sale_id)
            )
        ).one_or_none()
        if row is None:
            return None
        return (
            TrainingSaleState.model_validate(row[0]),
            None if row[1] is None else TrainingSavedState.model_validate(row[1]),
        )

    async def names(self, tenant_id: UUID, catalog_ids: set[UUID]) -> dict[UUID, str]:
        rows = await self.session.execute(
            select(TenantCatalog.id, TenantCatalog.brand_name).where(
                TenantCatalog.tenant_id == tenant_id, TenantCatalog.id.in_(catalog_ids)
            )
        )
        return {row.id: row.brand_name for row in rows}

    async def allocate_receipt(self, register_id: UUID) -> tuple[int, str] | None:
        return await POSRepository(self.session).allocate_receipt_number(register_id)

    async def stock_snapshots(
        self, quote: MixedCartQuoteV2, today: date
    ) -> tuple[tuple[UnitStockBatchV2, ...], tuple[TrainingLegacyBatchSnapshot, ...]]:
        physical: list[UnitStockBatchV2] = []
        legacy: list[TrainingLegacyBatchSnapshot] = []
        cart = UnitCartRepository(self.session)
        physical_catalogs = {
            line.catalog_id
            for line in quote.lines
            if isinstance(line, PhysicalUnitDraftQuoteLineV2)
        }
        for catalog_id in sorted(physical_catalogs, key=str):
            lots, packs = await cart.read_physical_stock(
                quote.tenant_id, quote.branch_id, catalog_id
            )
            physical.extend(physical_stock_snapshots_v2(lots, packs))
        legacy_catalogs = {
            line.catalog_id for line in quote.lines if isinstance(line, LegacyResultLineV2)
        }
        for catalog_id in sorted(legacy_catalogs, key=str):
            batches = await InventoryRepository(self.session).fefo_candidates(
                tenant_id=quote.tenant_id,
                branch_id=quote.branch_id,
                catalog_id=catalog_id,
                include_expired=False,
                today=today,
                lock=False,
            )
            legacy.extend(
                TrainingLegacyBatchSnapshot(
                    id=batch.id,
                    tenant_id=batch.tenant_id,
                    branch_id=batch.branch_id,
                    catalog_id=batch.catalog_id,
                    expires_at=batch.expires_at,
                    created_at=batch.created_at,
                    quantity_remaining=batch.qty_remaining,
                    is_blocked=batch.is_blocked,
                )
                for batch in batches
            )
        return tuple(physical), tuple(legacy)

    async def insert(self, result: TrainingCheckoutResultV1, request_hash: str) -> None:
        self.session.add(
            TrainingCheckout(
                id=result.operation_id,
                tenant_id=result.tenant_id,
                branch_id=result.branch_id,
                register_id=result.register_id,
                shift_id=result.shift_id,
                cashier_user_id=result.cashier_id,
                sale_id=result.sale_id,
                request_hash=request_hash,
                quote_revision=result.request.expected_revision_hash,
                total_amount=result.total_amount,
                receipt_number=result.receipt_number,
                receipt_seq=result.receipt_sequence,
                result_payload=result.model_dump(mode="json"),
                receipt_payload=result.receipt.model_dump(mode="json"),
                created_at=result.completed_at,
                created_by=result.cashier_id,
            )
        )
        await self.session.flush()

    async def complete_sale(self, sale: Sale, result: TrainingCheckoutResultV1) -> None:
        # Keep the original idempotent draft-creation operation identity intact.
        sale.total_amount = result.total_amount
        sale.status = "completed"
        sale.completed_at = result.completed_at
        sale.receipt_number = result.receipt_number
        sale.receipt_seq = result.receipt_sequence
        sale.receipt_snapshot = result.receipt.model_dump(mode="json")
        await self.session.flush()

    async def validate_completion(self) -> None:
        await self.session.execute(text("SET CONSTRAINTS training_checkout_complete IMMEDIATE"))
        await self.session.execute(text("SET CONSTRAINTS training_checkout_complete DEFERRED"))
