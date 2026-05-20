"""audit: audit_log table + triggers on every tenant-data table

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-20

audit_log is an INSERT-only ledger for every change to tenant data,
plus three explicit action types written by the service layer:
VIEW (looking at a sensitive record), EXPORT (data extraction),
IMPERSONATE (support session under a tenant context).

trg_audit_log() was created in migration 0001; here we redefine it so
it can also log the `tenant` table — which has no `tenant_id` column,
because the row IS the tenant. The redefinition swallows the
"record has no field tenant_id" error and falls back to NEW.id (for
root-scoped writes) or current_tenant_id().

NOT triggered: session / email_code / login_attempt (auth-internal),
batch_movement (already implied by the parent write_off/sale/incoming).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "0010"
down_revision: Union[str, Sequence[str], None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TRIGGERED_TABLES_FULL = [
    # AFTER INSERT OR UPDATE OR DELETE
    "tenant",
    "tenant_settings",
    "branch",
    "register",
    "user_assignment",
    "role",
    "tenant_catalog",
    "batch",
    "supplier",
    "incoming_document",
    "sale",
    "invoice",
    "payment",
]

TRIGGERED_TABLES_INSERT_ONLY = [
    # AFTER INSERT only — these tables are immutable by design
    "write_off",
    "supplier_return",
]


def upgrade() -> None:
    # ---- audit_log table ----------------------------------------------------
    op.execute(
        """
        CREATE TABLE audit_log (
          id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id       UUID,
          user_id         UUID REFERENCES app_user(id),
          action          TEXT NOT NULL
                            CHECK (action IN ('INSERT','UPDATE','DELETE','VIEW','EXPORT','IMPERSONATE')),
          table_name      TEXT NOT NULL,
          record_id       UUID,
          old_values      JSONB,
          new_values      JSONB,
          changed_fields  JSONB,
          ip_address      INET,
          user_agent      TEXT,
          metadata        JSONB,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_al_tenant_time ON audit_log (tenant_id, created_at DESC) "
        "WHERE tenant_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_al_user_time ON audit_log (user_id, created_at DESC) "
        "WHERE user_id IS NOT NULL"
    )
    op.execute("CREATE INDEX ix_al_table_record ON audit_log (table_name, record_id)")
    op.execute("CREATE INDEX ix_al_action ON audit_log (action, created_at DESC)")
    op.execute("ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON audit_log
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )

    # ---- redefine trg_audit_log() to tolerate tables without tenant_id ------
    op.execute(
        """
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
    )

    # ---- attach triggers to all listed tables ------------------------------
    for table in TRIGGERED_TABLES_FULL:
        op.execute(
            f"""
            CREATE TRIGGER trg_audit_{table}
              AFTER INSERT OR UPDATE OR DELETE ON {table}
              FOR EACH ROW EXECUTE FUNCTION trg_audit_log()
            """
        )

    for table in TRIGGERED_TABLES_INSERT_ONLY:
        op.execute(
            f"""
            CREATE TRIGGER trg_audit_{table}
              AFTER INSERT ON {table}
              FOR EACH ROW EXECUTE FUNCTION trg_audit_log()
            """
        )


def downgrade() -> None:
    for table in TRIGGERED_TABLES_FULL + TRIGGERED_TABLES_INSERT_ONLY:
        op.execute(f"DROP TRIGGER IF EXISTS trg_audit_{table} ON {table}")
    op.execute("DROP TABLE IF EXISTS audit_log CASCADE")
    # Restore the simpler version of trg_audit_log from migration 0001 so a
    # downgrade leaves a working baseline.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION trg_audit_log() RETURNS TRIGGER AS $$
        DECLARE
          v_tenant_id UUID;
          v_user_id UUID;
          v_old_data JSONB;
          v_new_data JSONB;
          v_changed_fields JSONB;
        BEGIN
          v_tenant_id := COALESCE(
            CASE WHEN TG_OP = 'DELETE' THEN (OLD).tenant_id ELSE (NEW).tenant_id END,
            current_tenant_id()
          );
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
            CASE WHEN TG_OP = 'DELETE' THEN (OLD).id ELSE (NEW).id END,
            v_old_data, v_new_data, v_changed_fields, now()
          );

          IF TG_OP = 'DELETE' THEN
            RETURN OLD;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
