"""One scoped SQL snapshot of a lot and selectable opened-pack identities."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, PermissionDeniedError
from app.domains.inventory.physical_schemas import PhysicalLotRead
from app.domains.inventory.physical_writeoff_stock_schemas import (
    PhysicalWriteOffPackRead,
    PhysicalWriteOffStockRead,
)

STOCK_SQL = """
WITH authorized AS MATERIALIZED (
 SELECT current_user='aurum_app' AND session_user='aurum_app'
   AND NOT public.is_support_session() AND NOT public.is_tenant_support_session()
   AND public.current_tenant_id()=:tenant AND public.current_app_user_id()=:actor
   AND EXISTS(SELECT 1 FROM public.app_user WHERE id=:actor AND status='active')
   AND public.tenant_actor_has_scoped_permission(:tenant,'batches.view',:branch)
   AND public.tenant_actor_has_scoped_permission(:tenant,'batches.write_off',:branch) AS allowed
), selected_lot AS MATERIALIZED (
 SELECT l.id AS lot_id,l.branch_id,l.catalog_id,c.brand_name AS catalog_name,l.expires_at,
   l.packaging,l.closed_packs::text AS closed_packs,
   coalesce(p.opened_units,0)::text AS opened_units,
   (l.closed_packs::numeric*(l.packaging->>'units_per_pack')::integer
     +coalesce(p.opened_units,0))::text AS base_units,
   l.is_blocked,l.blocking_revision::text AS blocking_revision
 FROM public.unit_stock_lot_v2 l
 LEFT JOIN public.tenant_catalog c ON c.tenant_id=l.tenant_id AND c.id=l.catalog_id
 LEFT JOIN LATERAL (
   SELECT sum(remaining_units) AS opened_units FROM public.unit_stock_opened_pack_v2 p
   WHERE p.tenant_id=l.tenant_id AND p.branch_id=l.branch_id AND p.lot_id=l.id
 ) p ON true
 WHERE l.tenant_id=:tenant AND l.branch_id=:branch AND l.id=:lot
   AND (SELECT allowed FROM authorized)
), opened AS MATERIALIZED (
 SELECT p.id AS pack_id,p.remaining_units::text AS remaining_units,
   jsonb_build_object('intact_blisters',p.state->'intact_blisters',
     'opened_blisters',p.state->'opened_blisters','loose_units',p.state->'loose_units') AS state
 FROM public.unit_stock_opened_pack_v2 p
 JOIN selected_lot l ON l.lot_id=p.lot_id AND l.branch_id=p.branch_id
 WHERE p.tenant_id=:tenant AND p.branch_id=:branch AND p.remaining_units>0
), page_rows AS (
 SELECT * FROM opened ORDER BY pack_id LIMIT :limit OFFSET :offset
)
SELECT (SELECT allowed FROM authorized) AS allowed,
 (SELECT to_jsonb(l) FROM selected_lot l) AS lot,
 (SELECT count(*) FROM opened) AS total,
 coalesce((SELECT jsonb_agg(to_jsonb(p) ORDER BY p.pack_id) FROM page_rows p),'[]'::jsonb) AS packs
"""


class PhysicalWriteOffStockRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def read(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        branch_id: UUID,
        lot_id: UUID,
        page: int,
        page_size: int,
    ) -> PhysicalWriteOffStockRead:
        row = (
            await self.session.execute(
                text(STOCK_SQL),
                {
                    "tenant": tenant_id,
                    "actor": actor_id,
                    "branch": branch_id,
                    "lot": lot_id,
                    "limit": page_size,
                    "offset": (page - 1) * page_size,
                },
            )
        ).one()
        if row.allowed is not True:
            raise PermissionDeniedError("Physical write-off stock view permission denied")
        if row.lot is None:
            raise NotFoundError("Physical stock lot not found")
        return PhysicalWriteOffStockRead(
            lot=PhysicalLotRead.model_validate(row.lot),
            opened_packs=[PhysicalWriteOffPackRead.model_validate(pack) for pack in row.packs],
            total=row.total,
            page=page,
            page_size=page_size,
        )
