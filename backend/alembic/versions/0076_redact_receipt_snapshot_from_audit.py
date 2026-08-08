"""redact immutable receipt snapshots from audit rows

Revision ID: 0076
Revises: 0075
Create Date: 2026-07-30
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0076"
down_revision: str | Sequence[str] | None = "0075"
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
    "receipt_snapshot",
    "notes",
    "comment",
)


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


def upgrade() -> None:
    op.execute(_recursive_redaction_function_sql(SENSITIVE_FIELDS))


def downgrade() -> None:
    op.execute(
        _recursive_redaction_function_sql(
            tuple(field for field in SENSITIVE_FIELDS if field != "receipt_snapshot")
        )
    )
