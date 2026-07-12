"""security: make audit log append-only and audit prescriptions

Revision ID: 0026
Revises: 0025
Create Date: 2026-07-12
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0026"
down_revision: str | Sequence[str] | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


BASE_SENSITIVE_FIELDS = (
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
    "patient_name",
    "doctor_name",
    "doctor_license",
    "contact_person",
    "purchase_price",
)
PRESCRIPTION_SENSITIVE_FIELDS = ("prescription_number", "notes")


def _redaction_function_sql(fields: tuple[str, ...]) -> str:
    field_list = ",\n".join(f"    '{field}'" for field in fields)
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

      FOR v_key IN SELECT jsonb_object_keys(v_result)
      LOOP
        v_key_lower := lower(v_key);
        IF v_key_lower = ANY(v_sensitive_fields)
          OR v_key_lower LIKE '%\\_email' ESCAPE '\\'
          OR v_key_lower LIKE '%\\_phone' ESCAPE '\\'
        THEN
          v_result := jsonb_set(
            v_result,
            ARRAY[v_key],
            to_jsonb('***'::text),
            false
          );
        END IF;
      END LOOP;

      RETURN v_result;
    END;
    $$ LANGUAGE plpgsql IMMUTABLE
    """


APPEND_AUDIT_EVENT_SQL = """
CREATE FUNCTION public.append_audit_event(
  p_tenant_id UUID,
  p_user_id UUID,
  p_action TEXT,
  p_table_name TEXT,
  p_record_id UUID,
  p_metadata JSONB
) RETURNS UUID AS $$
DECLARE
  v_id UUID;
BEGIN
  IF p_action NOT IN ('VIEW', 'EXPORT', 'IMPERSONATE') THEN
    RAISE EXCEPTION 'Only explicit audit actions may be appended'
      USING ERRCODE = '22023';
  END IF;

  IF NULLIF(btrim(p_table_name), '') IS NULL THEN
    RAISE EXCEPTION 'Audit table name must not be empty'
      USING ERRCODE = '22023';
  END IF;

  IF session_user <> 'aurum_support' THEN
    IF p_tenant_id IS NULL
      OR p_tenant_id IS DISTINCT FROM public.current_tenant_id()
    THEN
      RAISE EXCEPTION 'Audit tenant does not match the active context'
        USING ERRCODE = '42501';
    END IF;

    IF p_user_id IS DISTINCT FROM public.current_app_user_id() THEN
      RAISE EXCEPTION 'Audit user does not match the active context'
        USING ERRCODE = '42501';
    END IF;
  END IF;

  IF p_action = 'IMPERSONATE' AND session_user <> 'aurum_support' THEN
    RAISE EXCEPTION 'Only the support role may log impersonation'
      USING ERRCODE = '42501';
  END IF;

  INSERT INTO public.audit_log (
    tenant_id,
    user_id,
    action,
    table_name,
    record_id,
    metadata,
    created_at
  ) VALUES (
    p_tenant_id,
    p_user_id,
    p_action,
    p_table_name,
    p_record_id,
    public.audit_redact_jsonb(p_metadata),
    now()
  )
  RETURNING id INTO v_id;

  RETURN v_id;
END;
$$ LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
"""


APPEND_AUDIT_EVENT_SIGNATURE = "public.append_audit_event(UUID, UUID, TEXT, TEXT, UUID, JSONB)"

AUDIT_SCHEMA_GUARD_SQL = """
DO $$
BEGIN
  IF has_schema_privilege('aurum_app', 'public', 'CREATE') THEN
    RAISE EXCEPTION
      'aurum_app still has CREATE on public; bootstrap schema ownership first';
  END IF;
END
$$
"""

PRESCRIPTION_AUDIT_TRIGGER_SQL = """
CREATE TRIGGER trg_audit_prescription_log
  AFTER INSERT OR UPDATE OR DELETE ON public.prescription_log
  FOR EACH ROW EXECUTE FUNCTION public.trg_audit_log()
"""


def upgrade() -> None:
    # A SECURITY DEFINER function may only resolve objects from schemas that
    # the runtime role cannot modify.
    op.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC, aurum_app")
    op.execute("GRANT USAGE ON SCHEMA public TO aurum_app")
    op.execute(AUDIT_SCHEMA_GUARD_SQL)

    op.execute(_redaction_function_sql(BASE_SENSITIVE_FIELDS + PRESCRIPTION_SENSITIVE_FIELDS))
    op.execute("ALTER FUNCTION public.trg_audit_log() SECURITY DEFINER")
    op.execute(
        "ALTER FUNCTION public.trg_audit_log() SET search_path = pg_catalog, public, pg_temp"
    )

    op.execute(APPEND_AUDIT_EVENT_SQL)
    op.execute(
        "COMMENT ON FUNCTION "
        f"{APPEND_AUDIT_EVENT_SIGNATURE} IS "
        "'Validated write boundary for explicit immutable audit events'"
    )

    op.execute(PRESCRIPTION_AUDIT_TRIGGER_SQL)

    op.execute("REVOKE ALL PRIVILEGES ON TABLE public.audit_log FROM aurum_app")
    op.execute("GRANT SELECT ON TABLE public.audit_log TO aurum_app")
    op.execute("REVOKE ALL PRIVILEGES ON FUNCTION public.trg_audit_log() " "FROM PUBLIC, aurum_app")
    op.execute(
        "REVOKE ALL PRIVILEGES ON FUNCTION public.audit_redact_jsonb(JSONB) "
        "FROM PUBLIC, aurum_app"
    )
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {APPEND_AUDIT_EVENT_SIGNATURE} FROM PUBLIC")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION {APPEND_AUDIT_EVENT_SIGNATURE} " "TO aurum_app, aurum_support"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_audit_prescription_log ON public.prescription_log")
    op.execute(f"DROP FUNCTION IF EXISTS {APPEND_AUDIT_EVENT_SIGNATURE}")

    op.execute("ALTER FUNCTION public.trg_audit_log() SECURITY INVOKER")
    op.execute("ALTER FUNCTION public.trg_audit_log() RESET search_path")
    op.execute(_redaction_function_sql(BASE_SENSITIVE_FIELDS))

    op.execute("GRANT ALL PRIVILEGES ON TABLE public.audit_log TO aurum_app")
    op.execute("GRANT EXECUTE ON FUNCTION public.trg_audit_log() TO PUBLIC, aurum_app")
    op.execute("GRANT EXECUTE ON FUNCTION public.audit_redact_jsonb(JSONB) " "TO PUBLIC, aurum_app")
    op.execute("GRANT CREATE ON SCHEMA public TO aurum_app")
