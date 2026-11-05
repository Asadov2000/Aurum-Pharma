"""Scoped historical gross profit with independently reported warehouse losses.

Revision ID: 0207
Revises: 0206
"""

from alembic import op

revision = "0207"
down_revision = "0206"
branch_labels = None
depends_on = None

SIGNATURE = "public.pos_gross_profit_for_report(uuid,timestamptz,timestamptz,uuid)"


def upgrade() -> None:
    op.execute(r"""
      CREATE FUNCTION public.pos_gross_profit_for_report(
        p_tenant UUID,p_from TIMESTAMPTZ,p_to TIMESTAMPTZ,p_branch UUID DEFAULT NULL
      ) RETURNS TABLE (
        event_id UUID,line_id UUID,branch_id UUID,branch_name TEXT,name TEXT,
        occurred_at TIMESTAMPTZ,source_kind TEXT,event_kind TEXT,
        revenue NUMERIC,cost NUMERIC,gross_profit NUMERIC,cost_status TEXT
      ) LANGUAGE SQL STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp AS $$
      WITH authorized AS MATERIALIZED (
        SELECT p_tenant AS tenant_id
        WHERE session_user IN ('aurum_app','aurum_support')
          AND p_tenant=public.current_tenant_id()
          AND p_from IS NOT NULL AND p_to IS NOT NULL
          AND isfinite(p_from) AND isfinite(p_to) AND p_from<p_to
          AND EXISTS(SELECT 1 FROM public.app_user u
            WHERE u.id=public.current_app_user_id() AND u.status='active')
      ), scoped_sales AS MATERIALIZED (
        SELECT s.* FROM authorized a JOIN public.sale s ON s.tenant_id=a.tenant_id
        WHERE s.status IN ('completed','voided') AND NOT s.is_test
          AND (p_branch IS NULL OR s.branch_id=p_branch)
          AND coalesce(public.tenant_actor_has_scoped_permission(
            s.tenant_id,'reports.view',s.branch_id),false)
      ), checkouts AS (
        SELECT s.id AS sale_id,c.id AS checkout_id,c.stock_command_id,s.tenant_id,
          s.branch_id,s.completed_at,c.result_payload->'lines' AS lines,3 AS version,
          'physical'::TEXT AS source_kind
        FROM scoped_sales s JOIN public.pos_physical_checkout c
          ON c.tenant_id=s.tenant_id AND c.sale_id=s.id AND c.branch_id=s.branch_id
          AND c.register_id=s.register_id AND c.shift_id=s.shift_id
          AND c.cashier_user_id=s.cashier_user_id AND c.receipt_payload=s.receipt_snapshot
        WHERE s.sale_type='sale' AND s.status='completed'
          AND s.receipt_snapshot @> '{"schema_version": 3,"document_kind":"physical_receipt"}'
          AND c.result_payload @> '{"schema_version": 3,"document_kind":"physical_checkout"}'
        UNION ALL
        SELECT s.id,c.id,c.stock_command_id,s.tenant_id,s.branch_id,s.completed_at,
          c.result_payload->'lines',4,'mixed'
        FROM scoped_sales s JOIN public.pos_mixed_checkout c
          ON c.tenant_id=s.tenant_id AND c.sale_id=s.id AND c.branch_id=s.branch_id
          AND c.register_id=s.register_id AND c.shift_id=s.shift_id
          AND c.cashier_user_id=s.cashier_user_id AND c.receipt_payload=s.receipt_snapshot
        WHERE s.sale_type='sale' AND s.status='completed'
          AND s.receipt_snapshot @> '{"schema_version": 4,"document_kind":"mixed_receipt"}'
          AND c.result_payload @> '{"schema_version": 4,"document_kind":"mixed_checkout"}'
      ), returns AS (
        SELECT m.tenant_id,m.id,m.parent_sale_id,m.branch_id,j.parent_checkout_id,
          j.stock_command_id,3 AS version,s.completed_at AS occurred_at,'refund'::TEXT AS kind,
          m.receipt_payload->'lines' AS lines,'original_line_id'::TEXT AS identity_key
        FROM scoped_sales s JOIN public.pos_physical_cash_refund m
          ON m.tenant_id=s.tenant_id AND m.sale_id=s.id AND m.branch_id=s.branch_id
          AND m.parent_sale_id=s.parent_sale_id AND m.register_id=s.register_id
          AND m.shift_id=s.shift_id AND m.receipt_payload=s.receipt_snapshot
        JOIN public.pos_physical_return_command j
          ON j.tenant_id=m.tenant_id AND j.id=m.id AND j.parent_sale_id=m.parent_sale_id
          AND j.branch_id=m.branch_id AND j.actor_id=m.actor_id
          AND j.state='prepared_quarantine' AND j.total_amount=s.total_amount
        WHERE s.sale_type='return' AND s.status='completed' AND j.total_amount>0
          AND s.completed_at>=p_from AND s.completed_at<p_to
          AND s.receipt_snapshot @> '{"schema_version": 3,
            "document_kind":"physical_cash_refund_receipt"}'
        UNION ALL
        SELECT m.tenant_id,m.id,m.parent_sale_id,m.branch_id,j.parent_checkout_id,
          j.stock_command_id,4,s.completed_at,'refund',m.receipt_payload->'lines','original_line_id'
        FROM scoped_sales s JOIN public.pos_mixed_cash_refund m
          ON m.tenant_id=s.tenant_id AND m.sale_id=s.id AND m.branch_id=s.branch_id
          AND m.parent_sale_id=s.parent_sale_id AND m.register_id=s.register_id
          AND m.shift_id=s.shift_id AND m.receipt_payload=s.receipt_snapshot
        JOIN public.pos_mixed_return_command j
          ON j.tenant_id=m.tenant_id AND j.id=m.id AND j.parent_sale_id=m.parent_sale_id
          AND j.branch_id=m.branch_id AND j.actor_id=m.actor_id
          AND j.state='prepared_quarantine' AND j.total_amount=s.total_amount
        WHERE s.sale_type='return' AND s.status='completed' AND j.total_amount>0
          AND s.completed_at>=p_from AND s.completed_at<p_to
          AND s.receipt_snapshot @> '{"schema_version": 4,
            "document_kind":"mixed_cash_refund_receipt"}'
        UNION ALL
        SELECT m.tenant_id,m.id,m.parent_sale_id,m.branch_id,j.parent_checkout_id,
          j.stock_command_id,3,m.created_at,'zero_return',m.result_payload->'lines','line_id'
        FROM scoped_sales s JOIN public.pos_physical_zero_return m
          ON m.tenant_id=s.tenant_id AND m.parent_sale_id=s.id AND m.branch_id=s.branch_id
        JOIN public.pos_physical_return_command j
          ON j.tenant_id=m.tenant_id AND j.id=m.id AND j.parent_sale_id=m.parent_sale_id
          AND j.branch_id=m.branch_id AND j.actor_id=m.actor_id
          AND j.state='prepared_quarantine' AND j.total_amount=0
        WHERE s.sale_type='sale' AND s.status='completed'
          AND m.created_at>=p_from AND m.created_at<p_to
        UNION ALL
        SELECT m.tenant_id,m.id,m.parent_sale_id,m.branch_id,j.parent_checkout_id,
          j.stock_command_id,4,m.created_at,'zero_return',m.result_payload->'lines','line_id'
        FROM scoped_sales s JOIN public.pos_mixed_zero_return m
          ON m.tenant_id=s.tenant_id AND m.parent_sale_id=s.id AND m.branch_id=s.branch_id
        JOIN public.pos_mixed_return_command j
          ON j.tenant_id=m.tenant_id AND j.id=m.id AND j.parent_sale_id=m.parent_sale_id
          AND j.branch_id=m.branch_id AND j.actor_id=m.actor_id
          AND j.state='prepared_quarantine' AND j.total_amount=0
        WHERE s.sale_type='sale' AND s.status='completed'
          AND m.created_at>=p_from AND m.created_at<p_to
      ), events AS (
        SELECT s.tenant_id,s.id AS event_id,si.id AS line_id,s.branch_id,
          catalog.brand_name::TEXT AS name,s.completed_at AS occurred_at,
          'legacy'::TEXT AS source_kind,
          CASE WHEN s.sale_type='return' THEN 'refund' ELSE 'sale' END AS event_kind,
          CASE WHEN s.sale_type='return' THEN -si.total_price ELSE si.total_price END AS revenue,
          NULL::NUMERIC AS cost
        FROM scoped_sales s JOIN public.sale_item si ON si.tenant_id=s.tenant_id AND si.sale_id=s.id
        LEFT JOIN public.tenant_catalog catalog
          ON catalog.tenant_id=si.tenant_id AND catalog.id=si.catalog_id
        WHERE s.sale_type IN ('sale','return') AND s.completed_at>=p_from AND s.completed_at<p_to
          AND NOT coalesce(
            s.receipt_snapshot @> '{"schema_version": 3,"document_kind":"physical_receipt"}'
            OR s.receipt_snapshot @> '{"schema_version": 4,"document_kind":"mixed_receipt"}'
            OR s.receipt_snapshot @> '{"schema_version": 3,
              "document_kind":"physical_cash_refund_receipt"}'
            OR s.receipt_snapshot @> '{"schema_version": 4,
              "document_kind":"mixed_cash_refund_receipt"}',false)
        UNION ALL
        SELECT c.tenant_id,c.sale_id,(line->>'id')::UUID,c.branch_id,line->>'name',c.completed_at,
          c.source_kind,'sale',(line->>'net_amount')::NUMERIC,
          CASE WHEN line->>'inventory_kind'='physical_unit' THEN valuation.cost END
        FROM checkouts c CROSS JOIN LATERAL jsonb_array_elements(c.lines) AS line
        LEFT JOIN LATERAL (
          SELECT CASE WHEN count(*)>0 AND bool_and(
            m.cost_version IS NOT DISTINCT FROM 1 AND m.cost_amount IS NOT NULL)
            THEN sum(m.cost_amount) END AS cost
          FROM public.unit_stock_movement_v2 m
          WHERE m.tenant_id=c.tenant_id AND m.command_id=c.stock_command_id
            AND m.line_id=(line->>'id')::UUID AND m.branch_id=c.branch_id AND m.units_taken>0
        ) valuation ON true
        WHERE c.completed_at>=p_from AND c.completed_at<p_to
        UNION ALL
        SELECT e.tenant_id,e.id,(original->>'id')::UUID,e.branch_id,original->>'name',e.occurred_at,
          c.source_kind,e.kind,
          CASE WHEN e.kind='refund' THEN -(returned->>'net_amount')::NUMERIC ELSE 0 END,
          CASE WHEN original->>'inventory_kind'='physical_unit' THEN -valuation.cost END
        FROM returns e JOIN checkouts c
          ON c.tenant_id=e.tenant_id AND c.sale_id=e.parent_sale_id AND c.branch_id=e.branch_id
          AND c.checkout_id=e.parent_checkout_id AND c.stock_command_id=e.stock_command_id
          AND c.version=e.version
        CROSS JOIN LATERAL jsonb_array_elements(e.lines) AS returned
        CROSS JOIN LATERAL jsonb_array_elements(c.lines) AS original
        LEFT JOIN LATERAL (
          SELECT sum(saved.cost_amount) AS cost FROM (
            SELECT r.cost_amount FROM public.pos_physical_return_slice r
            WHERE e.version=3 AND r.tenant_id=e.tenant_id AND r.command_id=e.id
              AND r.line_id=(original->>'id')::UUID AND r.branch_id=e.branch_id
            UNION ALL
            SELECT r.cost_amount FROM public.pos_mixed_return_slice r
            WHERE e.version=4 AND r.tenant_id=e.tenant_id AND r.command_id=e.id
              AND r.line_id=(original->>'id')::UUID AND r.branch_id=e.branch_id
          ) saved
        ) valuation ON true
        WHERE original->>'id'=returned->>e.identity_key
        UNION ALL
        SELECT w.tenant_id,w.id,NULL::UUID,w.branch_id,catalog.brand_name::TEXT,w.created_at,
          'legacy','writeoff',0::NUMERIC,w.amount
        FROM authorized a JOIN public.write_off w ON w.tenant_id=a.tenant_id
        JOIN public.batch b ON b.tenant_id=w.tenant_id AND b.id=w.batch_id
        LEFT JOIN public.tenant_catalog catalog
          ON catalog.tenant_id=b.tenant_id AND catalog.id=b.catalog_id
        WHERE w.created_at>=p_from AND w.created_at<p_to
          AND (p_branch IS NULL OR w.branch_id=p_branch)
          AND coalesce(public.tenant_actor_has_scoped_permission(
            w.tenant_id,'reports.view',w.branch_id),false)
        UNION ALL
        SELECT w.tenant_id,w.id,NULL::UUID,w.branch_id,catalog.brand_name::TEXT,w.created_at,
          'physical','writeoff',0::NUMERIC,w.total_cost
        FROM authorized a JOIN public.physical_writeoff_command w ON w.tenant_id=a.tenant_id
        JOIN public.unit_stock_lot_v2 lot ON lot.tenant_id=w.tenant_id AND lot.id=w.lot_id
        LEFT JOIN public.tenant_catalog catalog
          ON catalog.tenant_id=lot.tenant_id AND catalog.id=lot.catalog_id
        WHERE w.created_at>=p_from AND w.created_at<p_to
          AND (p_branch IS NULL OR w.branch_id=p_branch)
          AND coalesce(public.tenant_actor_has_scoped_permission(
            w.tenant_id,'reports.view',w.branch_id),false)
      )
      SELECT e.event_id,e.line_id,e.branch_id,b.name::TEXT,coalesce(e.name,'Без названия'),
        e.occurred_at,e.source_kind,e.event_kind,e.revenue,
        CASE WHEN rights.can_cost THEN e.cost END,
        CASE WHEN rights.can_cost AND e.event_kind<>'writeoff' THEN e.revenue-e.cost END,
        CASE WHEN NOT rights.can_cost THEN 'hidden'
          WHEN e.cost IS NULL THEN 'unknown' ELSE 'known' END
      FROM events e JOIN public.branch b ON b.tenant_id=e.tenant_id AND b.id=e.branch_id
      CROSS JOIN LATERAL (SELECT coalesce(public.tenant_actor_has_scoped_permission(
        e.tenant_id,'batches.view_costs',e.branch_id),false) AS can_cost) rights
      $$
    """)
    op.execute(f"ALTER FUNCTION {SIGNATURE} OWNER TO aurum_schema_owner")
    op.execute(f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC,aurum_app,aurum_support")
    op.execute(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO aurum_app,aurum_support")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION {SIGNATURE}")
