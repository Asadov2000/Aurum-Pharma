"""auth: app_user, session, email_code, login_attempt

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-19

Creates the authentication tables. app_user lives outside the tenant
boundary (it's the identity table) — no tenant_id column, no RLS policy.
session / email_code / login_attempt are also outside tenant scope.

The deferred foreign key app_user.home_tenant_id -> tenant.id is added
later in migration 0003 (foundation), because `tenant` does not exist yet.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # APP_USER — identity table; no tenant_id, no RLS
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE app_user (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          email               TEXT NOT NULL,
          email_lower         TEXT GENERATED ALWAYS AS (lower(email)) STORED,
          full_name           TEXT NOT NULL,
          phone               TEXT,
          password_hash       TEXT,
          totp_secret         TEXT,
          is_developer        BOOLEAN NOT NULL DEFAULT false,
          is_administrator    BOOLEAN NOT NULL DEFAULT false,
          home_tenant_id      UUID,
          status              TEXT NOT NULL DEFAULT 'invited'
                                CHECK (status IN ('invited','active','blocked','archived')),
          invited_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          activated_at        TIMESTAMPTZ,
          blocked_at          TIMESTAMPTZ,
          archived_at         TIMESTAMPTZ,
          last_login_at       TIMESTAMPTZ,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX ux_app_user_email_lower ON app_user (email_lower)")
    op.execute("CREATE INDEX ix_app_user_status ON app_user (status)")
    op.execute(
        "CREATE INDEX ix_app_user_home_tenant ON app_user (home_tenant_id) "
        "WHERE home_tenant_id IS NOT NULL"
    )
    op.execute(
        """
        CREATE TRIGGER trg_app_user_updated BEFORE UPDATE ON app_user
          FOR EACH ROW EXECUTE FUNCTION trg_set_updated_meta()
        """
    )

    # -------------------------------------------------------------------------
    # SESSION — refresh-token storage; hash only, never plaintext
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE session (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          user_id             UUID NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
          refresh_token_hash  TEXT NOT NULL,
          user_agent          TEXT,
          ip_address          INET,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          last_used_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
          expires_at          TIMESTAMPTZ NOT NULL,
          revoked_at          TIMESTAMPTZ,
          revoked_reason      TEXT
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_session_user ON session (user_id) WHERE revoked_at IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_session_refresh_hash ON session (refresh_token_hash)"
    )
    op.execute(
        "CREATE INDEX ix_session_expires ON session (expires_at) WHERE revoked_at IS NULL"
    )

    # -------------------------------------------------------------------------
    # EMAIL_CODE — short-lived numeric codes for login / password reset
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE email_code (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          email_lower         TEXT NOT NULL,
          code_hash           TEXT NOT NULL,
          code_salt           TEXT NOT NULL,
          purpose             TEXT NOT NULL DEFAULT 'login'
                                CHECK (purpose IN ('login','password_reset','email_verification')),
          ip_address          INET,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          expires_at          TIMESTAMPTZ NOT NULL,
          used_at             TIMESTAMPTZ
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_email_code_email ON email_code (email_lower, purpose) "
        "WHERE used_at IS NULL"
    )
    op.execute("CREATE INDEX ix_email_code_expires ON email_code (expires_at)")

    # -------------------------------------------------------------------------
    # LOGIN_ATTEMPT — DB-backed rate-limit ledger + audit trail
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE login_attempt (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          email_lower         TEXT,
          user_id             UUID,
          ip_address          INET NOT NULL,
          user_agent          TEXT,
          outcome             TEXT NOT NULL
                                CHECK (outcome IN (
                                  'code_requested', 'code_failed', 'code_expired',
                                  'password_failed', 'totp_failed', 'success', 'blocked'
                                )),
          metadata_json       JSONB,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_login_attempt_email_time ON login_attempt "
        "(email_lower, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_login_attempt_ip_time ON login_attempt "
        "(ip_address, created_at DESC)"
    )

    _ = sa  # keep import even when no helper is used


def downgrade() -> None:
    # CASCADE handles future foreign keys that would otherwise block dropping
    # app_user (e.g. the deferred FK from migration 0003 onto home_tenant_id).
    op.execute("DROP TABLE IF EXISTS login_attempt CASCADE")
    op.execute("DROP TABLE IF EXISTS email_code CASCADE")
    op.execute("DROP TABLE IF EXISTS session CASCADE")
    op.execute("DROP TABLE IF EXISTS app_user CASCADE")
