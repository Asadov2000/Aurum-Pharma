"""harden incoming finalization and supplier idempotency

Revision ID: 0134
Revises: 0133
Create Date: 2026-09-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0134"
down_revision: str | Sequence[str] | None = "0133"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


INCOMING_ACCEPTANCE_INTEGRITY_SQL = """
CREATE FUNCTION public.trg_validate_incoming_acceptance_integrity()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  IF NEW.status = 'accepted' AND (
    EXISTS (
      SELECT 1
      FROM public.incoming_item AS item
      LEFT JOIN public.batch AS batch
        ON batch.tenant_id = item.tenant_id
       AND batch.id = item.created_batch_id
      WHERE item.document_id = NEW.id
        AND (
          batch.id IS NULL
          OR batch.branch_id IS DISTINCT FROM NEW.branch_id
          OR batch.catalog_id IS DISTINCT FROM item.catalog_id
          OR batch.batch_number IS DISTINCT FROM item.batch_number
          OR batch.manufactured_at IS DISTINCT FROM item.manufactured_at
          OR batch.expires_at IS DISTINCT FROM item.expires_at
          OR batch.purchase_price IS DISTINCT FROM item.purchase_price
          OR batch.sale_price IS DISTINCT FROM item.sale_price
          OR batch.qty_initial IS DISTINCT FROM item.qty
        )
    )
    OR EXISTS (
      SELECT 1
      FROM public.incoming_item AS item
      WHERE item.document_id = NEW.id
        AND 1 <> (
          SELECT pg_catalog.count(*)
          FROM public.batch_movement AS movement
          WHERE movement.tenant_id = item.tenant_id
            AND movement.batch_id = item.created_batch_id
            AND movement.movement_type = 'incoming'
            AND movement.qty_delta = item.qty
            AND movement.source_table = 'incoming_item'
            AND movement.source_id = item.id
        )
    )
  ) THEN
    RAISE EXCEPTION 'Accepted incoming document has inconsistent stock provenance'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$;
"""


SUPPLIER_RETURN_INTEGRITY_SQL = """
CREATE FUNCTION public.trg_validate_supplier_return_integrity()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
    FROM public.incoming_document AS document
    JOIN public.incoming_item AS item
      ON item.tenant_id = document.tenant_id
     AND item.document_id = document.id
     AND item.created_batch_id = NEW.batch_id
    WHERE document.tenant_id = NEW.tenant_id
      AND document.id = NEW.source_document_id
      AND document.supplier_id = NEW.supplier_id
      AND document.status = 'accepted'
  ) OR 1 <> (
    SELECT pg_catalog.count(*)
    FROM public.batch_movement AS movement
    WHERE movement.tenant_id = NEW.tenant_id
      AND movement.batch_id = NEW.batch_id
      AND movement.movement_type = 'supplier_return'
      AND movement.qty_delta = -NEW.qty
      AND movement.source_table = 'supplier_return'
      AND movement.source_id = NEW.id
  ) THEN
    RAISE EXCEPTION 'Supplier return has inconsistent stock provenance'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END;
$$;
"""


def _add_finalize_permission() -> None:
    op.execute("""
        INSERT INTO public.permission (
          code, group_code, name, description, min_level_required,
          is_dangerous, is_active, scope_type, target_role_type, risk_level,
          developer_grantable, administrator_grantable, owner_grantable,
          developer_delegable, administrator_delegable, owner_delegable,
          requires_step_up, requires_confirmation
        ) VALUES (
          'incoming.finalize', 'incoming', 'Проведение прихода',
          'Окончательное принятие или отклонение документа прихода.', 3,
          true, true, 'BRANCH_SET', 'tenant', 'critical',
          true, true, true, true, true, true, false, true
        )
        ON CONFLICT (code) DO NOTHING
        """)
    op.execute(
        "ALTER TABLE public.role_permission " "DISABLE TRIGGER trg_guard_role_permission_mutation"
    )
    op.execute("""
        INSERT INTO public.role_permission (role_id, permission_code)
        SELECT role.id, 'incoming.finalize'
        FROM public.role AS role
        WHERE role.is_active
          AND (
            (role.is_system AND role.level <= 3)
            OR (role.is_protected AND role.protected_kind = 'tenant_owner')
          )
        ON CONFLICT (role_id, permission_code) DO NOTHING
        """)
    op.execute(
        "ALTER TABLE public.role_permission " "ENABLE TRIGGER trg_guard_role_permission_mutation"
    )
    op.execute("""
        INSERT INTO public.role_template_permission (template_id, permission_code)
        SELECT template.id, 'incoming.finalize'
        FROM public.role_template AS template
        WHERE template.slug = 'owner' AND template.is_active
        ON CONFLICT (template_id, permission_code) DO NOTHING
        """)
    op.execute("""
        INSERT INTO public.access_role_version_permission (
          role_version_id, permission_code, created_at
        )
        SELECT version.id, 'incoming.finalize', pg_catalog.statement_timestamp()
        FROM public.access_role_version AS version
        JOIN public.role AS role ON role.id = version.role_id
        WHERE version.status = 'published'
          AND role.is_active
          AND (
            (role.is_system AND role.level <= 3)
            OR (role.is_protected AND role.protected_kind = 'tenant_owner')
          )
        ON CONFLICT (role_version_id, permission_code) DO NOTHING
        """)
    op.execute("SELECT public.bump_all_authorization_policy_revisions()")


def upgrade() -> None:
    for table in (
        "supplier",
        "incoming_document",
        "incoming_item",
        "batch",
        "batch_movement",
        "supplier_return",
    ):
        op.execute(f"LOCK TABLE public.{table} IN SHARE ROW EXCLUSIVE MODE")

    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM public.incoming_item
            WHERE created_batch_id IS NOT NULL
            GROUP BY created_batch_id
            HAVING pg_catalog.count(*) > 1
          ) THEN
            RAISE EXCEPTION 'Cannot harden incoming provenance: a batch has multiple source items';
          END IF;
          IF EXISTS (
            SELECT 1 FROM public.supplier_return WHERE source_document_id IS NULL
          ) THEN
            RAISE EXCEPTION 'Cannot harden supplier returns: source document is missing';
          END IF;
        END;
        $$;
        """)

    op.add_column(
        "supplier",
        sa.Column(
            "operation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
    )
    op.create_unique_constraint(
        "uq_supplier_tenant_operation",
        "supplier",
        ["tenant_id", "operation_id"],
    )
    op.create_unique_constraint(
        "uq_incoming_item_created_batch",
        "incoming_item",
        ["created_batch_id"],
    )
    op.alter_column("supplier_return", "source_document_id", nullable=False)

    for name, table, expression in (
        (
            "ck_id_document_number_length",
            "incoming_document",
            "document_number IS NULL OR char_length(document_number) <= 120",
        ),
        (
            "ck_id_notes_length",
            "incoming_document",
            "notes IS NULL OR char_length(notes) <= 2000",
        ),
        (
            "ck_id_file_path_length",
            "incoming_document",
            "document_file_path IS NULL OR char_length(document_file_path) <= 1000",
        ),
        (
            "ck_ii_batch_number_length",
            "incoming_item",
            "batch_number IS NULL OR char_length(batch_number) <= 120",
        ),
    ):
        op.create_check_constraint(name, table, expression)

    _add_finalize_permission()

    op.execute(INCOMING_ACCEPTANCE_INTEGRITY_SQL)
    op.execute(SUPPLIER_RETURN_INTEGRITY_SQL)
    for function_name in (
        "trg_validate_incoming_acceptance_integrity",
        "trg_validate_supplier_return_integrity",
    ):
        op.execute(f"ALTER FUNCTION public.{function_name}() OWNER TO aurum_schema_owner")
        op.execute(
            f"REVOKE ALL ON FUNCTION public.{function_name}() "
            "FROM PUBLIC, aurum_app, aurum_support"
        )
    op.execute("""
        CREATE CONSTRAINT TRIGGER trg_validate_incoming_acceptance_integrity
        AFTER INSERT OR UPDATE ON public.incoming_document
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public.trg_validate_incoming_acceptance_integrity()
        """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER trg_validate_supplier_return_integrity
        AFTER INSERT ON public.supplier_return
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public.trg_validate_supplier_return_integrity()
        """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_validate_supplier_return_integrity " "ON public.supplier_return"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_validate_incoming_acceptance_integrity "
        "ON public.incoming_document"
    )
    op.execute("DROP FUNCTION IF EXISTS public.trg_validate_supplier_return_integrity()")
    op.execute("DROP FUNCTION IF EXISTS public.trg_validate_incoming_acceptance_integrity()")

    op.execute("""
        DELETE FROM public.access_role_version_permission
        WHERE permission_code = 'incoming.finalize'
        """)
    op.execute(
        "ALTER TABLE public.role_permission " "DISABLE TRIGGER trg_guard_role_permission_mutation"
    )
    op.execute("DELETE FROM public.role_permission WHERE permission_code = 'incoming.finalize'")
    op.execute(
        "ALTER TABLE public.role_permission " "ENABLE TRIGGER trg_guard_role_permission_mutation"
    )
    op.execute("""
        DELETE FROM public.role_template_permission
        WHERE permission_code = 'incoming.finalize'
        """)
    op.execute("SELECT public.bump_all_authorization_policy_revisions()")
    op.execute("DELETE FROM public.permission WHERE code = 'incoming.finalize'")

    for name, table in (
        ("ck_ii_batch_number_length", "incoming_item"),
        ("ck_id_file_path_length", "incoming_document"),
        ("ck_id_notes_length", "incoming_document"),
        ("ck_id_document_number_length", "incoming_document"),
    ):
        op.drop_constraint(name, table, type_="check")
    op.alter_column("supplier_return", "source_document_id", nullable=True)
    op.drop_constraint("uq_incoming_item_created_batch", "incoming_item", type_="unique")
    op.drop_constraint("uq_supplier_tenant_operation", "supplier", type_="unique")
    op.drop_column("supplier", "operation_id")
