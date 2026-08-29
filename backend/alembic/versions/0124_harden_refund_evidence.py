"""harden refund reason codes and audit privacy

Revision ID: 0124
Revises: 0123
Create Date: 2026-08-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0124"
down_revision: str | Sequence[str] | None = "0123"
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


def _quarantine_audit_function_sql(*, include_reason: bool) -> str:
    reason_field = ",\n              'refund_reason', NEW.refund_reason" if include_reason else ""
    return f"""
        CREATE OR REPLACE FUNCTION public.trg_audit_customer_return_quarantine()
        RETURNS TRIGGER AS $$
        BEGIN
          INSERT INTO public.audit_log (
            tenant_id, user_id, action, table_name, record_id, metadata, created_at
          ) VALUES (
            NEW.tenant_id, NEW.received_by, 'INSERT',
            'customer_return_quarantine_item', NEW.id,
            jsonb_build_object(
              'branch_id', NEW.branch_id,
              'return_sale_id', NEW.return_sale_id,
              'return_sale_item_id', NEW.return_sale_item_id,
              'parent_sale_id', NEW.parent_sale_id,
              'parent_sale_item_id', NEW.parent_sale_item_id,
              'catalog_id', NEW.catalog_id,
              'batch_id', NEW.batch_id,
              'qty', NEW.qty{reason_field}
            ), NEW.created_at
          );
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER
        SET search_path = pg_catalog, public, pg_temp
        """


def upgrade() -> None:
    # NOT VALID preserves immutable historical rows while PostgreSQL enforces
    # the controlled vocabulary for every new refund from this revision onward.
    op.execute("""
        ALTER TABLE public.customer_return_quarantine_item
        ADD CONSTRAINT ck_customer_return_quarantine_refund_reason_code
        CHECK (
          refund_reason IS NULL OR refund_reason IN (
            'dispensing_error', 'duplicate_sale', 'pricing_error',
            'quality_issue', 'damaged_package', 'customer_cancelled', 'other'
          )
        ) NOT VALID
        """)
    op.execute(_recursive_redaction_function_sql((*SENSITIVE_FIELDS, "refund_comment")))
    # The audit trail records the controlled reason, never the free-text comment.
    op.execute(_quarantine_audit_function_sql(include_reason=True))


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.customer_return_quarantine_item "
        "DROP CONSTRAINT IF EXISTS ck_customer_return_quarantine_refund_reason_code"
    )
    op.execute(_quarantine_audit_function_sql(include_reason=False))
    op.execute(_recursive_redaction_function_sql(SENSITIVE_FIELDS))
