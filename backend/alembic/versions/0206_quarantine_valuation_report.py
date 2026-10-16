"""Read historical quarantine valuation through a scoped, cost-gated projection.

Revision ID: 0206
Revises: 0205
"""

from alembic import op

revision = "0206"
down_revision = "0205"
branch_labels = None
depends_on = None

SIGNATURE = "public.pos_quarantine_on_date_for_report(uuid,timestamptz,uuid)"


def upgrade() -> None:
    op.execute(r"""
      CREATE FUNCTION public.pos_quarantine_on_date_for_report(
        p_tenant UUID,p_before TIMESTAMPTZ,p_branch UUID DEFAULT NULL
      ) RETURNS TABLE (
        inventory_kind TEXT,source_kind TEXT,intake_operation_id UUID,line_id UUID,
        stock_id UUID,branch_id UUID,branch_name TEXT,name TEXT,received_at TIMESTAMPTZ,
        qty NUMERIC,base_units BIGINT,unit TEXT,value NUMERIC,cost_status TEXT
      ) LANGUAGE SQL STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      WITH authorized AS MATERIALIZED (
        SELECT p_tenant AS tenant_id
        WHERE session_user IN ('aurum_app','aurum_support')
          AND p_tenant=public.current_tenant_id() AND p_before IS NOT NULL AND isfinite(p_before)
          AND EXISTS(SELECT 1 FROM public.app_user u
            WHERE u.id=public.current_app_user_id() AND u.status='active')
      ), markers AS (
        SELECT 'physical'::TEXT AS kind,m.tenant_id,m.id,m.parent_sale_id,m.branch_id,
          m.actor_id,m.created_at
        FROM authorized a JOIN public.pos_physical_cash_refund m ON m.tenant_id=a.tenant_id
        JOIN public.sale r ON r.tenant_id=m.tenant_id AND r.id=m.sale_id
          AND r.parent_sale_id=m.parent_sale_id AND r.branch_id=m.branch_id
          AND r.status='completed' AND r.sale_type='return' AND NOT r.is_test
          AND r.operation_id=m.id AND r.cashier_user_id=m.actor_id
        UNION ALL
        SELECT 'physical',m.tenant_id,m.id,m.parent_sale_id,m.branch_id,m.actor_id,m.created_at
        FROM authorized a JOIN public.pos_physical_zero_return m ON m.tenant_id=a.tenant_id
        UNION ALL
        SELECT 'mixed',m.tenant_id,m.id,m.parent_sale_id,m.branch_id,m.actor_id,m.created_at
        FROM authorized a JOIN public.pos_mixed_cash_refund m ON m.tenant_id=a.tenant_id
        JOIN public.sale r ON r.tenant_id=m.tenant_id AND r.id=m.sale_id
          AND r.parent_sale_id=m.parent_sale_id AND r.branch_id=m.branch_id
          AND r.status='completed' AND r.sale_type='return' AND NOT r.is_test
          AND r.operation_id=m.id AND r.cashier_user_id=m.actor_id
        UNION ALL
        SELECT 'mixed',m.tenant_id,m.id,m.parent_sale_id,m.branch_id,m.actor_id,m.created_at
        FROM authorized a JOIN public.pos_mixed_zero_return m ON m.tenant_id=a.tenant_id
      ), scoped_markers AS MATERIALIZED (
        SELECT m.* FROM markers m JOIN public.sale p
          ON p.tenant_id=m.tenant_id AND p.id=m.parent_sale_id AND p.branch_id=m.branch_id
          AND p.sale_type='sale' AND NOT p.is_test AND p.completed_at IS NOT NULL
          AND p.status='completed' AND p.parent_sale_id IS NULL
        WHERE m.created_at<p_before AND (p_branch IS NULL OR m.branch_id=p_branch)
          AND coalesce(public.tenant_actor_has_scoped_permission(
            m.tenant_id,'reports.view',m.branch_id),false)
      ), physical AS (
        SELECT m.tenant_id,m.kind,m.id,l.line_id,s.lot_id AS stock_id,m.branch_id,
          historical.value->>'name' AS name,m.created_at,
          sum(s.base_units)::BIGINT AS base_units,
          historical.value->'packaging'->>'unit' AS unit,sum(s.cost_amount) AS cost
        FROM scoped_markers m
        JOIN public.pos_physical_return_command c ON c.tenant_id=m.tenant_id AND c.id=m.id
          AND c.parent_sale_id=m.parent_sale_id AND c.branch_id=m.branch_id
          AND c.actor_id=m.actor_id AND c.state='prepared_quarantine'
        JOIN public.pos_physical_checkout checkout ON checkout.tenant_id=c.tenant_id
          AND checkout.id=c.parent_checkout_id AND checkout.sale_id=m.parent_sale_id
          AND checkout.branch_id=m.branch_id AND checkout.stock_command_id=c.stock_command_id
        JOIN public.pos_physical_return_line l ON l.tenant_id=c.tenant_id
          AND l.command_id=c.id AND l.branch_id=m.branch_id
        JOIN public.pos_physical_return_slice s ON s.tenant_id=l.tenant_id
          AND s.command_id=l.command_id AND s.line_id=l.line_id AND s.branch_id=m.branch_id
        JOIN public.unit_stock_lot_v2 lot ON lot.tenant_id=s.tenant_id AND lot.id=s.lot_id
          AND lot.branch_id=m.branch_id AND lot.catalog_id=l.catalog_id
        CROSS JOIN LATERAL jsonb_array_elements(checkout.result_payload->'lines') historical(value)
        WHERE m.kind='physical' AND checkout.result_payload->>'schema_version'='3'
          AND checkout.result_payload->>'document_kind'='physical_checkout'
          AND historical.value->>'id'=l.line_id::TEXT
          AND historical.value->>'catalog_id'=l.catalog_id::TEXT
          AND NOT EXISTS(SELECT 1 FROM public.physical_customer_return_disposition d
            WHERE d.tenant_id=m.tenant_id AND d.intake_operation_id=m.id
              AND d.line_id=l.line_id AND d.lot_id=s.lot_id AND d.resolved_at<p_before)
        GROUP BY m.tenant_id,m.kind,m.id,l.line_id,s.lot_id,m.branch_id,
          historical.value,m.created_at
      ), mixed_source AS (
        SELECT m.tenant_id,m.kind,m.id,l.line_id,l.inventory_kind,l.batch_id,l.catalog_id,
          l.legacy_quantity,m.branch_id,historical.value->>'name' AS name,m.created_at,
          historical.value->'packaging'->>'unit' AS unit
        FROM scoped_markers m
        JOIN public.pos_mixed_return_command c ON c.tenant_id=m.tenant_id AND c.id=m.id
          AND c.parent_sale_id=m.parent_sale_id AND c.branch_id=m.branch_id
          AND c.actor_id=m.actor_id AND c.state='prepared_quarantine'
        JOIN public.pos_mixed_checkout checkout ON checkout.tenant_id=c.tenant_id
          AND checkout.id=c.parent_checkout_id AND checkout.sale_id=m.parent_sale_id
          AND checkout.branch_id=m.branch_id AND checkout.stock_command_id=c.stock_command_id
        JOIN public.pos_mixed_return_line l ON l.tenant_id=c.tenant_id
          AND l.command_id=c.id AND l.branch_id=m.branch_id
        CROSS JOIN LATERAL jsonb_array_elements(checkout.result_payload->'lines') historical(value)
        WHERE m.kind='mixed' AND checkout.result_payload->>'schema_version'='4'
          AND checkout.result_payload->>'document_kind'='mixed_checkout'
          AND historical.value->>'id'=l.line_id::TEXT
          AND historical.value->>'catalog_id'=l.catalog_id::TEXT
          AND historical.value->>'inventory_kind'=l.inventory_kind
      ), mixed_physical AS (
        SELECT l.tenant_id,l.kind,l.id,l.line_id,s.lot_id AS stock_id,l.branch_id,l.name,
          l.created_at,sum(s.base_units)::BIGINT AS base_units,l.unit,sum(s.cost_amount) AS cost
        FROM mixed_source l JOIN public.pos_mixed_return_slice s
          ON s.tenant_id=l.tenant_id AND s.command_id=l.id AND s.line_id=l.line_id
          AND s.branch_id=l.branch_id
        JOIN public.unit_stock_lot_v2 lot ON lot.tenant_id=s.tenant_id AND lot.id=s.lot_id
          AND lot.branch_id=l.branch_id AND lot.catalog_id=l.catalog_id
        WHERE l.inventory_kind='physical_unit'
          AND NOT EXISTS(SELECT 1 FROM public.mixed_customer_return_disposition d
            WHERE d.tenant_id=l.tenant_id AND d.intake_operation_id=l.id
              AND d.line_id=l.line_id AND d.lot_id=s.lot_id AND d.resolved_at<p_before)
        GROUP BY l.tenant_id,l.kind,l.id,l.line_id,s.lot_id,l.branch_id,l.name,l.created_at,l.unit
      ), sources AS (
        SELECT 'physical'::TEXT AS inventory_kind,p.tenant_id,p.kind,p.id,p.line_id,p.stock_id,
          p.branch_id,p.name,p.created_at,0::NUMERIC AS qty,p.base_units,p.unit,p.cost
        FROM physical p
        UNION ALL
        SELECT 'physical',p.tenant_id,p.kind,p.id,p.line_id,p.stock_id,
          p.branch_id,p.name,p.created_at,0::NUMERIC,p.base_units,p.unit,p.cost
        FROM mixed_physical p
        UNION ALL
        SELECT 'legacy',l.tenant_id,l.kind,l.id,l.line_id,l.batch_id,l.branch_id,l.name,
          l.created_at,l.legacy_quantity,NULL::BIGINT,NULL::TEXT,NULL::NUMERIC
        FROM mixed_source l
        JOIN public.batch b ON b.tenant_id=l.tenant_id AND b.id=l.batch_id
          AND b.branch_id=l.branch_id AND b.catalog_id=l.catalog_id
        WHERE l.inventory_kind='legacy_pack'
          AND NOT EXISTS(SELECT 1 FROM public.mixed_customer_return_disposition d
            WHERE d.tenant_id=l.tenant_id AND d.intake_operation_id=l.id
              AND d.line_id=l.line_id AND d.batch_id=l.batch_id AND d.resolved_at<p_before)
        UNION ALL
        SELECT 'legacy',q.tenant_id,'legacy',q.id,q.return_sale_item_id,q.batch_id,q.branch_id,
          catalog.brand_name::TEXT,q.received_at,q.qty,NULL::BIGINT,NULL::TEXT,NULL::NUMERIC
        FROM authorized a JOIN public.customer_return_quarantine_item q
          ON q.tenant_id=a.tenant_id
        JOIN public.sale r ON r.tenant_id=q.tenant_id AND r.id=q.return_sale_id
          AND r.branch_id=q.branch_id AND r.parent_sale_id=q.parent_sale_id
          AND r.sale_type='return' AND r.status='completed'
          AND r.completed_at IS NOT NULL AND NOT r.is_test
        JOIN public.tenant_catalog catalog
          ON catalog.tenant_id=q.tenant_id AND catalog.id=q.catalog_id
        WHERE q.received_at<p_before AND (p_branch IS NULL OR q.branch_id=p_branch)
          AND coalesce(public.tenant_actor_has_scoped_permission(
            q.tenant_id,'reports.view',q.branch_id),false)
          AND NOT EXISTS(SELECT 1 FROM public.pos_mixed_checkout m
            WHERE m.tenant_id=q.tenant_id AND m.sale_id=q.parent_sale_id)
          AND NOT EXISTS(SELECT 1 FROM public.customer_return_disposition d
            WHERE d.tenant_id=q.tenant_id AND d.quarantine_item_id=q.id AND d.resolved_at<p_before)
      )
      SELECT s.inventory_kind,s.kind,s.id,s.line_id,s.stock_id,s.branch_id,b.name::TEXT,
        coalesce(s.name,'Без названия'),s.created_at,s.qty,s.base_units,s.unit,
        CASE WHEN rights.can_cost THEN s.cost ELSE NULL END,
        CASE WHEN NOT rights.can_cost THEN 'hidden'
          WHEN s.cost IS NULL THEN 'unknown' ELSE 'known' END
      FROM sources s JOIN public.branch b ON b.tenant_id=s.tenant_id AND b.id=s.branch_id
      CROSS JOIN LATERAL (SELECT coalesce(public.tenant_actor_has_scoped_permission(
        s.tenant_id,'batches.view_costs',s.branch_id),false) AS can_cost) rights
      $$
    """)
    op.execute(f"ALTER FUNCTION {SIGNATURE} OWNER TO aurum_schema_owner")
    op.execute(f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC,aurum_app,aurum_support")
    op.execute(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO aurum_app,aurum_support")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION {SIGNATURE}")
