"""Single-statement snapshot of the scoped quarantine read interface."""

from datetime import datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError
from app.domains.pos.quarantine_report_schemas import QuarantineReportSnapshot

SNAPSHOT_SQL = """
WITH rows AS MATERIALIZED (
 SELECT * FROM public.pos_quarantine_on_date_for_report(:tenant,:before,:branch)
), totals AS (
 SELECT count(*) AS total,coalesce(sum(qty),0)::TEXT AS total_qty,
   CASE WHEN count(*) FILTER(WHERE cost_status<>'known')=0
     THEN coalesce(sum(value),0)::TEXT ELSE NULL END AS total_value,
   CASE WHEN count(*)>0 AND count(*) FILTER(WHERE cost_status<>'hidden')=0
     THEN NULL ELSE coalesce(sum(value),0)::TEXT END AS known_total_value,
   CASE WHEN count(*)=0 OR count(*) FILTER(WHERE cost_status<>'known')=0 THEN 'known'
     WHEN count(*) FILTER(WHERE cost_status<>'hidden')=0 THEN 'hidden'
     ELSE 'partial' END AS cost_status FROM rows
), units AS (
 SELECT unit,sum(base_units)::TEXT AS quantity FROM rows
 WHERE inventory_kind='physical' GROUP BY unit
), paged AS (
 SELECT * FROM rows ORDER BY received_at DESC,source_kind,intake_operation_id,line_id,stock_id
 LIMIT :limit OFFSET :offset
)
SELECT jsonb_build_object(
 'total',t.total,'total_qty',t.total_qty,'total_value',t.total_value,
 'known_total_value',t.known_total_value,'cost_status',t.cost_status,
 'physical_quantities',coalesce((SELECT jsonb_agg(to_jsonb(u) ORDER BY u.unit) FROM units u),'[]'),
 'rows',coalesce((SELECT jsonb_agg(jsonb_build_object(
   'inventory_kind',p.inventory_kind,'source_kind',p.source_kind,
   'intake_operation_id',p.intake_operation_id,'line_id',p.line_id,'stock_id',p.stock_id,
   'branch_id',p.branch_id,'branch_name',p.branch_name,'name',p.name,'received_at',p.received_at,
   'qty',p.qty::TEXT,'physical_quantity',CASE WHEN p.inventory_kind='physical'
     THEN jsonb_build_object('unit',p.unit,'quantity',p.base_units::TEXT) ELSE NULL END,
   'value',p.value::TEXT,'cost_status',p.cost_status)
   ORDER BY p.received_at DESC,p.source_kind,p.intake_operation_id,p.line_id,p.stock_id)
   FROM paged p),'[]')) FROM totals t
"""


class QuarantineReportRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def timezone(self, tenant_id: UUID) -> str:
        value = await self.session.scalar(
            text("SELECT report_timezone FROM tenant_settings WHERE tenant_id=:tenant"),
            {"tenant": tenant_id},
        )
        return value if isinstance(value, str) else "Asia/Dushanbe"

    async def snapshot(
        self,
        *,
        tenant_id: UUID,
        before: datetime,
        branch_id: UUID | None,
        limit: int | None,
        offset: int,
    ) -> QuarantineReportSnapshot:
        payload = await self.session.scalar(
            text(SNAPSHOT_SQL),
            {
                "tenant": tenant_id,
                "before": before,
                "branch": branch_id,
                "limit": limit,
                "offset": offset,
            },
        )
        try:
            return QuarantineReportSnapshot.model_validate(payload)
        except ValidationError:
            raise BusinessRuleError("Quarantine report snapshot is invalid") from None
