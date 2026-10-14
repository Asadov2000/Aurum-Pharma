"""One cost-free SQL snapshot of received physical lots and opened units."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import PermissionDeniedError
from app.domains.inventory.physical_schemas import PhysicalLotList, PhysicalLotRead

LIST_SQL = """
WITH lots AS (
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
 WHERE l.tenant_id=:tenant AND l.branch_id=:branch
   AND current_user='aurum_app' AND session_user='aurum_app'
   AND NOT public.is_support_session() AND public.current_tenant_id()=:tenant
   AND NOT public.is_tenant_support_session()
   AND public.current_app_user_id()=:actor
   AND EXISTS(SELECT 1 FROM public.app_user WHERE id=:actor AND status='active')
   AND public.tenant_actor_has_scoped_permission(:tenant,'batches.view',:branch)
   AND (CAST(:catalog AS uuid) IS NULL OR l.catalog_id=CAST(:catalog AS uuid))
   AND (CAST(:blocked AS boolean) IS NULL OR l.is_blocked=CAST(:blocked AS boolean))
   AND (:show_empty OR l.closed_packs>0 OR coalesce(p.opened_units,0)>0)
   AND (:q='' OR strpos(lower(coalesce(c.brand_name,'')),lower(:q))>0
     OR strpos(l.id::text,lower(:q))>0)
), page_rows AS (
 SELECT * FROM lots ORDER BY expires_at,lot_id LIMIT :limit OFFSET :offset
)
SELECT (SELECT count(*) FROM lots) AS total,
 coalesce((SELECT jsonb_agg(to_jsonb(p) ORDER BY p.expires_at,p.lot_id)
   FROM page_rows p),'[]'::jsonb) AS items
"""


class PhysicalInventoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_lots(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        branch_id: UUID,
        catalog_id: UUID | None,
        is_blocked: bool | None,
        show_empty: bool,
        q: str,
        page: int,
        page_size: int,
    ) -> PhysicalLotList:
        authorized = await self.session.scalar(
            text(
                "SELECT current_user='aurum_app' AND session_user='aurum_app' "
                "AND NOT public.is_support_session() "
                "AND NOT public.is_tenant_support_session() "
                "AND public.current_tenant_id()=:tenant "
                "AND public.current_app_user_id()=:actor "
                "AND EXISTS(SELECT 1 FROM public.app_user WHERE id=:actor AND status='active') "
                "AND public.tenant_actor_has_scoped_permission(:tenant,'batches.view',:branch)"
            ),
            {"tenant": tenant_id, "actor": actor_id, "branch": branch_id},
        )
        if authorized is not True:
            raise PermissionDeniedError("Physical inventory view permission denied")
        row = (
            await self.session.execute(
                text(LIST_SQL),
                {
                    "tenant": tenant_id,
                    "actor": actor_id,
                    "branch": branch_id,
                    "catalog": catalog_id,
                    "blocked": is_blocked,
                    "show_empty": show_empty,
                    "q": q,
                    "limit": page_size,
                    "offset": (page - 1) * page_size,
                },
            )
        ).one()
        return PhysicalLotList(
            items=[PhysicalLotRead.model_validate(item) for item in row.items],
            total=row.total,
            page=page,
            page_size=page_size,
        )
