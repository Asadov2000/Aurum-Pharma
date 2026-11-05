"""One consistent SQL snapshot of immutable prepared stock journal history."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, PermissionDeniedError
from app.domains.inventory.unit_report_contracts import UnitStockSnapshot

STOCK_SNAPSHOT_SQL = """
WITH context AS (
 SELECT current_user='aurum_app' AND session_user='aurum_app'
   AND NOT public.is_support_session()
   AND public.current_tenant_id()=:tenant_id AND public.current_app_user_id() IS NOT NULL
   AND public.tenant_actor_has_scoped_permission(
     :tenant_id,'reports.view',:branch_id) AS authorized,
   coalesce(public.tenant_actor_has_scoped_permission(
     :tenant_id,'batches.view_costs',:branch_id),false) AS costs_visible
), history AS (
 SELECT e.lot_id,sum(e.closed_delta) AS closed_packs,sum(e.opened_delta) AS opened_units,
   sum(e.units_delta) AS base_units,
   coalesce(sum(e.cost_amount) FILTER(WHERE e.kind<>'initialize'),0) AS spent_cost,
   count(*) FILTER(WHERE e.kind<>'initialize'
     AND (e.cost_version IS DISTINCT FROM 1 OR e.cost_amount IS NULL)) AS unknown_spent
 FROM public.unit_stock_entry e CROSS JOIN context c
 WHERE c.authorized AND e.tenant_id=:tenant_id AND e.branch_id=:branch_id
   AND e.created_at<:cutoff_exclusive
 GROUP BY e.lot_id HAVING bool_or(e.kind='initialize')
), valued AS (
 SELECT l.id AS lot_id,l.catalog_id,l.expires_at,l.version,l.unit,l.units_per_pack,l.dispense_step,
   h.closed_packs,h.opened_units,h.base_units,
   CASE WHEN coalesce(public.unit_lot_is_received_for_report(
     l.tenant_id,l.branch_id,l.catalog_id,l.id,:cutoff_exclusive),false)
     THEN 'received' ELSE 'prepared' END AS source_kind,
   CASE WHEN c.costs_visible AND l.cost_version=1 AND l.initial_closed_packs IS NOT NULL
     AND l.purchase_cost_per_pack IS NOT NULL AND h.unknown_spent=0
   THEN l.initial_closed_packs*l.purchase_cost_per_pack-h.spent_cost ELSE NULL END AS remaining_cost
 FROM history h JOIN public.unit_stock_lot l ON l.id=h.lot_id
   AND l.tenant_id=:tenant_id AND l.branch_id=:branch_id CROSS JOIN context c
), validity AS (
 SELECT coalesce(bool_and(closed_packs>=0 AND opened_units>=0 AND base_units>=0
   AND base_units=closed_packs*units_per_pack+opened_units
   AND (remaining_cost IS NULL OR remaining_cost>=0)),true) AS valid FROM valued
), stocked AS (
 SELECT * FROM valued WHERE base_units>0
), totals AS (
 SELECT count(*) AS lot_count,count(*) FILTER(WHERE source_kind='received') AS received_lot_count,
   count(*) FILTER(WHERE source_kind='prepared') AS prepared_lot_count,
   count(remaining_cost) AS known_cost_lot_count,
   count(*) FILTER(WHERE remaining_cost IS NULL) AS unknown_cost_lot_count,
   coalesce(sum(remaining_cost),0) AS known_remaining_cost,
   coalesce(sum(remaining_cost) FILTER(WHERE source_kind='received'),0)
     AS received_known_remaining_cost,
   coalesce(sum(remaining_cost) FILTER(WHERE source_kind='prepared'),0)
     AS prepared_known_remaining_cost
 FROM stocked
), page_rows AS (
 SELECT lot_id,catalog_id,expires_at,source_kind,closed_packs,opened_units,base_units,
   jsonb_build_object('version',version,'unit',unit,'units_per_pack',units_per_pack,
     'dispense_step',dispense_step) AS packaging,
   CASE WHEN NOT c.costs_visible THEN 'hidden' WHEN remaining_cost IS NULL THEN 'unknown'
     ELSE 'known' END AS cost_status,CAST(remaining_cost AS TEXT) AS remaining_cost
 FROM stocked CROSS JOIN context c ORDER BY lot_id LIMIT :page_size OFFSET :offset
)
SELECT coalesce(c.authorized,false) AS authorized,v.valid,
 jsonb_build_object(
   'summary',jsonb_build_object(
     'lot_count',t.lot_count,'received_lot_count',t.received_lot_count,
     'prepared_lot_count',t.prepared_lot_count,
     'cost_status',CASE WHEN NOT c.costs_visible THEN 'hidden'
       WHEN t.unknown_cost_lot_count>0 THEN 'partial' ELSE 'known' END,
     'known_cost_lot_count',CASE WHEN c.costs_visible THEN t.known_cost_lot_count ELSE NULL END,
     'unknown_cost_lot_count',CASE WHEN c.costs_visible THEN t.unknown_cost_lot_count ELSE NULL END,
     'known_remaining_cost',CASE WHEN c.costs_visible THEN CAST(t.known_remaining_cost AS TEXT)
       ELSE NULL END,
     'received_known_remaining_cost',CASE WHEN c.costs_visible
       THEN CAST(t.received_known_remaining_cost AS TEXT) ELSE NULL END,
     'prepared_known_remaining_cost',CASE WHEN c.costs_visible
       THEN CAST(t.prepared_known_remaining_cost AS TEXT) ELSE NULL END),
   'items',coalesce((SELECT jsonb_agg(to_jsonb(p) ORDER BY p.lot_id) FROM page_rows p),'[]'::jsonb)
 ) AS payload
FROM context c CROSS JOIN totals t CROSS JOIN validity v
"""


class UnitStockReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def assert_actor(self, tenant_id: UUID, branch_id: UUID, actor_id: UUID) -> None:
        if not isinstance(actor_id, UUID):
            raise PermissionDeniedError("An authenticated actor is required")
        allowed = await self.session.scalar(
            text(
                "SELECT current_user='aurum_app' AND session_user='aurum_app' "
                "AND NOT public.is_support_session() AND public.current_app_user_id()=:actor "
                "AND public.current_tenant_id()=:tenant AND "
                "public.tenant_actor_has_scoped_permission(:tenant,'reports.view',:branch)"
            ),
            {"actor": actor_id, "tenant": tenant_id, "branch": branch_id},
        )
        if allowed is not True:
            raise PermissionDeniedError("Unit stock report permission denied")

    async def fetch_stock_snapshot(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        cutoff_exclusive: datetime,
        page: int = 1,
        page_size: int = 50,
    ) -> UnitStockSnapshot:
        if (
            type(page) is not int
            or not 1 <= page <= 1_000_000
            or type(page_size) is not int
            or not 1 <= page_size <= 100
            or not isinstance(cutoff_exclusive, datetime)
            or cutoff_exclusive.tzinfo is None
            or cutoff_exclusive.utcoffset() is None
        ):
            raise ValueError("report pagination and timezone-aware cutoff are required")
        row = (
            (
                await self.session.execute(
                    text(STOCK_SNAPSHOT_SQL).columns(payload=JSONB()),
                    {
                        "tenant_id": tenant_id,
                        "branch_id": branch_id,
                        "cutoff_exclusive": cutoff_exclusive,
                        "page_size": page_size,
                        "offset": (page - 1) * page_size,
                    },
                )
            )
            .mappings()
            .one()
        )
        if row["authorized"] is not True:
            raise PermissionDeniedError("Unit stock report permission denied")
        if row["valid"] is not True:
            raise BusinessRuleError("Prepared unit stock history is inconsistent")
        try:
            return UnitStockSnapshot.model_validate(row["payload"])
        except ValidationError as error:
            raise BusinessRuleError("Prepared unit stock history is inconsistent") from error
