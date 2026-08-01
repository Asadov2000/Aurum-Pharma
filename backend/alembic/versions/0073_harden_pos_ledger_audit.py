"""security: seal inventory movements and audit POS financial rows

Revision ID: 0073
Revises: 0072
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0073"
down_revision: str | Sequence[str] | None = "0072"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SENSITIVE_FIELDS = (
    "password_hash",
    "totp_secret",
    "refresh_token_hash",
    "code_hash",
    "code_salt",
    "jwt_secret",
    "access_token",
    "refresh_token",
    "email",
    "email_lower",
    "phone",
    "recipient",
    "full_name",
    "owner_full_name",
    "cashier_name",
    "patient_name",
    "doctor_name",
    "doctor_license",
    "contact_person",
    "purchase_price",
    "prescription_number",
    "notes",
    "comment",
)

LEGACY_SENSITIVE_FIELDS = tuple(field for field in SENSITIVE_FIELDS if field != "comment")


def _recursive_redaction_function_sql(fields: tuple[str, ...]) -> str:
    field_list = ",\n".join(f"        '{field}'" for field in fields)
    return f"""
    CREATE OR REPLACE FUNCTION public.audit_redact_jsonb(p_data JSONB)
    RETURNS JSONB AS $$
    DECLARE
      v_result JSONB;
      v_key TEXT;
      v_value JSONB;
      v_key_lower TEXT;
      v_sensitive_fields TEXT[] := ARRAY[
{field_list}
      ];
    BEGIN
      IF p_data IS NULL THEN
        RETURN NULL;
      END IF;

      IF pg_catalog.jsonb_typeof(p_data) = 'object' THEN
        v_result := '{{}}'::JSONB;
        FOR v_key, v_value IN
          SELECT item.key, item.value
          FROM pg_catalog.jsonb_each(p_data) AS item
        LOOP
          v_key_lower := pg_catalog.lower(v_key);
          IF v_key_lower = ANY(v_sensitive_fields)
            OR v_key_lower LIKE '%\\_email' ESCAPE '\\'
            OR v_key_lower LIKE '%\\_phone' ESCAPE '\\'
          THEN
            v_result := v_result || pg_catalog.jsonb_build_object(
              v_key,
              pg_catalog.to_jsonb('***'::TEXT)
            );
          ELSE
            v_result := v_result || pg_catalog.jsonb_build_object(
              v_key,
              public.audit_redact_jsonb(v_value)
            );
          END IF;
        END LOOP;
        RETURN v_result;
      END IF;

      IF pg_catalog.jsonb_typeof(p_data) = 'array' THEN
        SELECT COALESCE(
          pg_catalog.jsonb_agg(
            public.audit_redact_jsonb(item.value)
            ORDER BY item.ordinality
          ),
          '[]'::JSONB
        )
        INTO v_result
        FROM pg_catalog.jsonb_array_elements(p_data)
          WITH ORDINALITY AS item(value, ordinality);
        RETURN v_result;
      END IF;

      RETURN p_data;
    END;
    $$ LANGUAGE plpgsql IMMUTABLE
    SECURITY INVOKER
    SET search_path = pg_catalog, pg_temp
    """


def _legacy_redaction_function_sql(fields: tuple[str, ...]) -> str:
    field_list = ",\n".join(f"        '{field}'" for field in fields)
    return f"""
    CREATE OR REPLACE FUNCTION public.audit_redact_jsonb(p_data JSONB)
    RETURNS JSONB AS $$
    DECLARE
      v_result JSONB := p_data;
      v_key TEXT;
      v_key_lower TEXT;
      v_sensitive_fields TEXT[] := ARRAY[
{field_list}
      ];
    BEGIN
      IF v_result IS NULL THEN
        RETURN NULL;
      END IF;

      FOR v_key IN SELECT pg_catalog.jsonb_object_keys(v_result)
      LOOP
        v_key_lower := pg_catalog.lower(v_key);
        IF v_key_lower = ANY(v_sensitive_fields)
          OR v_key_lower LIKE '%\\_email' ESCAPE '\\'
          OR v_key_lower LIKE '%\\_phone' ESCAPE '\\'
        THEN
          v_result := pg_catalog.jsonb_set(
            v_result,
            ARRAY[v_key],
            pg_catalog.to_jsonb('***'::TEXT),
            false
          );
        END IF;
      END LOOP;

      RETURN v_result;
    END;
    $$ LANGUAGE plpgsql IMMUTABLE
    """


BATCH_MOVEMENT_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_batch_movement_immutability()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
  RAISE EXCEPTION 'Batch movement ledger is immutable'
    USING ERRCODE = 'check_violation';
END;
$$;
"""


def upgrade() -> None:
    for table in ("sale_item", "sale_payment", "batch_movement"):
        op.execute(f"LOCK TABLE public.{table} IN SHARE ROW EXCLUSIVE MODE")

    op.execute(_recursive_redaction_function_sql(SENSITIVE_FIELDS))

    for table in ("sale_item", "sale_payment"):
        op.execute(f"""
            CREATE TRIGGER trg_audit_{table}
              AFTER INSERT OR UPDATE OR DELETE ON public.{table}
              FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log()
            """)

    op.execute(BATCH_MOVEMENT_GUARD_SQL)
    op.execute(
        "REVOKE ALL ON FUNCTION public.trg_guard_batch_movement_immutability() "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute("""
        CREATE TRIGGER trg_guard_batch_movement_immutability
          BEFORE UPDATE OR DELETE ON public.batch_movement
          FOR EACH ROW
          EXECUTE FUNCTION public.trg_guard_batch_movement_immutability()
        """)
    op.execute(
        "REVOKE UPDATE, DELETE ON TABLE public.batch_movement " "FROM aurum_app, aurum_support"
    )

    op.execute("""
        DO $$
        DECLARE
          v_guard_owner TEXT;
        BEGIN
          SELECT pg_catalog.pg_get_userbyid(routines.proowner)
            INTO v_guard_owner
          FROM pg_catalog.pg_proc AS routines
          JOIN pg_catalog.pg_namespace AS schemas
            ON schemas.oid = routines.pronamespace
          WHERE schemas.nspname = 'public'
            AND routines.proname = 'trg_guard_batch_movement_immutability';

          IF v_guard_owner IS DISTINCT FROM 'aurum_schema_owner'
             OR pg_catalog.has_function_privilege(
               'aurum_app',
               'public.trg_guard_batch_movement_immutability()',
               'EXECUTE'
             )
             OR pg_catalog.has_function_privilege(
               'aurum_support',
               'public.trg_guard_batch_movement_immutability()',
               'EXECUTE'
             )
          THEN
            RAISE EXCEPTION 'Unsafe batch movement guard configuration';
          END IF;
        END;
        $$;
        """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_guard_batch_movement_immutability " "ON public.batch_movement"
    )
    op.execute("DROP FUNCTION public.trg_guard_batch_movement_immutability()")
    op.execute("GRANT UPDATE, DELETE ON TABLE public.batch_movement " "TO aurum_app, aurum_support")

    for table in ("sale_payment", "sale_item"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_audit_{table} ON public.{table}")

    op.execute(_legacy_redaction_function_sql(LEGACY_SENSITIVE_FIELDS))
