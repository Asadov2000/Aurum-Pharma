"""audit: redact sensitive fields before writing JSON snapshots

Revision ID: 0024
Revises: 0023
Create Date: 2026-06-26

The API already redacts audit payloads before returning them to clients.
This migration moves the same protection into PostgreSQL triggers so raw
audit_log rows no longer store credentials, contact fields, patient/person
fields, or purchase prices in clear text.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0024"
down_revision: str | Sequence[str] | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


AUDIT_REDACT_JSONB_SQL = """
CREATE OR REPLACE FUNCTION audit_redact_jsonb(p_data JSONB) RETURNS JSONB AS $$
DECLARE
  v_result JSONB := p_data;
  v_key TEXT;
  v_key_lower TEXT;
  v_sensitive_fields TEXT[] := ARRAY[
    'password_hash',
    'totp_secret',
    'refresh_token_hash',
    'code_hash',
    'code_salt',
    'jwt_secret',
    'access_token',
    'refresh_token',
    'email',
    'email_lower',
    'phone',
    'recipient',
    'full_name',
    'owner_full_name',
    'patient_name',
    'doctor_name',
    'doctor_license',
    'contact_person',
    'purchase_price'
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
      v_result := jsonb_set(v_result, ARRAY[v_key], to_jsonb('***'::text), false);
    END IF;
  END LOOP;

  RETURN v_result;
END;
$$ LANGUAGE plpgsql IMMUTABLE;
"""


REDACTED_AUDIT_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION trg_audit_log() RETURNS TRIGGER AS $$
DECLARE
  v_tenant_id UUID;
  v_record_id UUID;
  v_user_id UUID;
  v_old_data JSONB;
  v_new_data JSONB;
  v_changed_fields JSONB;
BEGIN
  -- tenant_id resolution: try the row's column, fall back to NEW.id
  -- for root tables (tenant itself), then to GUC.
  BEGIN
    v_tenant_id := COALESCE(
      CASE WHEN TG_OP = 'DELETE' THEN (OLD).tenant_id ELSE (NEW).tenant_id END,
      current_tenant_id()
    );
  EXCEPTION WHEN undefined_column THEN
    BEGIN
      v_tenant_id := COALESCE(
        CASE WHEN TG_OP = 'DELETE' THEN (OLD).id ELSE (NEW).id END,
        current_tenant_id()
      );
    EXCEPTION WHEN OTHERS THEN
      v_tenant_id := current_tenant_id();
    END;
  END;

  BEGIN
    v_record_id := CASE WHEN TG_OP = 'DELETE' THEN (OLD).id ELSE (NEW).id END;
  EXCEPTION WHEN OTHERS THEN
    v_record_id := NULL;
  END;

  v_user_id := current_app_user_id();

  IF TG_OP = 'INSERT' THEN
    v_old_data := NULL;
    v_new_data := to_jsonb(NEW);
  ELSIF TG_OP = 'UPDATE' THEN
    v_old_data := to_jsonb(OLD);
    v_new_data := to_jsonb(NEW);
    SELECT jsonb_object_agg(key, value) INTO v_changed_fields
    FROM jsonb_each(v_new_data) WHERE v_old_data->key IS DISTINCT FROM value;
    IF v_changed_fields IS NULL OR v_changed_fields = '{}'::jsonb THEN
      RETURN NEW;
    END IF;
  ELSE
    v_old_data := to_jsonb(OLD);
    v_new_data := NULL;
  END IF;

  v_old_data := audit_redact_jsonb(v_old_data);
  v_new_data := audit_redact_jsonb(v_new_data);
  v_changed_fields := audit_redact_jsonb(v_changed_fields);

  INSERT INTO audit_log (
    tenant_id, user_id, action, table_name, record_id,
    old_values, new_values, changed_fields, created_at
  ) VALUES (
    v_tenant_id, v_user_id, TG_OP::text, TG_TABLE_NAME::text,
    v_record_id, v_old_data, v_new_data, v_changed_fields, now()
  );

  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


RAW_AUDIT_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION trg_audit_log() RETURNS TRIGGER AS $$
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
      current_tenant_id()
    );
  EXCEPTION WHEN undefined_column THEN
    BEGIN
      v_tenant_id := COALESCE(
        CASE WHEN TG_OP = 'DELETE' THEN (OLD).id ELSE (NEW).id END,
        current_tenant_id()
      );
    EXCEPTION WHEN OTHERS THEN
      v_tenant_id := current_tenant_id();
    END;
  END;

  BEGIN
    v_record_id := CASE WHEN TG_OP = 'DELETE' THEN (OLD).id ELSE (NEW).id END;
  EXCEPTION WHEN OTHERS THEN
    v_record_id := NULL;
  END;

  v_user_id := current_app_user_id();

  IF TG_OP = 'INSERT' THEN
    v_old_data := NULL;
    v_new_data := to_jsonb(NEW);
  ELSIF TG_OP = 'UPDATE' THEN
    v_old_data := to_jsonb(OLD);
    v_new_data := to_jsonb(NEW);
    SELECT jsonb_object_agg(key, value) INTO v_changed_fields
    FROM jsonb_each(v_new_data) WHERE v_old_data->key IS DISTINCT FROM value;
    IF v_changed_fields IS NULL OR v_changed_fields = '{}'::jsonb THEN
      RETURN NEW;
    END IF;
  ELSE
    v_old_data := to_jsonb(OLD);
    v_new_data := NULL;
  END IF;

  INSERT INTO audit_log (
    tenant_id, user_id, action, table_name, record_id,
    old_values, new_values, changed_fields, created_at
  ) VALUES (
    v_tenant_id, v_user_id, TG_OP::text, TG_TABLE_NAME::text,
    v_record_id, v_old_data, v_new_data, v_changed_fields, now()
  );

  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


def upgrade() -> None:
    op.execute(AUDIT_REDACT_JSONB_SQL)
    op.execute(REDACTED_AUDIT_TRIGGER_SQL)


def downgrade() -> None:
    op.execute(RAW_AUDIT_TRIGGER_SQL)
    op.execute("DROP FUNCTION IF EXISTS audit_redact_jsonb(JSONB)")
