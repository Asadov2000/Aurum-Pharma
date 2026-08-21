"""guard incoming document lifecycle and finalized receipt contents

Revision ID: 0106
Revises: 0105
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0106"
down_revision: str | None = "0105"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PREFLIGHT_SQL = """
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM public.incoming_item AS item
    JOIN public.incoming_document AS document ON document.id = item.document_id
    WHERE item.tenant_id IS DISTINCT FROM document.tenant_id
  ) THEN
    RAISE EXCEPTION 'Incoming item tenant scope is inconsistent';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM public.incoming_document AS document
    WHERE document.status = 'accepted'
      AND (
        document.accepted_at IS NULL
        OR NOT EXISTS (
          SELECT 1
          FROM public.incoming_item AS item
          WHERE item.document_id = document.id
        )
        OR EXISTS (
          SELECT 1
          FROM public.incoming_item AS item
          WHERE item.document_id = document.id
            AND item.created_batch_id IS NULL
        )
      )
  ) THEN
    RAISE EXCEPTION 'Accepted incoming document history is incomplete';
  END IF;
END;
$$;
"""


DOCUMENT_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_incoming_document_lifecycle()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'draft' THEN
      RAISE EXCEPTION 'Incoming document must be created as a draft'
        USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  END IF;

  IF TG_OP = 'DELETE' THEN
    IF OLD.status <> 'draft' THEN
      RAISE EXCEPTION 'Finalized incoming document cannot be deleted'
        USING ERRCODE = 'check_violation';
    END IF;
    RETURN OLD;
  END IF;

  IF OLD.status <> 'draft' THEN
    RAISE EXCEPTION 'Finalized incoming document cannot be changed'
      USING ERRCODE = 'check_violation';
  END IF;

  IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id THEN
    RAISE EXCEPTION 'Incoming document tenant ownership cannot be changed'
      USING ERRCODE = 'check_violation';
  END IF;

  IF NEW.status NOT IN ('draft', 'accepted', 'rejected') THEN
    RAISE EXCEPTION 'Unsupported incoming document status transition'
      USING ERRCODE = 'check_violation';
  END IF;

  IF NEW.status <> 'draft'
     AND (
       NEW.branch_id IS DISTINCT FROM OLD.branch_id
       OR NEW.supplier_id IS DISTINCT FROM OLD.supplier_id
     ) THEN
    RAISE EXCEPTION 'Incoming document ownership cannot change during finalization'
      USING ERRCODE = 'check_violation';
  END IF;

  IF NEW.status = 'accepted'
     AND (
       NEW.accepted_at IS NULL
       OR NOT EXISTS (
         SELECT 1
         FROM public.incoming_item AS item
         WHERE item.document_id = NEW.id
       )
       OR EXISTS (
         SELECT 1
         FROM public.incoming_item AS item
         WHERE item.document_id = NEW.id
           AND item.created_batch_id IS NULL
       )
     ) THEN
    RAISE EXCEPTION 'Accepted incoming document requires finalized batches'
      USING ERRCODE = 'check_violation';
  END IF;

  IF NEW.status = 'rejected'
     AND EXISTS (
       SELECT 1
       FROM public.incoming_item AS item
       WHERE item.document_id = NEW.id
         AND item.created_batch_id IS NOT NULL
     ) THEN
    RAISE EXCEPTION 'Rejected incoming document cannot own created batches'
      USING ERRCODE = 'check_violation';
  END IF;

  RETURN NEW;
END;
$$;
"""


ITEM_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_incoming_item_lifecycle()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
  v_document_status TEXT;
  v_document_tenant_id UUID;
BEGIN
  IF TG_OP IN ('UPDATE', 'DELETE') THEN
    SELECT document.status, document.tenant_id
      INTO v_document_status, v_document_tenant_id
    FROM public.incoming_document AS document
    WHERE document.id = OLD.document_id
    FOR SHARE;

    IF NOT FOUND
       OR v_document_status <> 'draft'
       OR v_document_tenant_id IS DISTINCT FROM OLD.tenant_id THEN
      RAISE EXCEPTION 'Incoming items require a matching draft document'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  IF TG_OP = 'UPDATE'
     AND (
       NEW.document_id IS DISTINCT FROM OLD.document_id
       OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
     ) THEN
    RAISE EXCEPTION 'Incoming item ownership cannot be changed'
      USING ERRCODE = 'check_violation';
  END IF;

  IF TG_OP IN ('INSERT', 'UPDATE') THEN
    SELECT document.status, document.tenant_id
      INTO v_document_status, v_document_tenant_id
    FROM public.incoming_document AS document
    WHERE document.id = NEW.document_id
    FOR SHARE;

    IF NOT FOUND
       OR v_document_status <> 'draft'
       OR v_document_tenant_id IS DISTINCT FROM NEW.tenant_id THEN
      RAISE EXCEPTION 'Incoming items require a matching draft document'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$;
"""


def upgrade() -> None:
    for table in ("incoming_document", "incoming_item"):
        op.execute(f"LOCK TABLE public.{table} IN SHARE ROW EXCLUSIVE MODE")
    op.execute(PREFLIGHT_SQL)
    op.execute(DOCUMENT_GUARD_SQL)
    op.execute(ITEM_GUARD_SQL)

    for function_name in (
        "trg_guard_incoming_document_lifecycle",
        "trg_guard_incoming_item_lifecycle",
    ):
        op.execute(f"ALTER FUNCTION public.{function_name}() OWNER TO aurum_schema_owner")
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{function_name}() "
            "FROM PUBLIC, aurum_app, aurum_support"
        )

    op.execute("""
        CREATE TRIGGER trg_guard_incoming_document_lifecycle
          BEFORE INSERT OR UPDATE OR DELETE ON public.incoming_document
          FOR EACH ROW
          EXECUTE FUNCTION public.trg_guard_incoming_document_lifecycle()
        """)
    op.execute("""
        CREATE TRIGGER trg_guard_incoming_item_lifecycle
          BEFORE INSERT OR UPDATE OR DELETE ON public.incoming_item
          FOR EACH ROW
          EXECUTE FUNCTION public.trg_guard_incoming_item_lifecycle()
        """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_guard_incoming_item_lifecycle "
        "ON public.incoming_item"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_guard_incoming_document_lifecycle "
        "ON public.incoming_document"
    )
    op.execute("DROP FUNCTION IF EXISTS public.trg_guard_incoming_item_lifecycle()")
    op.execute("DROP FUNCTION IF EXISTS public.trg_guard_incoming_document_lifecycle()")
