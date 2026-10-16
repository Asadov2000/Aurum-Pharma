"""One statement computes period totals and its independently paginated detail."""

from datetime import datetime
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError
from app.domains.pos.profit_report_schemas import ProfitReportSnapshot

SNAPSHOT_SQL = """
WITH rows AS MATERIALIZED (
 SELECT * FROM public.pos_gross_profit_for_report(:tenant,:start,:end,:branch)
), sales AS (
 SELECT coalesce(sum(revenue) FILTER(WHERE event_kind='sale'),0) AS sales_revenue,
   -coalesce(sum(revenue) FILTER(WHERE event_kind='refund'),0) AS refunds,
   coalesce(sum(revenue),0) AS net_revenue,
   CASE WHEN count(*) FILTER(WHERE cost_status<>'known')=0 THEN coalesce(sum(cost),0) END AS cost,
   CASE WHEN count(*) FILTER(WHERE cost_status<>'known')=0
     THEN coalesce(sum(gross_profit),0) END AS profit,
   CASE WHEN count(*)>0 AND count(*) FILTER(WHERE cost_status<>'hidden')=0
     THEN NULL ELSE coalesce(sum(cost),0) END AS known_cost,
   CASE WHEN count(*)>0 AND count(*) FILTER(WHERE cost_status<>'hidden')=0
     THEN NULL ELSE coalesce(sum(gross_profit),0) END AS known_profit,
   CASE WHEN count(*) FILTER(WHERE cost_status<>'known')=0 THEN 'known'
     WHEN count(*) FILTER(WHERE cost_status<>'hidden')=0 THEN 'hidden' ELSE 'partial' END AS status
 FROM rows WHERE event_kind<>'writeoff'
), losses AS (
 SELECT CASE WHEN count(*) FILTER(WHERE cost_status<>'known')=0
     THEN coalesce(sum(cost),0) END AS cost,
   CASE WHEN count(*)>0 AND count(*) FILTER(WHERE cost_status<>'hidden')=0
     THEN NULL ELSE coalesce(sum(cost),0) END AS known_cost,
   CASE WHEN count(*) FILTER(WHERE cost_status<>'known')=0 THEN 'known'
     WHEN count(*) FILTER(WHERE cost_status<>'hidden')=0 THEN 'hidden' ELSE 'partial' END AS status
 FROM rows WHERE event_kind='writeoff'
), paged AS (
 SELECT * FROM rows
 ORDER BY occurred_at DESC,event_kind,source_kind,event_id,line_id NULLS FIRST
 LIMIT :limit OFFSET :offset
)
SELECT jsonb_build_object(
 'total',(SELECT count(*) FROM rows),'sales_revenue',s.sales_revenue::TEXT,
 'refunds',s.refunds::TEXT,'net_revenue',s.net_revenue::TEXT,'net_sales_cost',s.cost::TEXT,
 'known_net_sales_cost',s.known_cost::TEXT,'gross_profit',s.profit::TEXT,
 'known_gross_profit',s.known_profit::TEXT,'writeoff_cost',l.cost::TEXT,
 'known_writeoff_cost',l.known_cost::TEXT,'cost_status',s.status,'writeoff_cost_status',l.status,
 'rows',coalesce((SELECT jsonb_agg(jsonb_build_object(
   'event_id',p.event_id,'line_id',p.line_id,'branch_id',p.branch_id,'branch_name',p.branch_name,
   'name',p.name,'occurred_at',p.occurred_at,'source_kind',p.source_kind,'event_kind',p.event_kind,
   'revenue',p.revenue::TEXT,'cost',p.cost::TEXT,'gross_profit',p.gross_profit::TEXT,
   'cost_status',p.cost_status)
   ORDER BY p.occurred_at DESC,p.event_kind,p.source_kind,p.event_id,p.line_id NULLS FIRST)
   FROM paged p),'[]')) FROM sales s CROSS JOIN losses l
"""


class ProfitReportRepository:
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
        start: datetime,
        end: datetime,
        branch_id: UUID | None,
        limit: int | None,
        offset: int,
    ) -> ProfitReportSnapshot:
        payload = await self.session.scalar(
            text(SNAPSHOT_SQL),
            {
                "tenant": tenant_id,
                "start": start,
                "end": end,
                "branch": branch_id,
                "limit": limit,
                "offset": offset,
            },
        )
        try:
            return ProfitReportSnapshot.model_validate(payload)
        except ValidationError:
            raise BusinessRuleError("Gross profit report snapshot is invalid") from None
