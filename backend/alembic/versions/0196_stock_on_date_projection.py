"""Scoped historical legacy and received physical stock with cost redaction.

Revision ID: 0196
Revises: 0195
"""

from alembic import op

revision = "0196"
down_revision = "0195"
branch_labels = None
depends_on = None

SIGNATURE = "public.pos_stock_on_date_for_report(UUID,TIMESTAMPTZ,UUID)"


def upgrade() -> None:
    op.execute(r"""
      CREATE FUNCTION public.pos_stock_on_date_for_report(
        p_tenant UUID, p_cutoff TIMESTAMPTZ, p_branch UUID DEFAULT NULL
      ) RETURNS TABLE (
        stock_id UUID, inventory_kind TEXT, name TEXT, inn TEXT, branch_name TEXT,
        batch_number TEXT, expires_at DATE, qty NUMERIC, physical_stock JSONB,
        purchase_price NUMERIC, value NUMERIC, cost_status TEXT, currency TEXT
      ) LANGUAGE SQL STABLE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp AS $$
        WITH authorized AS MATERIALIZED (
          SELECT p_tenant AS tenant_id
          WHERE session_user IN ('aurum_app','aurum_support')
            AND p_tenant IS NOT NULL AND p_tenant=public.current_tenant_id()
            AND p_cutoff IS NOT NULL AND isfinite(p_cutoff)
            AND public.current_app_user_id() IS NOT NULL
            AND EXISTS (SELECT 1 FROM public.app_user u
              WHERE u.id=public.current_app_user_id() AND u.status='active')
        ), legacy AS (
          SELECT b.id,b.catalog_id,b.branch_id,b.batch_number,b.expires_at,b.purchase_price,
            b.currency,sum(m.qty_delta) AS quantity,
            coalesce(public.tenant_actor_has_scoped_permission(
              b.tenant_id,'batches.view_costs',b.branch_id),false) AS costs_visible
          FROM authorized a JOIN public.batch b ON b.tenant_id=a.tenant_id
          JOIN public.batch_movement m ON m.tenant_id=b.tenant_id AND m.batch_id=b.id
            AND m.created_at<p_cutoff
          WHERE (p_branch IS NULL OR b.branch_id=p_branch)
            AND coalesce(public.tenant_actor_has_scoped_permission(
              b.tenant_id,'reports.view',b.branch_id),false)
          GROUP BY b.tenant_id,b.id,b.catalog_id,b.branch_id,b.batch_number,b.expires_at,
            b.purchase_price,b.currency
          HAVING sum(m.qty_delta)>0
        ), sources AS MATERIALIZED (
          SELECT l.*,i.batch_number,
            coalesce(public.tenant_actor_has_scoped_permission(
              l.tenant_id,'batches.view_costs',l.branch_id),false) AS costs_visible
          FROM authorized a JOIN public.unit_stock_lot_v2 l ON l.tenant_id=a.tenant_id
          JOIN public.incoming_item i ON i.tenant_id=l.tenant_id AND i.id=l.source_item_id
            AND i.created_unit_lot_v2_id=l.id AND i.catalog_id=l.catalog_id
            AND i.created_batch_id IS NULL AND i.created_unit_lot_id IS NULL
          JOIN public.incoming_document d ON d.tenant_id=i.tenant_id AND d.id=i.document_id
            AND d.branch_id=l.branch_id AND d.status='accepted'
            AND d.accepted_by IS NOT NULL AND d.accepted_at<p_cutoff
          WHERE (p_branch IS NULL OR l.branch_id=p_branch)
            AND coalesce(public.tenant_actor_has_scoped_permission(
              l.tenant_id,'reports.view',l.branch_id),false)
        ), initialized AS (
          SELECT l.id,sum(e.closed_delta) AS closed_packs,sum(e.base_units) AS base_units
          FROM sources l JOIN public.unit_stock_entry_v2 e
            ON e.tenant_id=l.tenant_id AND e.lot_id=l.id AND e.source_item_id=l.source_item_id
            AND e.branch_id=l.branch_id AND e.catalog_id=l.catalog_id
            AND e.kind='initialize' AND e.created_at<p_cutoff
          GROUP BY l.id
        ), spent AS (
          SELECT l.id,sum(m.closed_delta) AS closed_delta,sum(m.units_taken) AS units_taken,
            coalesce(sum(m.cost_amount) FILTER(WHERE m.units_taken>0),0) AS cost_amount,
            count(*) FILTER(WHERE m.units_taken>0
              AND (m.cost_version IS DISTINCT FROM 1 OR m.cost_amount IS NULL)) AS unknown_costs
          FROM sources l JOIN public.unit_stock_movement_v2 m
            ON m.tenant_id=l.tenant_id AND m.lot_id=l.id
            AND m.branch_id=l.branch_id AND m.catalog_id=l.catalog_id AND m.created_at<p_cutoff
          GROUP BY l.id
        ), balances AS (
          SELECT l.*,i.closed_packs+coalesce(s.closed_delta,0) AS historical_closed,
            i.base_units-coalesce(s.units_taken,0) AS historical_units,
            CASE WHEN l.costs_visible AND l.cost_version=1 AND coalesce(s.unknown_costs,0)=0
              THEN l.initial_closed_packs*l.purchase_cost_per_pack-coalesce(s.cost_amount,0)
              ELSE NULL END AS remaining_cost
          FROM sources l JOIN initialized i ON i.id=l.id LEFT JOIN spent s ON s.id=l.id
        ), combined AS (
          SELECT b.id,b.catalog_id,b.branch_id,b.batch_number,b.expires_at,
            'legacy'::TEXT AS inventory_kind,b.quantity AS qty,NULL::JSONB AS physical_stock,
            CASE WHEN b.costs_visible THEN b.purchase_price ELSE NULL END AS purchase_price,
            CASE WHEN b.costs_visible THEN round(b.quantity*b.purchase_price,2)
              ELSE NULL END AS value,
            CASE WHEN b.costs_visible THEN 'known' ELSE 'hidden' END AS cost_status,b.currency
          FROM legacy b
          UNION ALL
          SELECT l.id,l.catalog_id,l.branch_id,l.batch_number,l.expires_at,'physical',0::NUMERIC,
            jsonb_build_object('packaging',l.packaging,
              'closed_packs',l.historical_closed::TEXT,
              'opened_units',(l.historical_units-l.historical_closed
                *(l.packaging->>'units_per_pack')::NUMERIC)::TEXT,
              'base_units',l.historical_units::TEXT),
            CASE WHEN l.costs_visible THEN l.purchase_cost_per_pack ELSE NULL END,
            l.remaining_cost,
            CASE WHEN NOT l.costs_visible THEN 'hidden'
              WHEN l.remaining_cost IS NULL THEN 'unknown' ELSE 'known' END,'TJS'
          FROM balances l WHERE l.historical_units>0
        )
        SELECT c.id,c.inventory_kind,tc.brand_name,tc.inn,br.name,c.batch_number,c.expires_at,
          c.qty,c.physical_stock,c.purchase_price,c.value,c.cost_status,c.currency
        FROM combined c LEFT JOIN public.tenant_catalog tc
          ON tc.tenant_id=p_tenant AND tc.id=c.catalog_id
        LEFT JOIN public.branch br ON br.tenant_id=p_tenant AND br.id=c.branch_id
      $$
    """)
    op.execute(f"ALTER FUNCTION {SIGNATURE} OWNER TO aurum_schema_owner")
    op.execute(f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO aurum_app,aurum_support")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION {SIGNATURE}")
