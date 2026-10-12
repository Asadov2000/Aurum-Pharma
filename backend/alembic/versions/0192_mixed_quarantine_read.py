"""Cost-free, branch-scoped confirmed mixed customer-return queue.

Revision ID: 0192
Revises: 0191
"""

from alembic import op

revision = "0192"
down_revision = "0191"
branch_labels = None
depends_on = None

SIGNATURE = "public.mixed_customer_return_queue(UUID,UUID,TEXT)"


def upgrade() -> None:
    # Warehouse readers receive only this explicitly projected display interface;
    # no raw journal policy or grant is added, and no stock or cash writer is called.
    op.execute(r"""
      CREATE FUNCTION public.mixed_customer_return_queue(
        p_tenant UUID, p_branch UUID DEFAULT NULL, p_search TEXT DEFAULT NULL
      ) RETURNS TABLE (
        operation_id UUID, line_id UUID, inventory_kind TEXT, batch_id UUID, lot_id UUID,
        branch_id UUID, branch_name TEXT, report_timezone TEXT,
        parent_sale_id UUID, parent_receipt_number TEXT,
        return_sale_id UUID, return_receipt_number TEXT, catalog_id UUID, catalog_name TEXT,
        legacy_quantity NUMERIC, base_units BIGINT, packaging JSONB,
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
          SELECT m.tenant_id,m.id,m.parent_sale_id,m.branch_id,m.actor_id,m.sale_id,
            r.receipt_number,m.created_at,m.reason_code,m.comment,
            'cash_refund'::TEXT AS intake_kind
          FROM authorized a
          JOIN public.pos_mixed_cash_refund m ON m.tenant_id=a.tenant_id
          JOIN public.sale r ON r.tenant_id=m.tenant_id AND r.id=m.sale_id
            AND r.parent_sale_id=m.parent_sale_id AND r.branch_id=m.branch_id
            AND r.status='completed' AND r.sale_type='return' AND NOT r.is_test
            AND r.operation_id=m.id AND r.cashier_user_id=m.actor_id
          WHERE (p_branch IS NULL OR m.branch_id=p_branch)
            AND coalesce(public.tenant_actor_has_scoped_permission(
              m.tenant_id,'customer_returns.view',m.branch_id),false)
          UNION ALL
          SELECT m.tenant_id,m.id,m.parent_sale_id,m.branch_id,m.actor_id,NULL::UUID,
            NULL::TEXT,m.created_at,m.reason_code,m.comment,'zero_return'::TEXT
          FROM authorized a
          JOIN public.pos_mixed_zero_return m ON m.tenant_id=a.tenant_id
          WHERE (p_branch IS NULL OR m.branch_id=p_branch)
            AND coalesce(public.tenant_actor_has_scoped_permission(
              m.tenant_id,'customer_returns.view',m.branch_id),false)
        ), source AS (
          SELECT m.tenant_id,m.id AS operation_id,l.line_id,l.inventory_kind,
            m.branch_id,b.name::TEXT AS branch_name,settings.report_timezone::TEXT,
            m.parent_sale_id,p.receipt_number::TEXT AS parent_receipt_number,
            m.sale_id AS return_sale_id,m.receipt_number::TEXT AS return_receipt_number,
            l.catalog_id,historical.value->>'name' AS catalog_name,
            l.batch_id,l.legacy_quantity,historical.value->'packaging' AS packaging,
            m.created_at AS received_at,m.reason_code AS refund_reason,
            m.comment AS refund_comment,m.intake_kind
          FROM markers m
          JOIN public.sale p ON p.tenant_id=m.tenant_id AND p.id=m.parent_sale_id
            AND p.branch_id=m.branch_id AND p.status='completed'
            AND p.sale_type='sale' AND NOT p.is_test AND p.parent_sale_id IS NULL
          JOIN public.branch b ON b.tenant_id=m.tenant_id AND b.id=m.branch_id
          JOIN public.tenant_settings settings ON settings.tenant_id=m.tenant_id
          JOIN public.pos_mixed_return_command c ON c.tenant_id=m.tenant_id AND c.id=m.id
            AND c.parent_sale_id=m.parent_sale_id AND c.branch_id=m.branch_id
            AND c.actor_id=m.actor_id AND c.state='prepared_quarantine'
            AND (m.intake_kind<>'zero_return' OR c.total_amount=0)
          JOIN public.pos_mixed_checkout checkout ON checkout.tenant_id=c.tenant_id
            AND checkout.id=c.parent_checkout_id AND checkout.sale_id=p.id
            AND checkout.branch_id=m.branch_id AND checkout.stock_command_id=c.stock_command_id
          JOIN public.pos_mixed_return_line l ON l.tenant_id=c.tenant_id
            AND l.command_id=c.id AND l.branch_id=m.branch_id
          CROSS JOIN LATERAL
            jsonb_array_elements(checkout.result_payload->'lines') historical(value)
          WHERE checkout.result_payload->>'schema_version'='4'
            AND checkout.result_payload->>'document_kind'='mixed_checkout'
            AND historical.value->>'id'=l.line_id::TEXT
            AND historical.value->>'catalog_id'=l.catalog_id::TEXT
            AND historical.value->>'inventory_kind'=l.inventory_kind
            AND historical.value->>'position'=l.position::TEXT
            AND (l.inventory_kind<>'legacy_pack'
              OR historical.value->>'batch_id'=l.batch_id::TEXT)
        ), quantities AS (
          SELECT s.tenant_id,s.command_id,s.line_id,s.lot_id,
            sum(s.base_units)::BIGINT AS base_units
          FROM source l
          JOIN public.pos_mixed_return_slice s ON s.tenant_id=l.tenant_id
            AND s.command_id=l.operation_id AND s.line_id=l.line_id AND s.branch_id=l.branch_id
          WHERE l.inventory_kind='physical_unit'
          GROUP BY s.tenant_id,s.command_id,s.line_id,s.lot_id
        ), projected AS (
          SELECT l.operation_id,l.line_id,l.inventory_kind,l.batch_id,NULL::UUID AS lot_id,
            l.branch_id,l.branch_name,l.report_timezone,l.parent_sale_id,l.parent_receipt_number,
            l.return_sale_id,l.return_receipt_number,l.catalog_id,l.catalog_name,
            l.legacy_quantity,NULL::BIGINT AS base_units,NULL::JSONB AS packaging,
            batch.batch_number::TEXT,batch.expires_at,l.received_at,l.refund_reason,
            l.refund_comment,l.intake_kind
          FROM source l
          JOIN public.batch batch ON batch.tenant_id=l.tenant_id AND batch.id=l.batch_id
            AND batch.branch_id=l.branch_id AND batch.catalog_id=l.catalog_id
          WHERE l.inventory_kind='legacy_pack'
          UNION ALL
          SELECT l.operation_id,l.line_id,l.inventory_kind,NULL::UUID,q.lot_id,
            l.branch_id,l.branch_name,l.report_timezone,l.parent_sale_id,l.parent_receipt_number,
            l.return_sale_id,l.return_receipt_number,l.catalog_id,l.catalog_name,
            NULL::NUMERIC,q.base_units,l.packaging,i.batch_number::TEXT,lot.expires_at,
            l.received_at,l.refund_reason,l.refund_comment,l.intake_kind
          FROM source l
          JOIN quantities q ON q.tenant_id=l.tenant_id AND q.command_id=l.operation_id
            AND q.line_id=l.line_id
          JOIN public.unit_stock_lot_v2 lot ON lot.tenant_id=q.tenant_id AND lot.id=q.lot_id
            AND lot.branch_id=l.branch_id AND lot.catalog_id=l.catalog_id
          LEFT JOIN public.incoming_item i ON i.tenant_id=lot.tenant_id
            AND i.id=lot.source_item_id AND i.catalog_id=lot.catalog_id
          WHERE l.inventory_kind='physical_unit'
        )
        SELECT operation_id,line_id,inventory_kind,batch_id,lot_id,
          branch_id,branch_name,report_timezone,parent_sale_id,parent_receipt_number,
          return_sale_id,return_receipt_number,catalog_id,catalog_name,legacy_quantity,
          base_units,packaging,batch_number,expires_at,received_at,
          refund_reason,refund_comment,intake_kind
        FROM projected
        WHERE p_search IS NULL OR btrim(p_search)=''
          OR strpos(lower(catalog_name),lower(btrim(p_search)))>0
          OR strpos(lower(coalesce(batch_number,'')),lower(btrim(p_search)))>0
          OR strpos(lower(coalesce(parent_receipt_number,'')),lower(btrim(p_search)))>0
          OR strpos(lower(coalesce(return_receipt_number,'')),lower(btrim(p_search)))>0
      $$
    """)
    op.execute(f"ALTER FUNCTION {SIGNATURE} OWNER TO aurum_schema_owner")
    op.execute(f"REVOKE ALL ON FUNCTION {SIGNATURE} FROM PUBLIC,aurum_app,aurum_support")
    op.execute(f"GRANT EXECUTE ON FUNCTION {SIGNATURE} TO aurum_app")


def downgrade() -> None:
    # Only removes the read interface. Every intake, payment and stock fact remains.
    op.execute(f"DROP FUNCTION {SIGNATURE}")
