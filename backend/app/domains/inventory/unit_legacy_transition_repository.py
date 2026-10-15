"""RLS-respecting scalar source snapshot for an internal legacy transfer plan."""

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.inventory.unit_legacy_reconciliation import LegacyReconciliationCount

_ACTOR_SQL = """
 current_user='aurum_app' AND session_user='aurum_app'
 AND NOT public.is_support_session() AND NOT public.is_tenant_support_session()
 AND public.current_tenant_id()=:tenant AND public.current_app_user_id()=:actor
 AND EXISTS(SELECT 1 FROM public.app_user WHERE id=:actor AND status='active')
 AND public.tenant_actor_has_scoped_permission(:tenant,'batches.view',:branch)
 AND public.tenant_actor_has_scoped_permission(:tenant,'batches.view_costs',:branch)
 AND public.tenant_actor_has_scoped_permission(:tenant,'batches.update',:branch)
 AND public.tenant_actor_has_scoped_permission(:tenant,'incoming.view',:branch)
"""

_SOURCE_SQL = """
 SELECT b.catalog_id, r.legacy_quantity AS recorded_quantity,
   d.id AS incoming_document_id, i.id AS incoming_item_id,
   (SELECT m.id FROM public.batch_movement m
     WHERE m.tenant_id=b.tenant_id AND m.batch_id=b.id AND m.movement_type='incoming'
       AND m.source_table='incoming_item' AND m.source_id=i.id AND m.qty_delta=i.qty
     LIMIT 1) AS incoming_movement_id,
   b.qty_remaining AS current_quantity, r.count,
   b.unit_packaging_preparation IS NOT NULL
     OR b.unit_packaging_snapshot_v2 IS NOT NULL AS prepared,
   b.purchase_price AS current_pack_cost,
   (SELECT count(*) FROM public.incoming_item i
     WHERE i.tenant_id=b.tenant_id AND i.created_batch_id=b.id) AS source_count,
   i.purchase_price AS historical_pack_cost,
   coalesce(d.status='accepted' AND d.accepted_at IS NOT NULL AND d.accepted_by IS NOT NULL
     AND d.branch_id=b.branch_id AND i.catalog_id=b.catalog_id
     AND i.qty=b.qty_initial AND b.qty_remaining<=b.qty_initial AND i.expires_at=b.expires_at
     AND i.batch_number IS NOT DISTINCT FROM b.batch_number
     AND i.manufactured_at IS NOT DISTINCT FROM b.manufactured_at
     AND i.currency='TJS' AND d.currency='TJS' AND b.currency='TJS'
     AND i.created_unit_lot_id IS NULL AND i.created_unit_lot_v2_id IS NULL
     AND i.unit_packaging_preparation IS NULL AND i.unit_packaging_snapshot_v2 IS NULL
     AND (SELECT count(*) FROM public.batch_movement m
       WHERE m.tenant_id=b.tenant_id AND m.batch_id=b.id AND m.movement_type='incoming')=1
     AND EXISTS(SELECT 1 FROM public.batch_movement m
       WHERE m.tenant_id=b.tenant_id AND m.batch_id=b.id AND m.movement_type='incoming'
         AND m.source_table='incoming_item' AND m.source_id=i.id AND m.qty_delta=i.qty),false)
     AS provenance_matches
 FROM public.unit_legacy_reconciliation_record r
 JOIN public.batch b ON b.tenant_id=r.tenant_id AND b.id=r.batch_id
   AND b.branch_id=r.branch_id AND b.catalog_id=r.catalog_id
 LEFT JOIN public.incoming_item i ON i.tenant_id=b.tenant_id AND i.created_batch_id=b.id
 LEFT JOIN public.incoming_document d ON d.tenant_id=i.tenant_id AND d.id=i.document_id
 WHERE r.tenant_id=:tenant AND r.branch_id=:branch AND r.batch_id=:batch AND r.id=:record
   AND ({actor})
 LIMIT 2
"""


class LegacyTransitionSourceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    catalog_id: UUID
    incoming_document_id: UUID | None
    incoming_item_id: UUID | None
    incoming_movement_id: UUID | None
    recorded_quantity: Decimal
    current_quantity: Decimal
    count: LegacyReconciliationCount
    prepared: bool
    current_pack_cost: Decimal
    historical_pack_cost: Decimal | None
    source_count: int
    provenance_matches: bool


class LegacyTransitionSourceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def actor_allowed(self, *, tenant_id: UUID, branch_id: UUID, actor_id: UUID) -> bool:
        return (
            await self.session.scalar(
                text(f"SELECT {_ACTOR_SQL}"),
                {"tenant": tenant_id, "branch": branch_id, "actor": actor_id},
            )
            is True
        )

    async def read_source(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        batch_id: UUID,
        reconciliation_id: UUID,
        actor_id: UUID,
    ) -> tuple[LegacyTransitionSourceSnapshot, ...]:
        # One statement observes authorization, immutable count and current facts;
        # scalar columns bypass cached ORM identities. No write lock is acquired.
        result = await self.session.execute(
            text(_SOURCE_SQL.format(actor=_ACTOR_SQL)),
            {
                "tenant": tenant_id,
                "branch": branch_id,
                "batch": batch_id,
                "record": reconciliation_id,
                "actor": actor_id,
            },
        )
        return tuple(
            LegacyTransitionSourceSnapshot.model_validate(row) for row in result.mappings()
        )
