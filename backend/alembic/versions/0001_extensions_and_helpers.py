"""extensions and helper functions

Revision ID: 0001
Revises:
Create Date: 2026-05-19

Creates the three Postgres extensions used across the schema (pgcrypto,
pg_trgm, unaccent) and six helper functions:

- RLS context: current_tenant_id, current_app_user_id, is_support_session
- Trigger helpers: trg_set_updated_meta, trg_set_created_meta, trg_audit_log

Triggers themselves are attached to tables in the migrations that create
those tables. trg_audit_log targets a table `audit_log` that is created
in migration 0010 — the function is defined here but does nothing until
the trigger is bound to actual tables later.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # Extensions
    # -------------------------------------------------------------------------
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "pg_trgm"')
    op.execute('CREATE EXTENSION IF NOT EXISTS "unaccent"')

    # -------------------------------------------------------------------------
    # RLS context helpers — read the GUCs set by the auth middleware on each
    # request (app.tenant_id, app.user_id, app.support_session)
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS UUID AS $$
        DECLARE
          v TEXT;
        BEGIN
          v := current_setting('app.tenant_id', true);
          IF v IS NULL OR v = '' THEN
            RETURN NULL;
          END IF;
          RETURN v::UUID;
        EXCEPTION WHEN OTHERS THEN
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql STABLE;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION current_app_user_id() RETURNS UUID AS $$
        DECLARE
          v TEXT;
        BEGIN
          v := current_setting('app.user_id', true);
          IF v IS NULL OR v = '' THEN
            RETURN NULL;
          END IF;
          RETURN v::UUID;
        EXCEPTION WHEN OTHERS THEN
          RETURN NULL;
        END;
        $$ LANGUAGE plpgsql STABLE;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION is_support_session() RETURNS BOOLEAN AS $$
        BEGIN
          RETURN current_setting('app.support_session', true) = 'true';
        EXCEPTION WHEN OTHERS THEN
          RETURN false;
        END;
        $$ LANGUAGE plpgsql STABLE;
        """
    )

    # -------------------------------------------------------------------------
    # Trigger function: BEFORE UPDATE — refresh updated_at and updated_by
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION trg_set_updated_meta() RETURNS TRIGGER AS $$
        BEGIN
          NEW.updated_at := now();
          IF TG_OP = 'UPDATE' AND current_app_user_id() IS NOT NULL THEN
            NEW.updated_by := current_app_user_id();
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # -------------------------------------------------------------------------
    # Trigger function: BEFORE INSERT — fill created_by from the session user
    # if the caller did not set it explicitly
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION trg_set_created_meta() RETURNS TRIGGER AS $$
        BEGIN
          IF NEW.created_by IS NULL AND current_app_user_id() IS NOT NULL THEN
            NEW.created_by := current_app_user_id();
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )

    # -------------------------------------------------------------------------
    # Trigger function: AFTER INSERT/UPDATE/DELETE — write a row into audit_log
    # The `audit_log` table is created in migration 0010; until then this
    # function exists but is not attached to anything.
    # -------------------------------------------------------------------------
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


def downgrade() -> None:
    # Drop functions in reverse order. CASCADE so that any future triggers
    # bound to these functions (added in later migrations) come off cleanly
    # if a developer downgrades all the way to base.
    op.execute("DROP FUNCTION IF EXISTS trg_audit_log() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS trg_set_created_meta() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS trg_set_updated_meta() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS is_support_session() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS current_app_user_id() CASCADE")
    op.execute("DROP FUNCTION IF EXISTS current_tenant_id() CASCADE")

    op.execute('DROP EXTENSION IF EXISTS "unaccent"')
    op.execute('DROP EXTENSION IF EXISTS "pg_trgm"')
    op.execute('DROP EXTENSION IF EXISTS "pgcrypto"')
