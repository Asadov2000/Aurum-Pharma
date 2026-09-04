"""enforce supplier idempotency, provenance, and audit privacy

Revision ID: 0135
Revises: 0134
Create Date: 2026-09-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0135"
down_revision: str | Sequence[str] | None = "0134"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


AUDIT_TABLE_REDACTION_SQL = """
CREATE FUNCTION public.audit_redact_table_snapshot(p_table_name TEXT, p_data JSONB)
RETURNS JSONB
LANGUAGE plpgsql
IMMUTABLE
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_result JSONB := public.audit_redact_jsonb(p_data);
  v_field TEXT;
BEGIN
  IF v_result IS NULL THEN
    RETURN NULL;
  END IF;

  IF p_table_name = 'incoming_document' THEN
    v_field := 'total_amount';
  ELSIF p_table_name = 'supplier_return' THEN
    v_field := 'amount';
  ELSE
    RETURN v_result;
  END IF;

  IF v_result ? v_field AND v_result -> v_field <> 'null'::JSONB THEN
    v_result := pg_catalog.jsonb_set(
      v_result,
      ARRAY[v_field],
      pg_catalog.to_jsonb('***'::TEXT),
      false
    );
  END IF;
  RETURN v_result;
END;
$$;
"""


def _audit_trigger_sql(redactor: str, *, table_aware: bool = True) -> str:
    def redact(value: str) -> str:
        if table_aware:
            return f"{redactor}(TG_TABLE_NAME::TEXT, {value})"
        return f"{redactor}({value})"

    return f"""
CREATE OR REPLACE FUNCTION public.trg_audit_log()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $$
DECLARE
  v_tenant_id UUID;
  v_record_id UUID;
  v_user_id UUID;
  v_old_data JSONB;
  v_new_data JSONB;
  v_changed_fields JSONB;
BEGIN
  BEGIN
    v_tenant_id := COALESCE(
      CASE WHEN TG_OP = 'DELETE' THEN (OLD).tenant_id ELSE (NEW).tenant_id END,
      public.current_tenant_id()
    );
  EXCEPTION WHEN undefined_column THEN
    BEGIN
      v_tenant_id := COALESCE(
        CASE WHEN TG_OP = 'DELETE' THEN (OLD).id ELSE (NEW).id END,
        public.current_tenant_id()
      );
    EXCEPTION WHEN OTHERS THEN
      v_tenant_id := public.current_tenant_id();
    END;
  END;

  BEGIN
    v_record_id := CASE WHEN TG_OP = 'DELETE' THEN (OLD).id ELSE (NEW).id END;
  EXCEPTION WHEN OTHERS THEN
    v_record_id := NULL;
  END;

  v_user_id := public.current_app_user_id();

  IF TG_OP = 'INSERT' THEN
    v_old_data := NULL;
    v_new_data := pg_catalog.to_jsonb(NEW);
  ELSIF TG_OP = 'UPDATE' THEN
    v_old_data := pg_catalog.to_jsonb(OLD);
    v_new_data := pg_catalog.to_jsonb(NEW);
    SELECT pg_catalog.jsonb_object_agg(key, value) INTO v_changed_fields
    FROM pg_catalog.jsonb_each(v_new_data)
    WHERE v_old_data -> key IS DISTINCT FROM value;
    IF v_changed_fields IS NULL OR v_changed_fields = '{{}}'::JSONB THEN
      RETURN NEW;
    END IF;
  ELSE
    v_old_data := pg_catalog.to_jsonb(OLD);
    v_new_data := NULL;
  END IF;

  v_old_data := {redact("v_old_data")};
  v_new_data := {redact("v_new_data")};
  v_changed_fields := {redact("v_changed_fields")};

  INSERT INTO public.audit_log (
    tenant_id, user_id, action, table_name, record_id,
    old_values, new_values, changed_fields, created_at
  ) VALUES (
    v_tenant_id, v_user_id, TG_OP::TEXT, TG_TABLE_NAME::TEXT,
    v_record_id, v_old_data, v_new_data, v_changed_fields,
    pg_catalog.now()
  );

  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$;
"""


MOVEMENT_PROVENANCE_SQL = """
CREATE FUNCTION public.trg_validate_supplier_movement_provenance()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  IF NEW.source_table = 'incoming_item' THEN
    IF NOT EXISTS (
      SELECT 1
      FROM public.incoming_item AS item
      JOIN public.incoming_document AS document
        ON document.tenant_id = item.tenant_id
       AND document.id = item.document_id
      WHERE item.tenant_id = NEW.tenant_id
        AND item.id = NEW.source_id
        AND item.created_batch_id = NEW.batch_id
        AND document.status = 'accepted'
        AND NEW.movement_type = 'incoming'
        AND NEW.qty_delta = item.qty
    ) THEN
      RAISE EXCEPTION 'Incoming movement has inconsistent source provenance'
        USING ERRCODE = 'check_violation';
    END IF;
  ELSIF NEW.source_table = 'supplier_return' THEN
    IF NOT EXISTS (
      SELECT 1
      FROM public.supplier_return AS supplier_return
      WHERE supplier_return.tenant_id = NEW.tenant_id
        AND supplier_return.id = NEW.source_id
        AND supplier_return.batch_id = NEW.batch_id
        AND NEW.movement_type = 'supplier_return'
        AND NEW.qty_delta = -supplier_return.qty
    ) THEN
      RAISE EXCEPTION 'Supplier return movement has inconsistent source provenance'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;
"""


PROVENANCE_PREFLIGHT_SQL = """
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM public.incoming_item AS item
    JOIN public.incoming_document AS document
      ON document.tenant_id = item.tenant_id
     AND document.id = item.document_id
    LEFT JOIN public.batch AS batch
      ON batch.tenant_id = item.tenant_id
     AND batch.id = item.created_batch_id
    WHERE document.status = 'accepted'
      AND (
        batch.id IS NULL
        OR batch.branch_id IS DISTINCT FROM document.branch_id
        OR batch.catalog_id IS DISTINCT FROM item.catalog_id
        OR batch.batch_number IS DISTINCT FROM item.batch_number
        OR batch.manufactured_at IS DISTINCT FROM item.manufactured_at
        OR batch.expires_at IS DISTINCT FROM item.expires_at
        OR batch.purchase_price IS DISTINCT FROM item.purchase_price
        OR batch.sale_price IS DISTINCT FROM item.sale_price
        OR batch.qty_initial IS DISTINCT FROM item.qty
        OR 1 <> (
          SELECT pg_catalog.count(*)
          FROM public.batch_movement AS movement
          WHERE movement.tenant_id = item.tenant_id
            AND movement.source_table = 'incoming_item'
            AND movement.source_id = item.id
            AND movement.batch_id = item.created_batch_id
            AND movement.movement_type = 'incoming'
            AND movement.qty_delta = item.qty
        )
      )
  ) OR EXISTS (
    SELECT 1
    FROM public.batch_movement AS movement
    LEFT JOIN public.incoming_item AS item
      ON item.tenant_id = movement.tenant_id
     AND item.id = movement.source_id
    LEFT JOIN public.incoming_document AS document
      ON document.tenant_id = item.tenant_id
     AND document.id = item.document_id
    WHERE movement.source_table = 'incoming_item'
      AND (
        item.id IS NULL
        OR document.status IS DISTINCT FROM 'accepted'
        OR movement.batch_id IS DISTINCT FROM item.created_batch_id
        OR movement.movement_type IS DISTINCT FROM 'incoming'
        OR movement.qty_delta IS DISTINCT FROM item.qty
      )
  ) OR EXISTS (
    SELECT 1
    FROM public.supplier_return AS supplier_return
    LEFT JOIN public.incoming_document AS document
      ON document.tenant_id = supplier_return.tenant_id
     AND document.id = supplier_return.source_document_id
    LEFT JOIN public.incoming_item AS item
      ON item.tenant_id = document.tenant_id
     AND item.document_id = document.id
     AND item.created_batch_id = supplier_return.batch_id
    WHERE document.id IS NULL
      OR document.supplier_id IS DISTINCT FROM supplier_return.supplier_id
      OR document.status IS DISTINCT FROM 'accepted'
      OR item.id IS NULL
      OR 1 <> (
        SELECT pg_catalog.count(*)
        FROM public.batch_movement AS movement
        WHERE movement.tenant_id = supplier_return.tenant_id
          AND movement.source_table = 'supplier_return'
          AND movement.source_id = supplier_return.id
          AND movement.batch_id = supplier_return.batch_id
          AND movement.movement_type = 'supplier_return'
          AND movement.qty_delta = -supplier_return.qty
      )
  ) OR EXISTS (
    SELECT 1
    FROM public.batch_movement AS movement
    LEFT JOIN public.supplier_return AS supplier_return
      ON supplier_return.tenant_id = movement.tenant_id
     AND supplier_return.id = movement.source_id
    WHERE movement.source_table = 'supplier_return'
      AND (
        supplier_return.id IS NULL
        OR movement.batch_id IS DISTINCT FROM supplier_return.batch_id
        OR movement.movement_type IS DISTINCT FROM 'supplier_return'
        OR movement.qty_delta IS DISTINCT FROM -supplier_return.qty
      )
  ) THEN
    RAISE EXCEPTION 'Cannot harden supplier provenance: inconsistent ledger data exists';
  END IF;
END;
$$;
"""


def upgrade() -> None:
    op.execute(
        "LOCK TABLE public.supplier, public.incoming_document, public.incoming_item, "
        "public.batch, public.batch_movement, public.supplier_return, public.audit_log "
        "IN SHARE ROW EXCLUSIVE MODE"
    )
    op.execute(PROVENANCE_PREFLIGHT_SQL)

    op.add_column(
        "supplier",
        sa.Column("create_request_fingerprint", sa.Text(), nullable=True),
    )
    op.execute("""
        UPDATE public.supplier
        SET create_request_fingerprint = pg_catalog.md5(
          '{"address":' || COALESCE(pg_catalog.to_jsonb(address)::TEXT, 'null') ||
          ',"contact_person":' || COALESCE(pg_catalog.to_jsonb(contact_person)::TEXT, 'null') ||
          ',"email":' || COALESCE(pg_catalog.to_jsonb(email)::TEXT, 'null') ||
          ',"inn_or_tin":' || COALESCE(pg_catalog.to_jsonb(inn_or_tin)::TEXT, 'null') ||
          ',"legal_name":' || COALESCE(pg_catalog.to_jsonb(legal_name)::TEXT, 'null') ||
          ',"name":' || COALESCE(pg_catalog.to_jsonb(name)::TEXT, 'null') ||
          ',"notes":' || COALESCE(pg_catalog.to_jsonb(notes)::TEXT, 'null') ||
          ',"phone":' || COALESCE(pg_catalog.to_jsonb(phone)::TEXT, 'null') || '}'
        ) || pg_catalog.md5(
          'legacy:' ||
          '{"address":' || COALESCE(pg_catalog.to_jsonb(address)::TEXT, 'null') ||
          ',"contact_person":' || COALESCE(pg_catalog.to_jsonb(contact_person)::TEXT, 'null') ||
          ',"email":' || COALESCE(pg_catalog.to_jsonb(email)::TEXT, 'null') ||
          ',"inn_or_tin":' || COALESCE(pg_catalog.to_jsonb(inn_or_tin)::TEXT, 'null') ||
          ',"legal_name":' || COALESCE(pg_catalog.to_jsonb(legal_name)::TEXT, 'null') ||
          ',"name":' || COALESCE(pg_catalog.to_jsonb(name)::TEXT, 'null') ||
          ',"notes":' || COALESCE(pg_catalog.to_jsonb(notes)::TEXT, 'null') ||
          ',"phone":' || COALESCE(pg_catalog.to_jsonb(phone)::TEXT, 'null') || '}'
        )
        """)
    op.alter_column("supplier", "create_request_fingerprint", nullable=False)
    op.create_check_constraint(
        "ck_supplier_create_request_fingerprint",
        "supplier",
        "create_request_fingerprint ~ '^[0-9a-f]{64}$'",
    )

    op.create_index(
        "uq_bm_incoming_item_source",
        "batch_movement",
        ["tenant_id", "source_table", "source_id"],
        unique=True,
        postgresql_where=sa.text("source_table = 'incoming_item'"),
    )
    op.create_index(
        "uq_bm_supplier_return_source",
        "batch_movement",
        ["tenant_id", "source_table", "source_id"],
        unique=True,
        postgresql_where=sa.text("source_table = 'supplier_return'"),
    )
    op.execute(MOVEMENT_PROVENANCE_SQL)
    op.execute(
        "ALTER FUNCTION public.trg_validate_supplier_movement_provenance() "
        "OWNER TO aurum_schema_owner"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.trg_validate_supplier_movement_provenance() "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute("""
        CREATE CONSTRAINT TRIGGER trg_validate_supplier_movement_provenance
        AFTER INSERT ON public.batch_movement
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION public.trg_validate_supplier_movement_provenance()
        """)

    op.execute(AUDIT_TABLE_REDACTION_SQL)
    op.execute(
        "ALTER FUNCTION public.audit_redact_table_snapshot(TEXT, JSONB) "
        "OWNER TO aurum_schema_owner"
    )
    op.execute(
        "REVOKE ALL ON FUNCTION public.audit_redact_table_snapshot(TEXT, JSONB) "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute(_audit_trigger_sql("public.audit_redact_table_snapshot"))
    op.execute("ALTER FUNCTION public.trg_audit_log() OWNER TO aurum_schema_owner")
    op.execute(
        "REVOKE ALL ON FUNCTION public.trg_audit_log() " "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute("""
        UPDATE public.audit_log
        SET old_values = public.audit_redact_table_snapshot(table_name, old_values),
            new_values = public.audit_redact_table_snapshot(table_name, new_values),
            changed_fields = public.audit_redact_table_snapshot(table_name, changed_fields)
        WHERE table_name IN ('incoming_document', 'supplier_return')
        """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_validate_supplier_movement_provenance "
        "ON public.batch_movement"
    )
    op.execute("DROP FUNCTION IF EXISTS public.trg_validate_supplier_movement_provenance()")
    op.drop_index("uq_bm_supplier_return_source", table_name="batch_movement")
    op.drop_index("uq_bm_incoming_item_source", table_name="batch_movement")

    op.execute(_audit_trigger_sql("public.audit_redact_jsonb", table_aware=False))
    op.execute("ALTER FUNCTION public.trg_audit_log() OWNER TO aurum_schema_owner")
    op.execute(
        "REVOKE ALL ON FUNCTION public.trg_audit_log() " "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute("DROP FUNCTION IF EXISTS public.audit_redact_table_snapshot(TEXT, JSONB)")

    op.drop_constraint(
        "ck_supplier_create_request_fingerprint",
        "supplier",
        type_="check",
    )
    op.drop_column("supplier", "create_request_fingerprint")
