"""optimize local Edge read projections

Revision ID: 0149
Revises: 0148
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0149"
down_revision: str | Sequence[str] | None = "0148"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(r"""
      CREATE FUNCTION public.search_sync_read_catalog(
        p_snapshot_id UUID,
        p_query TEXT
      ) RETURNS TABLE (catalog_id UUID)
      LANGUAGE plpgsql
      STABLE
      SECURITY DEFINER
      SET search_path = pg_catalog, pg_temp
      SET row_security = off
      AS $$
      DECLARE
        v_tenant_id UUID;
        v_branch_id UUID;
        v_node_id UUID;
        v_pattern TEXT;
      BEGIN
        v_tenant_id := public.current_tenant_id();
        v_branch_id := NULLIF(current_setting('app.branch_id', true), '')::UUID;
        v_node_id := NULLIF(current_setting('app.edge_node_id', true), '')::UUID;

        IF v_tenant_id IS NULL OR v_branch_id IS NULL OR v_node_id IS NULL
           OR p_snapshot_id IS NULL OR p_query IS NULL OR p_query = '' THEN
          RETURN;
        END IF;

        IF NOT EXISTS (
          SELECT 1
          FROM public.sync_read_projection AS projection
          WHERE projection.snapshot_id = p_snapshot_id
            AND projection.tenant_id = v_tenant_id
            AND projection.branch_id = v_branch_id
            AND projection.node_id = v_node_id
        ) THEN
          RETURN;
        END IF;

        v_pattern := '%' || replace(
          replace(replace(lower(p_query), '\', '\\'), '%', '\%'), '_', '\_'
        ) || '%';

        RETURN QUERY
        SELECT matched.catalog_id
        FROM (
          SELECT catalog.catalog_id
          FROM public.sync_read_catalog_projection AS catalog
          WHERE catalog.snapshot_id = p_snapshot_id
            AND catalog.search_text ILIKE v_pattern ESCAPE '\'
          UNION
          SELECT barcode.catalog_id
          FROM public.sync_read_barcode_projection AS barcode
          WHERE barcode.snapshot_id = p_snapshot_id
            AND barcode.code ILIKE v_pattern ESCAPE '\'
        ) AS matched;
      END;
      $$
    """)
    op.execute("""
      ALTER FUNCTION public.search_sync_read_catalog(UUID, TEXT)
      OWNER TO aurum_schema_owner
    """)
    op.execute("""
      REVOKE ALL PRIVILEGES ON FUNCTION
        public.search_sync_read_catalog(UUID, TEXT)
      FROM PUBLIC, aurum_support
    """)
    op.execute("""
      GRANT EXECUTE ON FUNCTION public.search_sync_read_catalog(UUID, TEXT)
      TO aurum_app
    """)

    op.execute("""
      CREATE TABLE public.sync_read_inventory_rollup (
        snapshot_id UUID NOT NULL,
        tenant_id UUID NOT NULL,
        branch_id UUID NOT NULL,
        node_id UUID NOT NULL,
        has_stock BOOLEAN NOT NULL,
        expires_at DATE NOT NULL,
        batch_count BIGINT NOT NULL,
        total_qty NUMERIC(18,3) NOT NULL,
        purchase_value NUMERIC(14,2) NOT NULL,
        sale_value NUMERIC(14,2) NOT NULL,
        blocked_count BIGINT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
        created_by UUID,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
        updated_by UUID,
        CONSTRAINT pk_sync_read_inventory_rollup
          PRIMARY KEY (snapshot_id, has_stock, expires_at),
        CONSTRAINT ck_sync_read_inventory_rollup_batch_count
          CHECK (batch_count > 0),
        CONSTRAINT ck_sync_read_inventory_rollup_blocked_count
          CHECK (blocked_count >= 0),
        CONSTRAINT ck_sync_read_inventory_rollup_blocked_within_total
          CHECK (blocked_count <= batch_count),
        CONSTRAINT fk_sync_read_inventory_rollup_header FOREIGN KEY (
          snapshot_id, tenant_id, branch_id, node_id
        ) REFERENCES public.sync_read_projection (
          snapshot_id, tenant_id, branch_id, node_id
        ) ON DELETE RESTRICT
      )
    """)
    op.execute("""
      INSERT INTO public.sync_read_inventory_rollup (
        snapshot_id, tenant_id, branch_id, node_id, has_stock, expires_at,
        batch_count, total_qty, purchase_value, sale_value, blocked_count
      )
      SELECT
        snapshot_id, tenant_id, branch_id, node_id, qty_remaining > 0, expires_at,
        count(*), sum(qty_remaining), sum(qty_remaining * purchase_price),
        sum(qty_remaining * sale_price), count(*) FILTER (WHERE is_blocked)
      FROM public.sync_read_inventory_projection
      GROUP BY snapshot_id, tenant_id, branch_id, node_id,
               qty_remaining > 0, expires_at
    """)
    op.execute("""
      DO $$
      BEGIN
        IF EXISTS (
          (SELECT snapshot_id, tenant_id, branch_id, node_id,
                  qty_remaining > 0 AS has_stock, expires_at,
                  count(*) AS batch_count, sum(qty_remaining) AS total_qty,
                  sum(qty_remaining * purchase_price) AS purchase_value,
                  sum(qty_remaining * sale_price) AS sale_value,
                  count(*) FILTER (WHERE is_blocked) AS blocked_count
           FROM public.sync_read_inventory_projection
           GROUP BY snapshot_id, tenant_id, branch_id, node_id,
                    qty_remaining > 0, expires_at
           EXCEPT
           SELECT snapshot_id, tenant_id, branch_id, node_id, has_stock, expires_at,
                  batch_count, total_qty, purchase_value, sale_value, blocked_count
           FROM public.sync_read_inventory_rollup)
          UNION ALL
          (SELECT snapshot_id, tenant_id, branch_id, node_id, has_stock, expires_at,
                  batch_count, total_qty, purchase_value, sale_value, blocked_count
           FROM public.sync_read_inventory_rollup
           EXCEPT
           SELECT snapshot_id, tenant_id, branch_id, node_id,
                  qty_remaining > 0 AS has_stock, expires_at,
                  count(*) AS batch_count, sum(qty_remaining) AS total_qty,
                  sum(qty_remaining * purchase_price) AS purchase_value,
                  sum(qty_remaining * sale_price) AS sale_value,
                  count(*) FILTER (WHERE is_blocked) AS blocked_count
           FROM public.sync_read_inventory_projection
           GROUP BY snapshot_id, tenant_id, branch_id, node_id,
                    qty_remaining > 0, expires_at)
        ) THEN
          RAISE EXCEPTION 'Inventory rollup backfill is inconsistent'
            USING ERRCODE = '23514';
        END IF;
      END;
      $$
    """)
    op.execute("""
      CREATE FUNCTION public.trg_guard_sync_read_inventory_rollup_ready()
      RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
      SET search_path = pg_catalog, pg_temp AS $$
      BEGIN
        IF OLD.status = 'building' AND NEW.status = 'ready' AND EXISTS (
          (SELECT p.qty_remaining > 0 AS has_stock, p.expires_at,
                  count(*) AS batch_count, sum(p.qty_remaining) AS total_qty,
                  sum(p.qty_remaining * p.purchase_price) AS purchase_value,
                  sum(p.qty_remaining * p.sale_price) AS sale_value,
                  count(*) FILTER (WHERE p.is_blocked) AS blocked_count
           FROM public.sync_read_inventory_projection p
           WHERE p.snapshot_id = NEW.snapshot_id
           GROUP BY p.qty_remaining > 0, p.expires_at
           EXCEPT
           SELECT r.has_stock, r.expires_at, r.batch_count, r.total_qty,
                  r.purchase_value, r.sale_value, r.blocked_count
           FROM public.sync_read_inventory_rollup r
           WHERE r.snapshot_id = NEW.snapshot_id)
          UNION ALL
          (SELECT r.has_stock, r.expires_at, r.batch_count, r.total_qty,
                  r.purchase_value, r.sale_value, r.blocked_count
           FROM public.sync_read_inventory_rollup r
           WHERE r.snapshot_id = NEW.snapshot_id
           EXCEPT
           SELECT p.qty_remaining > 0 AS has_stock, p.expires_at,
                  count(*) AS batch_count, sum(p.qty_remaining) AS total_qty,
                  sum(p.qty_remaining * p.purchase_price) AS purchase_value,
                  sum(p.qty_remaining * p.sale_price) AS sale_value,
                  count(*) FILTER (WHERE p.is_blocked) AS blocked_count
           FROM public.sync_read_inventory_projection p
           WHERE p.snapshot_id = NEW.snapshot_id
           GROUP BY p.qty_remaining > 0, p.expires_at)
        ) THEN
          RAISE EXCEPTION 'Inventory rollup does not match immutable snapshot content'
            USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
      END;
      $$
    """)
    op.execute("""
      CREATE TRIGGER trg_guard_sync_read_inventory_rollup_ready
      BEFORE UPDATE OF status ON public.sync_read_projection
      FOR EACH ROW EXECUTE FUNCTION
        public.trg_guard_sync_read_inventory_rollup_ready()
    """)
    op.execute("""
      CREATE TRIGGER trg_guard_sync_read_inventory_rollup
      BEFORE INSERT OR UPDATE OR DELETE ON public.sync_read_inventory_rollup
      FOR EACH ROW EXECUTE FUNCTION public.trg_guard_sync_read_projection_rows()
    """)
    op.execute("""
      CREATE FUNCTION public.trg_audit_sync_read_inventory_rollup()
      RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
      SET search_path = pg_catalog, pg_temp AS $$
      DECLARE v_old JSONB; v_new JSONB; v_row JSONB;
      BEGIN
        IF TG_OP = 'INSERT' THEN v_new := to_jsonb(NEW); v_row := v_new;
        ELSIF TG_OP = 'UPDATE' THEN
          v_old := to_jsonb(OLD); v_new := to_jsonb(NEW); v_row := v_new;
        ELSE v_old := to_jsonb(OLD); v_row := v_old; END IF;
        v_old := v_old - ARRAY['total_qty','purchase_value','sale_value'];
        v_new := v_new - ARRAY['total_qty','purchase_value','sale_value'];
        INSERT INTO public.audit_log (
          tenant_id, user_id, action, table_name, record_id, old_values,
          new_values, changed_fields, created_at
        ) VALUES (
          NULLIF(v_row ->> 'tenant_id','')::UUID, public.current_app_user_id(),
          TG_OP::TEXT, TG_TABLE_NAME::TEXT,
          NULLIF(v_row ->> 'snapshot_id','')::UUID,
          public.audit_redact_table_snapshot(TG_TABLE_NAME::TEXT,v_old),
          public.audit_redact_table_snapshot(TG_TABLE_NAME::TEXT,v_new),
          NULL, statement_timestamp()
        );
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
      END;
      $$
    """)
    for suffix, event, function in (
        ("created", "BEFORE INSERT", "public.trg_set_created_meta()"),
        ("updated", "BEFORE UPDATE", "public.trg_set_updated_meta()"),
        (
            "audit",
            "AFTER INSERT OR UPDATE OR DELETE",
            "public.trg_audit_sync_read_inventory_rollup()",
        ),
    ):
        op.execute(f"""
          CREATE TRIGGER trg_sync_read_inventory_rollup_{suffix}
          {event} ON public.sync_read_inventory_rollup
          FOR EACH ROW EXECUTE FUNCTION {function}
        """)
    op.execute("""
      REVOKE ALL PRIVILEGES ON FUNCTION
        public.trg_guard_sync_read_inventory_rollup_ready()
      FROM PUBLIC, aurum_app, aurum_support
    """)
    op.execute("""
      REVOKE ALL PRIVILEGES ON FUNCTION
        public.trg_audit_sync_read_inventory_rollup()
      FROM PUBLIC, aurum_app, aurum_support
    """)
    op.execute("ALTER TABLE public.sync_read_inventory_rollup ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE public.sync_read_inventory_rollup FORCE ROW LEVEL SECURITY")
    op.execute("""
      CREATE POLICY tenant_isolation ON public.sync_read_inventory_rollup
      USING (
        tenant_id = public.current_tenant_id()
        AND (
          NULLIF(current_setting('app.edge_node_id', true), '') IS NULL
          OR (
            branch_id = NULLIF(current_setting('app.branch_id', true), '')::UUID
            AND node_id = NULLIF(current_setting('app.edge_node_id', true), '')::UUID
          )
        )
      )
      WITH CHECK (
        tenant_id = public.current_tenant_id()
        AND branch_id = NULLIF(current_setting('app.branch_id', true), '')::UUID
        AND node_id = NULLIF(current_setting('app.edge_node_id', true), '')::UUID
      )
    """)
    op.execute("""
      REVOKE ALL PRIVILEGES ON TABLE public.sync_read_inventory_rollup
      FROM PUBLIC, aurum_app
    """)
    op.execute("""
      GRANT SELECT, INSERT ON TABLE public.sync_read_inventory_rollup TO aurum_app
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION public.search_sync_read_catalog(UUID, TEXT)")
    op.execute("""
      DROP TRIGGER trg_guard_sync_read_inventory_rollup_ready
      ON public.sync_read_projection
    """)
    op.execute("DROP TABLE public.sync_read_inventory_rollup")
    op.execute("DROP FUNCTION public.trg_audit_sync_read_inventory_rollup()")
    op.execute("DROP FUNCTION public.trg_guard_sync_read_inventory_rollup_ready()")
