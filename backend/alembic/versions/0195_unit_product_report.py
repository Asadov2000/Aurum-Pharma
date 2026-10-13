"""Cost-free product totals for legacy, physical and mixed sales and returns.

Revision ID: 0195
Revises: 0194
"""

from alembic import op

revision = "0195"
down_revision = "0194"
branch_labels = None
depends_on = None

SIGNATURE = "public.pos_product_totals_for_report(UUID,TIMESTAMPTZ,TIMESTAMPTZ,UUID)"


def upgrade() -> None:
    # Raw checkout/return journals retain their existing RLS and cost protection.
    # Only aggregated retail facts leave this tenant- and branch-scoped function.
    op.execute(r"""
      CREATE FUNCTION public.pos_product_totals_for_report(
        p_tenant UUID, p_from TIMESTAMPTZ, p_to TIMESTAMPTZ, p_branch UUID DEFAULT NULL
      ) RETURNS TABLE (
        catalog_id UUID, quantity NUMERIC, physical_quantities JSONB,
        revenue NUMERIC, receipts_count BIGINT, currency TEXT
      ) LANGUAGE SQL STABLE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp AS $$
        WITH authorized AS MATERIALIZED (
          SELECT p_tenant AS tenant_id
          WHERE session_user IN ('aurum_app','aurum_support')
            AND p_tenant IS NOT NULL AND p_tenant=public.current_tenant_id()
            AND public.current_app_user_id() IS NOT NULL
            AND EXISTS (SELECT 1 FROM public.app_user u
              WHERE u.id=public.current_app_user_id() AND u.status='active')
            AND p_from IS NOT NULL AND p_to>p_from
            AND p_to-p_from<=interval '367 days'
        ), scoped_sales AS NOT MATERIALIZED (
          SELECT s.* FROM authorized a
          JOIN public.sale s ON s.tenant_id=a.tenant_id
          WHERE s.status IN ('completed','voided') AND NOT s.is_test
            AND (p_branch IS NULL OR s.branch_id=p_branch)
            AND coalesce(public.tenant_actor_has_scoped_permission(
              s.tenant_id,'reports.view',s.branch_id),false)
        ), checkouts AS NOT MATERIALIZED (
          SELECT s.id AS sale_id,c.id AS checkout_id,c.stock_command_id,s.tenant_id,
            s.branch_id,s.completed_at,c.result_payload->'lines' AS lines,3 AS version
          FROM scoped_sales s JOIN public.pos_physical_checkout c
            ON c.tenant_id=s.tenant_id AND c.sale_id=s.id AND c.branch_id=s.branch_id
              AND c.register_id=s.register_id AND c.shift_id=s.shift_id
              AND c.cashier_user_id=s.cashier_user_id AND c.receipt_payload=s.receipt_snapshot
          WHERE s.sale_type='sale' AND s.status='completed'
            AND s.receipt_snapshot @> '{"schema_version": 3,"document_kind": "physical_receipt"}'
            AND c.result_payload @> '{"schema_version": 3,"document_kind": "physical_checkout"}'
          UNION ALL
          SELECT s.id,c.id,c.stock_command_id,s.tenant_id,s.branch_id,s.completed_at,
            c.result_payload->'lines',4
          FROM scoped_sales s JOIN public.pos_mixed_checkout c
            ON c.tenant_id=s.tenant_id AND c.sale_id=s.id AND c.branch_id=s.branch_id
              AND c.register_id=s.register_id AND c.shift_id=s.shift_id
              AND c.cashier_user_id=s.cashier_user_id AND c.receipt_payload=s.receipt_snapshot
          WHERE s.sale_type='sale' AND s.status='completed'
            AND s.receipt_snapshot @> '{"schema_version": 4,"document_kind": "mixed_receipt"}'
            AND c.result_payload @> '{"schema_version": 4,"document_kind": "mixed_checkout"}'
        ), return_events AS (
          SELECT m.tenant_id,m.parent_sale_id,m.branch_id,j.parent_checkout_id,
            j.stock_command_id,3 AS version,m.receipt_payload->'lines' AS lines,
            'original_line_id' AS identity_key,true AS cash
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
              "document_kind": "physical_cash_refund_receipt"}'
          UNION ALL
          SELECT m.tenant_id,m.parent_sale_id,m.branch_id,j.parent_checkout_id,
            j.stock_command_id,4,m.receipt_payload->'lines','original_line_id',true
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
              "document_kind": "mixed_cash_refund_receipt"}'
          UNION ALL
          SELECT m.tenant_id,m.parent_sale_id,m.branch_id,j.parent_checkout_id,
            j.stock_command_id,3,m.result_payload->'lines','line_id',false
          FROM scoped_sales s JOIN public.pos_physical_zero_return m
            ON m.tenant_id=s.tenant_id AND m.parent_sale_id=s.id AND m.branch_id=s.branch_id
          JOIN public.pos_physical_return_command j
            ON j.tenant_id=m.tenant_id AND j.id=m.id AND j.parent_sale_id=m.parent_sale_id
              AND j.branch_id=m.branch_id AND j.actor_id=m.actor_id
              AND j.state='prepared_quarantine' AND j.total_amount=0
          WHERE s.sale_type='sale' AND s.status='completed'
            AND m.created_at>=p_from AND m.created_at<p_to
          UNION ALL
          SELECT m.tenant_id,m.parent_sale_id,m.branch_id,j.parent_checkout_id,
            j.stock_command_id,4,m.result_payload->'lines','line_id',false
          FROM scoped_sales s JOIN public.pos_mixed_zero_return m
            ON m.tenant_id=s.tenant_id AND m.parent_sale_id=s.id AND m.branch_id=s.branch_id
          JOIN public.pos_mixed_return_command j
            ON j.tenant_id=m.tenant_id AND j.id=m.id AND j.parent_sale_id=m.parent_sale_id
              AND j.branch_id=m.branch_id AND j.actor_id=m.actor_id
              AND j.state='prepared_quarantine' AND j.total_amount=0
          WHERE s.sale_type='sale' AND s.status='completed'
            AND m.created_at>=p_from AND m.created_at<p_to
        ), report_lines AS (
          SELECT si.catalog_id,
            CASE WHEN s.sale_type='return' THEN -si.qty ELSE si.qty END AS pack_quantity,
            NULL::TEXT AS unit,0::NUMERIC AS unit_quantity,
            CASE WHEN s.sale_type='return' THEN -si.total_price ELSE si.total_price END AS amount,
            CASE WHEN s.sale_type='sale' THEN s.id END AS receipt_id
          FROM scoped_sales s JOIN public.sale_item si
            ON si.tenant_id=s.tenant_id AND si.sale_id=s.id
          WHERE s.sale_type IN ('sale','return')
            AND s.completed_at>=p_from AND s.completed_at<p_to
            AND NOT coalesce(
              s.receipt_snapshot @> '{"schema_version": 3,"document_kind": "physical_receipt"}'
              OR s.receipt_snapshot @> '{"schema_version": 4,"document_kind": "mixed_receipt"}'
              OR s.receipt_snapshot @> '{"schema_version": 3,
                "document_kind": "physical_cash_refund_receipt"}'
              OR s.receipt_snapshot @> '{"schema_version": 4,
                "document_kind": "mixed_cash_refund_receipt"}',false)
          UNION ALL
          SELECT (line->>'catalog_id')::UUID,
            CASE WHEN line->>'inventory_kind'='legacy_pack'
              THEN (line->>'quantity')::NUMERIC ELSE 0 END,
            CASE WHEN line->>'inventory_kind'='physical_unit' THEN line->'packaging'->>'unit' END,
            CASE WHEN line->>'inventory_kind'='physical_unit'
              THEN (line->>'base_units')::NUMERIC ELSE 0 END,
            (line->>'net_amount')::NUMERIC,c.sale_id
          FROM checkouts c CROSS JOIN LATERAL jsonb_array_elements(c.lines) AS line
          WHERE c.completed_at>=p_from AND c.completed_at<p_to
          UNION ALL
          SELECT (original->>'catalog_id')::UUID,
            CASE WHEN original->>'inventory_kind'='legacy_pack'
              THEN -(returned->>'quantity')::NUMERIC ELSE 0 END,
            CASE WHEN original->>'inventory_kind'='physical_unit'
              THEN original->'packaging'->>'unit' END,
            CASE WHEN original->>'inventory_kind'='physical_unit'
              THEN -(returned->>'base_units')::NUMERIC ELSE 0 END,
            CASE WHEN e.cash THEN -(returned->>'net_amount')::NUMERIC ELSE 0 END,NULL::UUID
          FROM return_events e JOIN checkouts c
            ON c.tenant_id=e.tenant_id AND c.sale_id=e.parent_sale_id
              AND c.branch_id=e.branch_id AND c.checkout_id=e.parent_checkout_id
              AND c.stock_command_id=e.stock_command_id AND c.version=e.version
          CROSS JOIN LATERAL jsonb_array_elements(e.lines) AS returned
          CROSS JOIN LATERAL jsonb_array_elements(c.lines) AS original
          WHERE original->>'id'=returned->>e.identity_key
        ), physical AS (
          SELECT l.catalog_id,l.unit,sum(l.unit_quantity) AS units
          FROM report_lines l WHERE l.unit IS NOT NULL GROUP BY l.catalog_id,l.unit
          HAVING sum(l.unit_quantity)<>0
        ), physical_totals AS (
          SELECT p.catalog_id,jsonb_agg(jsonb_build_object(
            'unit',p.unit,'quantity',p.units::TEXT) ORDER BY p.unit) AS quantities
          FROM physical p GROUP BY p.catalog_id
        ), totals AS (
          SELECT l.catalog_id,sum(l.pack_quantity) AS quantity,sum(l.amount) AS revenue,
            count(DISTINCT l.receipt_id) AS receipts_count
          FROM report_lines l GROUP BY l.catalog_id
        )
        SELECT t.catalog_id,t.quantity,coalesce(p.quantities,'[]'::JSONB),
          t.revenue,t.receipts_count,'TJS'::TEXT
        FROM totals t LEFT JOIN physical_totals p ON p.catalog_id=t.catalog_id
        WHERE t.quantity<>0 OR t.revenue<>0 OR p.quantities IS NOT NULL
      $$;
    """)
    op.execute(f"ALTER FUNCTION {SIGNATURE} OWNER TO aurum_schema_owner")
    op.execute(f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC,aurum_app,aurum_support")
    op.execute(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO aurum_app,aurum_support")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION {SIGNATURE}")
