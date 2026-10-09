"""Cost-free, branch-scoped physical customer-return queue.

Revision ID: 0187
Revises: 0186
"""

from alembic import op

revision = "0187"
down_revision = "0186"
branch_labels = None
depends_on = None

SIGNATURE = "public.physical_customer_return_queue(UUID,UUID,TEXT)"


def upgrade() -> None:
    # A warehouse reader does not acquire pos.refund or access to raw cost
    # journals. The definer projects only this queue's explicitly named facts.
    op.execute(r"""
      CREATE FUNCTION public.physical_customer_return_queue(
        p_tenant UUID, p_branch UUID DEFAULT NULL, p_search TEXT DEFAULT NULL
      ) RETURNS TABLE (
        operation_id UUID, line_id UUID, lot_id UUID, branch_id UUID,
        branch_name TEXT, report_timezone TEXT, parent_sale_id UUID, parent_receipt_number TEXT,
        return_sale_id UUID, return_receipt_number TEXT, catalog_id UUID,
        catalog_name TEXT, base_units BIGINT, packaging JSONB,
        batch_number TEXT, expires_at DATE, received_at TIMESTAMPTZ,
        refund_reason TEXT, refund_comment TEXT, intake_kind TEXT
      ) LANGUAGE SQL STABLE SECURITY DEFINER
      SET search_path=pg_catalog,pg_temp AS $$
        WITH authorized AS MATERIALIZED (
          SELECT p_tenant AS tenant_id
          WHERE session_user='aurum_app'
            AND NOT coalesce(public.is_support_session(),true)
            AND NOT coalesce(public.is_tenant_support_session(),true)
            AND p_tenant IS NOT NULL
            AND p_tenant=public.current_tenant_id()
            AND public.current_app_user_id() IS NOT NULL
            AND EXISTS (SELECT 1 FROM public.app_user u
              WHERE u.id=public.current_app_user_id() AND u.status='active')
            AND (p_search IS NULL OR char_length(p_search)<=160)
        ), markers AS (
          SELECT m.tenant_id,m.id,m.parent_sale_id,m.branch_id,m.sale_id,
            r.receipt_number,m.created_at,m.reason_code,m.comment,
            'cash_refund'::TEXT AS intake_kind
          FROM authorized a
          JOIN public.pos_physical_cash_refund m ON m.tenant_id=a.tenant_id
          JOIN public.sale r ON r.tenant_id=m.tenant_id AND r.id=m.sale_id
            AND r.parent_sale_id=m.parent_sale_id AND r.branch_id=m.branch_id
            AND r.status='completed' AND r.sale_type='return' AND NOT r.is_test
          WHERE (p_branch IS NULL OR m.branch_id=p_branch)
            AND coalesce(public.tenant_actor_has_scoped_permission(
              m.tenant_id,'customer_returns.view',m.branch_id),false)
          UNION ALL
          SELECT m.tenant_id,m.id,m.parent_sale_id,m.branch_id,NULL::UUID,
            NULL::TEXT,m.created_at,m.reason_code,m.comment,'zero_return'::TEXT
          FROM authorized a
          JOIN public.pos_physical_zero_return m ON m.tenant_id=a.tenant_id
          WHERE (p_branch IS NULL OR m.branch_id=p_branch)
            AND coalesce(public.tenant_actor_has_scoped_permission(
              m.tenant_id,'customer_returns.view',m.branch_id),false)
        ), quantities AS (
          SELECT s.tenant_id,s.command_id,s.line_id,s.lot_id,
            sum(s.base_units)::BIGINT AS base_units
          FROM markers m
          JOIN public.pos_physical_return_slice s
            ON s.tenant_id=m.tenant_id AND s.command_id=m.id AND s.branch_id=m.branch_id
          GROUP BY s.tenant_id,s.command_id,s.line_id,s.lot_id
        )
        SELECT m.id,l.line_id,q.lot_id,m.branch_id,b.name::TEXT,settings.report_timezone::TEXT,m.parent_sale_id,
          p.receipt_number::TEXT,m.sale_id,m.receipt_number::TEXT,l.catalog_id,
          historical.value->>'name',q.base_units,
          historical.value->'packaging',i.batch_number::TEXT,lot.expires_at,
          m.created_at,m.reason_code,m.comment,m.intake_kind
        FROM markers m
        JOIN public.sale p ON p.tenant_id=m.tenant_id AND p.id=m.parent_sale_id
          AND p.branch_id=m.branch_id AND p.status='completed'
          AND p.sale_type='sale' AND NOT p.is_test
        JOIN public.branch b ON b.tenant_id=m.tenant_id AND b.id=m.branch_id
        JOIN public.tenant_settings settings ON settings.tenant_id=m.tenant_id
        JOIN public.pos_physical_return_command c ON c.tenant_id=m.tenant_id AND c.id=m.id
          AND c.parent_sale_id=m.parent_sale_id AND c.branch_id=m.branch_id
          AND c.state='prepared_quarantine'
        JOIN public.pos_physical_checkout checkout
          ON checkout.tenant_id=c.tenant_id AND checkout.id=c.parent_checkout_id
          AND checkout.sale_id=p.id AND checkout.branch_id=m.branch_id
        JOIN public.pos_physical_return_line l ON l.tenant_id=c.tenant_id
          AND l.command_id=c.id AND l.branch_id=m.branch_id
        JOIN quantities q ON q.tenant_id=l.tenant_id AND q.command_id=l.command_id
          AND q.line_id=l.line_id
        JOIN public.unit_stock_lot_v2 lot ON lot.tenant_id=q.tenant_id AND lot.id=q.lot_id
          AND lot.branch_id=m.branch_id AND lot.catalog_id=l.catalog_id
        LEFT JOIN public.incoming_item i ON i.tenant_id=lot.tenant_id
          AND i.id=lot.source_item_id AND i.catalog_id=lot.catalog_id
        CROSS JOIN LATERAL jsonb_array_elements(checkout.result_payload->'lines') historical(value)
        WHERE historical.value->>'id'=l.line_id::TEXT
          AND historical.value->>'catalog_id'=l.catalog_id::TEXT
          AND (p_search IS NULL OR btrim(p_search)=''
            OR strpos(lower(historical.value->>'name'),lower(btrim(p_search)))>0
            OR strpos(lower(coalesce(i.batch_number,'')),lower(btrim(p_search)))>0
            OR strpos(lower(coalesce(p.receipt_number,'')),lower(btrim(p_search)))>0
            OR strpos(lower(coalesce(m.receipt_number,'')),lower(btrim(p_search)))>0)
      $$
    """)
    op.execute(f"ALTER FUNCTION {SIGNATURE} OWNER TO aurum_schema_owner")
    op.execute(f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC,aurum_app,aurum_support")
    op.execute(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO aurum_app")


def downgrade() -> None:
    # Removes only a read interface, preserving all financial/physical history.
    op.execute(f"DROP FUNCTION {SIGNATURE}")
