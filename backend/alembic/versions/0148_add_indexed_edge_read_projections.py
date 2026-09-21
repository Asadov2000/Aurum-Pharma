"""add indexed local Edge read projections

Revision ID: 0148
Revises: 0147
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0148"
down_revision: str | Sequence[str] | None = "0147"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PROJECTION_TABLES = (
    "sync_read_projection",
    "sync_read_catalog_projection",
    "sync_read_barcode_projection",
    "sync_read_supplier_projection",
    "sync_read_inventory_projection",
)


def _metadata_columns() -> str:
    return """
      created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
      created_by UUID,
      updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
      updated_by UUID
    """


def _scope_columns() -> str:
    return """
      snapshot_id UUID NOT NULL,
      tenant_id UUID NOT NULL,
      branch_id UUID NOT NULL,
      node_id UUID NOT NULL
    """


def _meta_and_audit_triggers(table: str) -> None:
    op.execute(f"""
      CREATE TRIGGER trg_{table}_created
      BEFORE INSERT ON public.{table}
      FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_meta()
    """)
    op.execute(f"""
      CREATE TRIGGER trg_{table}_updated
      BEFORE UPDATE ON public.{table}
      FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_meta()
    """)
    op.execute(f"""
      CREATE TRIGGER trg_audit_{table}
      AFTER INSERT OR UPDATE OR DELETE ON public.{table}
      FOR EACH ROW EXECUTE FUNCTION public.trg_audit_sync_read_snapshot_storage()
    """)


def _edge_rls(table: str, privileges: str) -> None:
    op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
    op.execute(f"""
      CREATE POLICY tenant_isolation ON public.{table}
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
    op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM PUBLIC, aurum_app")
    op.execute(f"GRANT {privileges} ON TABLE public.{table} TO aurum_app")


def upgrade() -> None:
    op.execute("""
      ALTER TABLE public.sync_read_snapshot_cursor
      ADD COLUMN last_confirmed_at TIMESTAMPTZ
    """)
    op.execute("""
      CREATE OR REPLACE FUNCTION public.trg_guard_sync_read_snapshot_cursor()
      RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
      SET search_path = pg_catalog, pg_temp AS $$
      BEGIN
        IF TG_OP = 'DELETE' THEN
          RAISE EXCEPTION 'Read snapshot cursor cannot be deleted' USING ERRCODE = '55000';
        END IF;
        IF TG_OP = 'UPDATE' AND NEW.active_snapshot_id = OLD.active_snapshot_id THEN
          IF NEW.version <> OLD.version
             OR NEW.activated_at IS DISTINCT FROM OLD.activated_at
             OR (to_jsonb(NEW) - ARRAY['last_confirmed_at','updated_at','updated_by'])
                IS DISTINCT FROM
                (to_jsonb(OLD) - ARRAY['last_confirmed_at','updated_at','updated_by']) THEN
            RAISE EXCEPTION 'Read snapshot confirmation is invalid' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END IF;
        IF TG_OP = 'UPDATE' AND (
          NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
          OR NEW.branch_id IS DISTINCT FROM OLD.branch_id
          OR NEW.node_id IS DISTINCT FROM OLD.node_id
          OR NEW.created_at IS DISTINCT FROM OLD.created_at
          OR NEW.created_by IS DISTINCT FROM OLD.created_by
          OR NEW.version <> OLD.version + 1
        ) THEN
          RAISE EXCEPTION 'Read snapshot cursor update is not a valid pointer advance'
            USING ERRCODE = '23514';
        END IF;
        NEW.activated_at := statement_timestamp();
        RETURN NEW;
      END;
      $$
    """)
    op.execute("""
      UPDATE public.sync_read_snapshot_cursor
      SET last_confirmed_at = activated_at
    """)
    op.execute("""
      ALTER TABLE public.sync_read_snapshot_cursor
      ALTER COLUMN last_confirmed_at SET DEFAULT statement_timestamp(),
      ALTER COLUMN last_confirmed_at SET NOT NULL
    """)

    op.execute(f"""
      CREATE TABLE public.sync_read_projection (
        {_scope_columns()},
        source_revision BIGINT NOT NULL CHECK (source_revision >= 1),
        projection_version INTEGER NOT NULL DEFAULT 1 CHECK (projection_version = 1),
        status TEXT NOT NULL DEFAULT 'building' CHECK (status IN ('building', 'ready')),
        foundation_payload JSONB NOT NULL CHECK (jsonb_typeof(foundation_payload) = 'object'),
        catalog_count BIGINT NOT NULL CHECK (catalog_count >= 0),
        supplier_count BIGINT NOT NULL CHECK (supplier_count >= 0),
        inventory_count BIGINT NOT NULL CHECK (inventory_count >= 0),
        {_metadata_columns()},
        CONSTRAINT pk_sync_read_projection PRIMARY KEY (snapshot_id),
        CONSTRAINT ck_sync_read_projection_foundation_scope CHECK (
          foundation_payload ->> 'record_type' = 'foundation'
          AND (foundation_payload #>> '{{tenant,id}}')::UUID = tenant_id
          AND (foundation_payload #>> '{{branch,id}}')::UUID = branch_id
        ),
        CONSTRAINT uq_sync_read_projection_scope UNIQUE (
          snapshot_id, tenant_id, branch_id, node_id
        ),
        CONSTRAINT fk_sync_read_projection_snapshot FOREIGN KEY (
          snapshot_id, tenant_id, branch_id, node_id, source_revision
        ) REFERENCES public.sync_read_snapshot (
          snapshot_id, tenant_id, branch_id, node_id, source_revision
        ) ON DELETE RESTRICT
      )
    """)
    op.execute(f"""
      CREATE TABLE public.sync_read_catalog_projection (
        {_scope_columns()},
        catalog_id UUID NOT NULL,
        search_text TEXT NOT NULL,
        payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
        {_metadata_columns()},
        CONSTRAINT pk_sync_read_catalog_projection PRIMARY KEY (snapshot_id, catalog_id),
        CONSTRAINT ck_sync_read_catalog_projection_payload CHECK (
          payload ->> 'record_type' = 'catalog_product'
          AND (payload ->> 'id')::UUID = catalog_id
          AND (payload ->> 'tenant_id')::UUID = tenant_id
          AND search_text = lower(concat_ws(' ', payload ->> 'brand_name',
              payload ->> 'inn', payload ->> 'manufacturer'))
        ),
        CONSTRAINT fk_sync_read_catalog_projection_header FOREIGN KEY (
          snapshot_id, tenant_id, branch_id, node_id
        ) REFERENCES public.sync_read_projection (
          snapshot_id, tenant_id, branch_id, node_id
        ) ON DELETE RESTRICT
      )
    """)
    op.execute("""
      CREATE INDEX ix_sync_read_catalog_projection_search
      ON public.sync_read_catalog_projection USING gin (search_text gin_trgm_ops)
    """)
    op.execute(f"""
      CREATE TABLE public.sync_read_barcode_projection (
        {_scope_columns()},
        barcode_id UUID NOT NULL,
        catalog_id UUID NOT NULL,
        code TEXT NOT NULL,
        code_type TEXT NOT NULL,
        {_metadata_columns()},
        CONSTRAINT pk_sync_read_barcode_projection PRIMARY KEY (snapshot_id, barcode_id),
        CONSTRAINT fk_sync_read_barcode_projection_catalog FOREIGN KEY (
          snapshot_id, catalog_id
        ) REFERENCES public.sync_read_catalog_projection (
          snapshot_id, catalog_id
        ) ON DELETE RESTRICT
      )
    """)
    op.execute("""
      CREATE UNIQUE INDEX ix_sync_read_barcode_projection_lookup
      ON public.sync_read_barcode_projection (
        tenant_id, branch_id, node_id, snapshot_id, code
      )
    """)
    op.execute("""
      CREATE INDEX ix_sync_read_barcode_projection_search
      ON public.sync_read_barcode_projection USING gin (code gin_trgm_ops)
    """)
    op.execute(f"""
      CREATE TABLE public.sync_read_supplier_projection (
        {_scope_columns()},
        supplier_id UUID NOT NULL,
        search_text TEXT NOT NULL,
        payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
        {_metadata_columns()},
        CONSTRAINT pk_sync_read_supplier_projection PRIMARY KEY (snapshot_id, supplier_id),
        CONSTRAINT ck_sync_read_supplier_projection_payload CHECK (
          payload ->> 'record_type' = 'supplier'
          AND (payload ->> 'id')::UUID = supplier_id
          AND (payload ->> 'tenant_id')::UUID = tenant_id
          AND search_text = lower(concat_ws(' ', payload ->> 'name',
              payload ->> 'legal_name', payload ->> 'inn_or_tin',
              payload ->> 'contact_person'))
        ),
        CONSTRAINT fk_sync_read_supplier_projection_header FOREIGN KEY (
          snapshot_id, tenant_id, branch_id, node_id
        ) REFERENCES public.sync_read_projection (
          snapshot_id, tenant_id, branch_id, node_id
        ) ON DELETE RESTRICT
      )
    """)
    op.execute("""
      CREATE INDEX ix_sync_read_supplier_projection_search
      ON public.sync_read_supplier_projection USING gin (search_text gin_trgm_ops)
    """)
    op.execute("""
      CREATE FUNCTION public.trg_set_sync_read_projection_search_text()
      RETURNS TRIGGER LANGUAGE plpgsql
      SET search_path = pg_catalog, pg_temp AS $$
      BEGIN
        IF TG_TABLE_NAME = 'sync_read_catalog_projection' THEN
          NEW.search_text := lower(concat_ws(' ', NEW.payload ->> 'brand_name',
              NEW.payload ->> 'inn', NEW.payload ->> 'manufacturer'));
        ELSIF TG_TABLE_NAME = 'sync_read_supplier_projection' THEN
          NEW.search_text := lower(concat_ws(' ', NEW.payload ->> 'name',
              NEW.payload ->> 'legal_name', NEW.payload ->> 'inn_or_tin',
              NEW.payload ->> 'contact_person'));
        ELSE
          RAISE EXCEPTION 'Unsupported read projection search table'
            USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
      END;
      $$
    """)
    op.execute("""
      REVOKE ALL PRIVILEGES ON FUNCTION
        public.trg_set_sync_read_projection_search_text()
      FROM PUBLIC, aurum_app, aurum_support
    """)
    for table in (
        "sync_read_catalog_projection",
        "sync_read_supplier_projection",
    ):
        op.execute(f"""
          CREATE TRIGGER trg_00_{table}_search_text
          BEFORE INSERT ON public.{table}
          FOR EACH ROW EXECUTE FUNCTION
            public.trg_set_sync_read_projection_search_text()
        """)
    op.execute(f"""
      CREATE TABLE public.sync_read_inventory_projection (
        {_scope_columns()},
        batch_id UUID NOT NULL,
        catalog_id UUID NOT NULL,
        expires_at DATE NOT NULL,
        qty_remaining NUMERIC(14,3) NOT NULL,
        purchase_price NUMERIC(14,2) NOT NULL,
        sale_price NUMERIC(14,2) NOT NULL,
        is_blocked BOOLEAN NOT NULL,
        payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
        {_metadata_columns()},
        CONSTRAINT pk_sync_read_inventory_projection PRIMARY KEY (snapshot_id, batch_id),
        CONSTRAINT ck_sync_read_inventory_projection_payload CHECK (
          payload ->> 'record_type' = 'inventory_batch'
          AND (payload ->> 'id')::UUID = batch_id
          AND (payload ->> 'tenant_id')::UUID = tenant_id
          AND (payload ->> 'branch_id')::UUID = branch_id
          AND (payload ->> 'catalog_id')::UUID = catalog_id
          AND (payload ->> 'expires_at')::DATE = expires_at
          AND (payload ->> 'qty_remaining')::NUMERIC = qty_remaining
          AND (payload ->> 'purchase_price')::NUMERIC = purchase_price
          AND (payload ->> 'sale_price')::NUMERIC = sale_price
          AND (payload ->> 'is_blocked')::BOOLEAN = is_blocked
        ),
        CONSTRAINT fk_sync_read_inventory_projection_header FOREIGN KEY (
          snapshot_id, tenant_id, branch_id, node_id
        ) REFERENCES public.sync_read_projection (
          snapshot_id, tenant_id, branch_id, node_id
        ) ON DELETE RESTRICT
      )
    """)
    op.execute("""
      CREATE INDEX ix_sync_read_inventory_projection_catalog
      ON public.sync_read_inventory_projection (
        tenant_id, branch_id, node_id, snapshot_id, catalog_id
      )
    """)
    op.execute("""
      CREATE INDEX ix_sync_read_inventory_projection_expiry
      ON public.sync_read_inventory_projection (
        tenant_id, branch_id, node_id, snapshot_id, expires_at
      )
    """)
    op.execute("""
      CREATE INDEX ix_sync_read_inventory_projection_available
      ON public.sync_read_inventory_projection (
        tenant_id, branch_id, node_id, snapshot_id
      ) WHERE qty_remaining > 0
    """)

    # Existing ready snapshots are rebuilt deterministically before the new
    # cursor guard starts requiring a ready indexed projection.
    op.execute("""
      INSERT INTO public.sync_read_projection (
        snapshot_id, tenant_id, branch_id, node_id, source_revision,
        foundation_payload, catalog_count, supplier_count, inventory_count
      )
      SELECT
        s.snapshot_id, s.tenant_id, s.branch_id, s.node_id, s.source_revision,
        foundation.item,
        count(*) FILTER (WHERE catalog.item ->> 'record_type' = 'catalog_product'),
        count(*) FILTER (WHERE catalog.item ->> 'record_type' = 'supplier'),
        inventory.inventory_count
      FROM public.sync_read_snapshot s
      CROSS JOIN LATERAL (
        SELECT item
        FROM public.sync_read_snapshot_chunk c,
             LATERAL jsonb_array_elements(c.payload -> 'items') item
        WHERE c.snapshot_id = s.snapshot_id AND c.component = 'foundation'
        LIMIT 1
      ) foundation
      CROSS JOIN LATERAL (
        SELECT count(*) AS inventory_count
        FROM public.sync_read_snapshot_chunk c,
             LATERAL jsonb_array_elements(c.payload -> 'items') item
        WHERE c.snapshot_id = s.snapshot_id AND c.component = 'inventory'
      ) inventory
      LEFT JOIN LATERAL (
        SELECT item
        FROM public.sync_read_snapshot_chunk c,
             LATERAL jsonb_array_elements(c.payload -> 'items') item
        WHERE c.snapshot_id = s.snapshot_id AND c.component = 'catalog'
      ) catalog ON true
      WHERE s.status = 'ready'
      GROUP BY s.snapshot_id, s.tenant_id, s.branch_id, s.node_id,
               s.source_revision, foundation.item, inventory.inventory_count
    """)
    op.execute("""
      INSERT INTO public.sync_read_catalog_projection (
        snapshot_id, catalog_id, tenant_id, branch_id, node_id, search_text, payload
      )
      SELECT s.snapshot_id, (item ->> 'id')::UUID, s.tenant_id, s.branch_id, s.node_id,
             lower(concat_ws(' ', item ->> 'brand_name', item ->> 'inn',
                                  item ->> 'manufacturer')), item
      FROM public.sync_read_snapshot s
      JOIN public.sync_read_snapshot_chunk c ON c.snapshot_id = s.snapshot_id
      CROSS JOIN LATERAL jsonb_array_elements(c.payload -> 'items') item
      WHERE s.status = 'ready' AND c.component = 'catalog'
        AND item ->> 'record_type' = 'catalog_product'
    """)
    op.execute("""
      INSERT INTO public.sync_read_barcode_projection (
        snapshot_id, barcode_id, catalog_id, tenant_id, branch_id, node_id, code, code_type
      )
      SELECT p.snapshot_id, (barcode ->> 'id')::UUID, p.catalog_id,
             p.tenant_id, p.branch_id, p.node_id,
             barcode ->> 'code', barcode ->> 'code_type'
      FROM public.sync_read_catalog_projection p
      CROSS JOIN LATERAL jsonb_array_elements(p.payload -> 'barcodes') barcode
    """)
    op.execute("""
      INSERT INTO public.sync_read_supplier_projection (
        snapshot_id, supplier_id, tenant_id, branch_id, node_id, search_text, payload
      )
      SELECT s.snapshot_id, (item ->> 'id')::UUID, s.tenant_id, s.branch_id, s.node_id,
             lower(concat_ws(' ', item ->> 'name', item ->> 'legal_name',
                                  item ->> 'inn_or_tin', item ->> 'contact_person')), item
      FROM public.sync_read_snapshot s
      JOIN public.sync_read_snapshot_chunk c ON c.snapshot_id = s.snapshot_id
      CROSS JOIN LATERAL jsonb_array_elements(c.payload -> 'items') item
      WHERE s.status = 'ready' AND c.component = 'catalog'
        AND item ->> 'record_type' = 'supplier'
    """)
    op.execute("""
      INSERT INTO public.sync_read_inventory_projection (
        snapshot_id, batch_id, tenant_id, branch_id, node_id, catalog_id,
        expires_at, qty_remaining, purchase_price, sale_price, is_blocked, payload
      )
      SELECT s.snapshot_id, (item ->> 'id')::UUID, s.tenant_id, s.branch_id, s.node_id,
             (item ->> 'catalog_id')::UUID, (item ->> 'expires_at')::DATE,
             (item ->> 'qty_remaining')::NUMERIC(14,3),
             (item ->> 'purchase_price')::NUMERIC(14,2),
             (item ->> 'sale_price')::NUMERIC(14,2),
             (item ->> 'is_blocked')::BOOLEAN, item
      FROM public.sync_read_snapshot s
      JOIN public.sync_read_snapshot_chunk c ON c.snapshot_id = s.snapshot_id
      CROSS JOIN LATERAL jsonb_array_elements(c.payload -> 'items') item
      WHERE s.status = 'ready' AND c.component = 'inventory'
        AND item ->> 'record_type' = 'inventory_batch'
    """)
    op.execute("UPDATE public.sync_read_projection SET status = 'ready'")
    op.execute("""
      DO $$
      BEGIN
        IF EXISTS (
          SELECT 1
          FROM public.sync_read_snapshot s
          JOIN public.sync_read_projection p ON p.snapshot_id = s.snapshot_id
          WHERE s.status = 'ready'
            AND (
              (SELECT count(*)
               FROM public.sync_read_snapshot_chunk c,
                    LATERAL jsonb_array_elements(c.payload -> 'items') item
               WHERE c.snapshot_id = s.snapshot_id AND c.component = 'foundation') <> 1
              OR (SELECT count(*)
                  FROM public.sync_read_snapshot_chunk c,
                       LATERAL jsonb_array_elements(c.payload -> 'items') item
                  WHERE c.snapshot_id = s.snapshot_id AND c.component = 'catalog')
                 <> p.catalog_count + p.supplier_count
              OR (SELECT count(*)
                  FROM public.sync_read_snapshot_chunk c,
                       LATERAL jsonb_array_elements(c.payload -> 'items') item
                  WHERE c.snapshot_id = s.snapshot_id AND c.component = 'inventory')
                 <> p.inventory_count
            )
        ) THEN
          RAISE EXCEPTION 'Existing read snapshot contains unsupported projection records'
            USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
          SELECT 1
          FROM public.sync_read_snapshot_cursor c
          LEFT JOIN public.sync_read_projection p
            ON p.snapshot_id = c.active_snapshot_id
           AND p.tenant_id = c.tenant_id
           AND p.branch_id = c.branch_id
           AND p.node_id = c.node_id
           AND p.status = 'ready'
          WHERE p.snapshot_id IS NULL
        ) THEN
          RAISE EXCEPTION 'Active read snapshot could not be indexed safely'
            USING ERRCODE = '23514';
        END IF;
      END;
      $$
    """)

    op.execute("""
      CREATE FUNCTION public.trg_guard_sync_read_projection_header()
      RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
      SET search_path = pg_catalog, pg_temp AS $$
      BEGIN
        IF TG_OP = 'DELETE' THEN
          RAISE EXCEPTION 'Read projection headers are immutable' USING ERRCODE = '55000';
        END IF;
        IF TG_OP = 'INSERT' AND (
          NEW.status <> 'building' OR NOT EXISTS (
            SELECT 1 FROM public.sync_read_snapshot s
            WHERE s.snapshot_id = NEW.snapshot_id
              AND s.tenant_id = NEW.tenant_id
              AND s.branch_id = NEW.branch_id
              AND s.node_id = NEW.node_id
              AND s.status = 'draft'
          )
        ) THEN
          RAISE EXCEPTION 'Read projection requires a draft snapshot'
            USING ERRCODE = '55000';
        END IF;
        IF TG_OP = 'UPDATE' AND (
          OLD.status <> 'building' OR NEW.status <> 'ready'
          OR (to_jsonb(NEW) - ARRAY['status','updated_at','updated_by'])
             IS DISTINCT FROM
             (to_jsonb(OLD) - ARRAY['status','updated_at','updated_by'])
        ) THEN
          RAISE EXCEPTION 'Read projection header update is invalid' USING ERRCODE = '55000';
        END IF;
        IF TG_OP = 'UPDATE' AND (
          (SELECT count(*) FROM public.sync_read_catalog_projection p
           WHERE p.snapshot_id = NEW.snapshot_id) <> NEW.catalog_count
          OR (SELECT count(*) FROM public.sync_read_supplier_projection p
              WHERE p.snapshot_id = NEW.snapshot_id) <> NEW.supplier_count
          OR (SELECT count(*) FROM public.sync_read_inventory_projection p
              WHERE p.snapshot_id = NEW.snapshot_id) <> NEW.inventory_count
          OR (SELECT count(*)
              FROM public.sync_read_snapshot_chunk c,
                   LATERAL jsonb_array_elements(c.payload -> 'items') item
              WHERE c.snapshot_id = NEW.snapshot_id AND c.component = 'foundation') <> 1
          OR (SELECT count(*)
              FROM public.sync_read_snapshot_chunk c,
                   LATERAL jsonb_array_elements(c.payload -> 'items') item
              WHERE c.snapshot_id = NEW.snapshot_id AND c.component = 'catalog')
             <> NEW.catalog_count + NEW.supplier_count
          OR (SELECT count(*)
              FROM public.sync_read_snapshot_chunk c,
                   LATERAL jsonb_array_elements(c.payload -> 'items') item
              WHERE c.snapshot_id = NEW.snapshot_id AND c.component = 'inventory')
             <> NEW.inventory_count
          OR NEW.foundation_payload IS DISTINCT FROM (
            SELECT item
            FROM public.sync_read_snapshot_chunk c,
                 LATERAL jsonb_array_elements(c.payload -> 'items') item
            WHERE c.snapshot_id = NEW.snapshot_id AND c.component = 'foundation'
          )
          OR EXISTS (
            (SELECT p.payload FROM public.sync_read_catalog_projection p
             WHERE p.snapshot_id = NEW.snapshot_id)
            EXCEPT
            (SELECT item
             FROM public.sync_read_snapshot_chunk c,
                  LATERAL jsonb_array_elements(c.payload -> 'items') item
             WHERE c.snapshot_id = NEW.snapshot_id AND c.component = 'catalog'
               AND item ->> 'record_type' = 'catalog_product')
          )
          OR EXISTS (
            (SELECT item
             FROM public.sync_read_snapshot_chunk c,
                  LATERAL jsonb_array_elements(c.payload -> 'items') item
             WHERE c.snapshot_id = NEW.snapshot_id AND c.component = 'catalog'
               AND item ->> 'record_type' = 'catalog_product')
            EXCEPT
            (SELECT p.payload FROM public.sync_read_catalog_projection p
             WHERE p.snapshot_id = NEW.snapshot_id)
          )
          OR EXISTS (
            (SELECT p.payload FROM public.sync_read_supplier_projection p
             WHERE p.snapshot_id = NEW.snapshot_id)
            EXCEPT
            (SELECT item
             FROM public.sync_read_snapshot_chunk c,
                  LATERAL jsonb_array_elements(c.payload -> 'items') item
             WHERE c.snapshot_id = NEW.snapshot_id AND c.component = 'catalog'
               AND item ->> 'record_type' = 'supplier')
          )
          OR EXISTS (
            (SELECT item
             FROM public.sync_read_snapshot_chunk c,
                  LATERAL jsonb_array_elements(c.payload -> 'items') item
             WHERE c.snapshot_id = NEW.snapshot_id AND c.component = 'catalog'
               AND item ->> 'record_type' = 'supplier')
            EXCEPT
            (SELECT p.payload FROM public.sync_read_supplier_projection p
             WHERE p.snapshot_id = NEW.snapshot_id)
          )
          OR EXISTS (
            (SELECT p.payload FROM public.sync_read_inventory_projection p
             WHERE p.snapshot_id = NEW.snapshot_id)
            EXCEPT
            (SELECT item
             FROM public.sync_read_snapshot_chunk c,
                  LATERAL jsonb_array_elements(c.payload -> 'items') item
             WHERE c.snapshot_id = NEW.snapshot_id AND c.component = 'inventory')
          )
          OR EXISTS (
            (SELECT item
             FROM public.sync_read_snapshot_chunk c,
                  LATERAL jsonb_array_elements(c.payload -> 'items') item
             WHERE c.snapshot_id = NEW.snapshot_id AND c.component = 'inventory')
            EXCEPT
            (SELECT p.payload FROM public.sync_read_inventory_projection p
             WHERE p.snapshot_id = NEW.snapshot_id)
          )
          OR EXISTS (
            (SELECT b.catalog_id, jsonb_build_object(
               'id', b.barcode_id, 'code', b.code, 'code_type', b.code_type
             )
             FROM public.sync_read_barcode_projection b
             WHERE b.snapshot_id = NEW.snapshot_id)
            EXCEPT
            (SELECT (item ->> 'id')::UUID, barcode
             FROM public.sync_read_snapshot_chunk c,
                  LATERAL jsonb_array_elements(c.payload -> 'items') item,
                  LATERAL jsonb_array_elements(item -> 'barcodes') barcode
             WHERE c.snapshot_id = NEW.snapshot_id AND c.component = 'catalog'
               AND item ->> 'record_type' = 'catalog_product')
          )
          OR EXISTS (
            (SELECT (item ->> 'id')::UUID, barcode
             FROM public.sync_read_snapshot_chunk c,
                  LATERAL jsonb_array_elements(c.payload -> 'items') item,
                  LATERAL jsonb_array_elements(item -> 'barcodes') barcode
             WHERE c.snapshot_id = NEW.snapshot_id AND c.component = 'catalog'
               AND item ->> 'record_type' = 'catalog_product')
            EXCEPT
            (SELECT b.catalog_id, jsonb_build_object(
               'id', b.barcode_id, 'code', b.code, 'code_type', b.code_type
             )
             FROM public.sync_read_barcode_projection b
             WHERE b.snapshot_id = NEW.snapshot_id)
          )
        ) THEN
          RAISE EXCEPTION 'Read projection does not match immutable snapshot content'
            USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
      END;
      $$
    """)
    op.execute("""
      CREATE FUNCTION public.trg_guard_sync_read_projection_rows()
      RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
      SET search_path = pg_catalog, pg_temp AS $$
      BEGIN
        IF TG_OP IN ('UPDATE', 'DELETE') THEN
          RAISE EXCEPTION 'Read projection rows are immutable' USING ERRCODE = '55000';
        END IF;
        IF NOT EXISTS (
          SELECT 1 FROM public.sync_read_projection p
          JOIN public.sync_read_snapshot s ON s.snapshot_id = p.snapshot_id
          WHERE p.snapshot_id = NEW.snapshot_id
            AND p.tenant_id = NEW.tenant_id
            AND p.branch_id = NEW.branch_id
            AND p.node_id = NEW.node_id
            AND p.status = 'building'
            AND s.status = 'draft'
        ) THEN
          RAISE EXCEPTION 'Read projection rows require a building draft'
            USING ERRCODE = '55000';
        END IF;
        RETURN NEW;
      END;
      $$
    """)
    op.execute("""
      CREATE TRIGGER trg_guard_sync_read_projection
      BEFORE INSERT OR UPDATE OR DELETE ON public.sync_read_projection
      FOR EACH ROW EXECUTE FUNCTION public.trg_guard_sync_read_projection_header()
    """)
    for table in PROJECTION_TABLES[1:]:
        op.execute(f"""
          CREATE TRIGGER trg_guard_{table}
          BEFORE INSERT OR UPDATE OR DELETE ON public.{table}
          FOR EACH ROW EXECUTE FUNCTION public.trg_guard_sync_read_projection_rows()
        """)

    op.execute("""
      CREATE OR REPLACE FUNCTION public.trg_guard_sync_read_snapshot_cursor()
      RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
      SET search_path = pg_catalog, pg_temp AS $$
      DECLARE
        v_old_revision BIGINT;
        v_new_revision BIGINT;
      BEGIN
        IF TG_OP = 'DELETE' THEN
          RAISE EXCEPTION 'Read snapshot cursor cannot be deleted' USING ERRCODE = '55000';
        END IF;
        IF TG_OP = 'UPDATE' AND NEW.active_snapshot_id = OLD.active_snapshot_id THEN
          IF NEW.version <> OLD.version
             OR NEW.activated_at IS DISTINCT FROM OLD.activated_at
             OR (to_jsonb(NEW) - ARRAY['last_confirmed_at','updated_at','updated_by'])
                IS DISTINCT FROM
                (to_jsonb(OLD) - ARRAY['last_confirmed_at','updated_at','updated_by']) THEN
            RAISE EXCEPTION 'Read snapshot confirmation is invalid' USING ERRCODE = '23514';
          END IF;
          NEW.last_confirmed_at := statement_timestamp();
          RETURN NEW;
        END IF;
        IF TG_OP = 'UPDATE' AND (
          NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
          OR NEW.branch_id IS DISTINCT FROM OLD.branch_id
          OR NEW.node_id IS DISTINCT FROM OLD.node_id
          OR NEW.created_at IS DISTINCT FROM OLD.created_at
          OR NEW.created_by IS DISTINCT FROM OLD.created_by
          OR NEW.version <> OLD.version + 1
        ) THEN
          RAISE EXCEPTION 'Read snapshot cursor update is not a valid pointer advance'
            USING ERRCODE = '23514';
        END IF;
        IF NOT EXISTS (
          SELECT 1 FROM public.sync_read_snapshot s
          JOIN public.sync_read_projection p ON p.snapshot_id = s.snapshot_id
          WHERE s.snapshot_id = NEW.active_snapshot_id
            AND s.tenant_id = NEW.tenant_id
            AND s.branch_id = NEW.branch_id
            AND s.node_id = NEW.node_id
            AND s.status = 'ready' AND p.status = 'ready'
        ) THEN
          RAISE EXCEPTION 'Read snapshot cursor requires a ready indexed snapshot'
            USING ERRCODE = '23514';
        END IF;
        IF TG_OP = 'UPDATE' THEN
          SELECT source_revision INTO v_old_revision
          FROM public.sync_read_snapshot WHERE snapshot_id = OLD.active_snapshot_id;
          SELECT source_revision INTO v_new_revision
          FROM public.sync_read_snapshot WHERE snapshot_id = NEW.active_snapshot_id;
          IF v_new_revision <= v_old_revision THEN
            RAISE EXCEPTION 'Read snapshot cursor cannot move backwards'
              USING ERRCODE = '23514';
          END IF;
        END IF;
        NEW.activated_at := statement_timestamp();
        NEW.last_confirmed_at := statement_timestamp();
        RETURN NEW;
      END;
      $$
    """)

    op.execute("""
      CREATE OR REPLACE FUNCTION public.trg_audit_sync_read_snapshot_storage()
      RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
      SET search_path = pg_catalog, pg_temp AS $$
      DECLARE v_old JSONB; v_new JSONB; v_row JSONB; v_record_id UUID;
      BEGIN
        IF TG_OP = 'INSERT' THEN v_new := to_jsonb(NEW); v_row := v_new;
        ELSIF TG_OP = 'UPDATE' THEN v_old := to_jsonb(OLD); v_new := to_jsonb(NEW); v_row := v_new;
        ELSE v_old := to_jsonb(OLD); v_row := v_old; END IF;
        v_old := v_old - ARRAY['payload','foundation_payload','search_text','code',
          'qty_remaining','purchase_price','sale_price'];
        v_new := v_new - ARRAY['payload','foundation_payload','search_text','code',
          'qty_remaining','purchase_price','sale_price'];
        v_record_id := COALESCE(NULLIF(v_row ->> 'snapshot_id','')::UUID,
                                NULLIF(v_row ->> 'node_id','')::UUID);
        INSERT INTO public.audit_log (
          tenant_id,user_id,action,table_name,record_id,old_values,new_values,
          changed_fields,created_at
        ) VALUES (
          NULLIF(v_row ->> 'tenant_id','')::UUID, public.current_app_user_id(),
          TG_OP::TEXT, TG_TABLE_NAME::TEXT, v_record_id,
          public.audit_redact_table_snapshot(TG_TABLE_NAME::TEXT,v_old),
          public.audit_redact_table_snapshot(TG_TABLE_NAME::TEXT,v_new),
          NULL, statement_timestamp()
        );
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
      END;
      $$
    """)

    for function in (
        "public.trg_guard_sync_read_projection_header()",
        "public.trg_guard_sync_read_projection_rows()",
    ):
        op.execute(
            f"REVOKE ALL PRIVILEGES ON FUNCTION {function} " "FROM PUBLIC, aurum_app, aurum_support"
        )
    for table in PROJECTION_TABLES:
        _meta_and_audit_triggers(table)
        _edge_rls(
            table,
            "SELECT, INSERT, UPDATE" if table == "sync_read_projection" else "SELECT, INSERT",
        )


def downgrade() -> None:
    op.execute("""
      CREATE OR REPLACE FUNCTION public.trg_guard_sync_read_snapshot_cursor()
      RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
      SET search_path = pg_catalog, pg_temp AS $$
      BEGIN
        IF TG_OP = 'DELETE' THEN
          RAISE EXCEPTION 'Read snapshot cursor cannot be deleted' USING ERRCODE = '55000';
        END IF;
        IF TG_OP = 'UPDATE' AND (
          NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
          OR NEW.branch_id IS DISTINCT FROM OLD.branch_id
          OR NEW.node_id IS DISTINCT FROM OLD.node_id
          OR NEW.created_at IS DISTINCT FROM OLD.created_at
          OR NEW.created_by IS DISTINCT FROM OLD.created_by
          OR NEW.active_snapshot_id IS NOT DISTINCT FROM OLD.active_snapshot_id
          OR NEW.version <> OLD.version + 1
        ) THEN
          RAISE EXCEPTION 'Read snapshot cursor update is not a valid pointer advance'
            USING ERRCODE = '23514';
        END IF;
        NEW.activated_at := statement_timestamp();
        IF NOT EXISTS (
          SELECT 1 FROM public.sync_read_snapshot s
          WHERE s.snapshot_id = NEW.active_snapshot_id
            AND s.tenant_id = NEW.tenant_id AND s.branch_id = NEW.branch_id
            AND s.node_id = NEW.node_id AND s.status = 'ready'
        ) THEN
          RAISE EXCEPTION 'Read snapshot cursor requires a ready snapshot in the same scope'
            USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
      END;
      $$
    """)
    for table in reversed(PROJECTION_TABLES):
        op.execute(f"DROP TABLE public.{table}")
    op.execute("DROP FUNCTION public.trg_set_sync_read_projection_search_text()")
    op.execute("DROP FUNCTION public.trg_guard_sync_read_projection_rows()")
    op.execute("DROP FUNCTION public.trg_guard_sync_read_projection_header()")
    op.execute("ALTER TABLE public.sync_read_snapshot_cursor DROP COLUMN last_confirmed_at")
    op.execute("""
      CREATE OR REPLACE FUNCTION public.trg_audit_sync_read_snapshot_storage()
      RETURNS TRIGGER LANGUAGE plpgsql SECURITY DEFINER
      SET search_path = pg_catalog, pg_temp AS $$
      DECLARE
        v_old JSONB;
        v_new JSONB;
        v_row JSONB;
        v_record_id UUID;
      BEGIN
        IF TG_OP = 'INSERT' THEN
          v_new := to_jsonb(NEW) - 'payload';
          v_row := v_new;
        ELSIF TG_OP = 'UPDATE' THEN
          v_old := to_jsonb(OLD) - 'payload';
          v_new := to_jsonb(NEW) - 'payload';
          v_row := v_new;
        ELSE
          v_old := to_jsonb(OLD) - 'payload';
          v_row := v_old;
        END IF;
        v_record_id := COALESCE(
          NULLIF(v_row ->> 'snapshot_id', '')::UUID,
          NULLIF(v_row ->> 'node_id', '')::UUID
        );
        INSERT INTO public.audit_log (
          tenant_id, user_id, action, table_name, record_id, old_values,
          new_values, changed_fields, created_at
        ) VALUES (
          NULLIF(v_row ->> 'tenant_id', '')::UUID,
          public.current_app_user_id(), TG_OP::TEXT, TG_TABLE_NAME::TEXT,
          v_record_id,
          public.audit_redact_table_snapshot(TG_TABLE_NAME::TEXT, v_old),
          public.audit_redact_table_snapshot(TG_TABLE_NAME::TEXT, v_new),
          CASE WHEN TG_OP = 'UPDATE' THEN public.audit_redact_table_snapshot(
            TG_TABLE_NAME::TEXT,
            (
              SELECT jsonb_object_agg(entry.key, entry.value)
              FROM jsonb_each(v_new) AS entry
              WHERE v_old -> entry.key IS DISTINCT FROM entry.value
            )
          ) ELSE NULL END,
          statement_timestamp()
        );
        RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
      END;
      $$
    """)
