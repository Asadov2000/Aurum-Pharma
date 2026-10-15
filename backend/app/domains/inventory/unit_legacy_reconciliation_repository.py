"""Scoped, cost-free legacy source projection, without row locks or writes."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory.models import Batch
from app.domains.inventory.unit_legacy_reconciliation import LegacyReconciliationSource


@dataclass(frozen=True)
class LegacyReconciliationBatch:
    source: LegacyReconciliationSource
    has_preparation: bool
    has_physical_snapshot: bool


class LegacyReconciliationPreviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def read_batch(
        self, *, tenant_id: UUID, branch_id: UUID, batch_id: UUID
    ) -> LegacyReconciliationBatch | None:
        result = await self.session.execute(
            select(
                Batch.tenant_id,
                Batch.branch_id,
                Batch.catalog_id,
                Batch.id.label("batch_id"),
                Batch.qty_remaining.label("legacy_quantity"),
                Batch.unit_packaging_preparation.is_not(None).label("has_preparation"),
                Batch.unit_packaging_snapshot_v2.is_not(None).label("has_physical_snapshot"),
            ).where(
                Batch.tenant_id == tenant_id,
                Batch.branch_id == branch_id,
                Batch.id == batch_id,
            )
        )
        row = result.one_or_none()
        if row is None:
            return None
        source = LegacyReconciliationSource.model_validate(
            {key: row._mapping[key] for key in LegacyReconciliationSource.model_fields}
        )
        return LegacyReconciliationBatch(
            source=source,
            has_preparation=row.has_preparation is True,
            has_physical_snapshot=row.has_physical_snapshot is True,
        )
