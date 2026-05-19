"""foundation: tenant, tenant_settings, branch, register

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-19

Creates the tenancy backbone:
- tenant (root entity, no tenant_id, no RLS — system-wide)
- tenant_settings (one row per tenant, RLS on, isolated by tenant_id)
- branch (pharmacy points; RLS on)
- register (cashier POS terminals; RLS on, scoped to a branch)

Also installs the deferred foreign key app_user.home_tenant_id -> tenant.id
that migration 0002 left open. CHECK constraints on settings (expiry
thresholds ordering) are enforced at the Pydantic layer instead — Postgres
JSONB CHECKs would duplicate the same logic with worse error messages.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "0003"
down_revision: Union[str, Sequence[str], None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # TENANT — root entity, NO tenant_id column, NO RLS
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE tenant (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          name                TEXT NOT NULL,
          legal_name          TEXT,
          inn_or_tin          TEXT,
          registration_number TEXT,
          contact_email       TEXT NOT NULL,
          contact_phone       TEXT,
          legal_address       TEXT,
          logo_url            TEXT,
          status              TEXT NOT NULL DEFAULT 'setup'
                                CHECK (status IN ('setup','trial','active','grace_period','readonly','archived')),
          setup_started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
          trial_started_at    TIMESTAMPTZ,
          trial_ends_at       TIMESTAMPTZ,
          drug_catalog_mode   TEXT NOT NULL DEFAULT 'autonomous'
                                CHECK (drug_catalog_mode IN ('connected','autonomous')),
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          suspended_at        TIMESTAMPTZ,
          archived_at         TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE INDEX ix_tenant_status ON tenant (status)")
    op.execute("CREATE INDEX ix_tenant_contact_email ON tenant (lower(contact_email))")
    op.execute(
        """
        CREATE TRIGGER trg_tenant_updated BEFORE UPDATE ON tenant
          FOR EACH ROW EXECUTE FUNCTION trg_set_updated_meta()
        """
    )

    # -------------------------------------------------------------------------
    # Deferred FK from migration 0002: app_user.home_tenant_id -> tenant.id
    # -------------------------------------------------------------------------
    op.execute(
        """
        ALTER TABLE app_user
          ADD CONSTRAINT fk_app_user_home_tenant
            FOREIGN KEY (home_tenant_id) REFERENCES tenant(id) ON DELETE SET NULL
        """
    )

    # -------------------------------------------------------------------------
    # TENANT_SETTINGS — one row per tenant
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE tenant_settings (
          tenant_id                   UUID PRIMARY KEY REFERENCES tenant(id) ON DELETE CASCADE,
          -- jsonb_build_object avoids the JSON-colon parsing that
          -- SQLAlchemy text() applies to colon-prefixed words inside
          -- op.execute() strings.
          expiry_thresholds           JSONB NOT NULL DEFAULT
                                        jsonb_build_object('yellow', 6, 'orange', 3, 'red', 1),
          expired_sale_mode           TEXT NOT NULL DEFAULT 'strict'
                                        CHECK (expired_sale_mode IN ('strict','warning','off')),
          refund_reason_mode          TEXT NOT NULL DEFAULT 'optional'
                                        CHECK (refund_reason_mode IN
                                          ('required','required_with_text','optional','off')),
          session_admin_minutes       INT NOT NULL DEFAULT 480
                                        CHECK (session_admin_minutes BETWEEN 30 AND 1440),
          session_pos_minutes         INT NOT NULL DEFAULT 480
                                        CHECK (session_pos_minutes BETWEEN 30 AND 1440),
          pin_mode_enabled            BOOLEAN NOT NULL DEFAULT false,
          prescription_warning_text   TEXT NOT NULL DEFAULT
            'Отпуск рецептурных препаратов осуществляется в соответствии с действующим законодательством РТ',
          created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by                  UUID REFERENCES app_user(id)
        )
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_tenant_settings_updated BEFORE UPDATE ON tenant_settings
          FOR EACH ROW EXECUTE FUNCTION trg_set_updated_meta()
        """
    )
    op.execute("ALTER TABLE tenant_settings ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON tenant_settings
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )

    # -------------------------------------------------------------------------
    # BRANCH
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE branch (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id           UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
          name                TEXT NOT NULL,
          address             TEXT,
          branch_type         TEXT NOT NULL DEFAULT 'pharmacy'
                                CHECK (branch_type IN ('pharmacy','pharmacy_post','kiosk')),
          license_number      TEXT,
          license_expires_at  DATE,
          working_hours       JSONB,
          receipt_header      JSONB,
          is_active           BOOLEAN NOT NULL DEFAULT true,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by          UUID REFERENCES app_user(id),
          updated_by          UUID REFERENCES app_user(id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_branch_tenant_active ON branch (tenant_id) WHERE is_active = true"
    )
    op.execute(
        """
        CREATE TRIGGER trg_branch_updated BEFORE UPDATE ON branch
          FOR EACH ROW EXECUTE FUNCTION trg_set_updated_meta()
        """
    )
    op.execute("ALTER TABLE branch ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON branch
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )

    # -------------------------------------------------------------------------
    # REGISTER
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE TABLE register (
          id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id           UUID NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
          branch_id           UUID NOT NULL REFERENCES branch(id),
          name                TEXT NOT NULL,
          printer_type        TEXT CHECK (printer_type IN ('browser','thermal_58','thermal_80','a4')),
          printer_config      JSONB,
          is_active           BOOLEAN NOT NULL DEFAULT true,
          created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by          UUID REFERENCES app_user(id),
          updated_by          UUID REFERENCES app_user(id)
        )
        """
    )
    op.execute("CREATE INDEX ix_register_branch ON register (branch_id)")
    op.execute("CREATE INDEX ix_register_tenant ON register (tenant_id)")
    op.execute(
        """
        CREATE TRIGGER trg_register_updated BEFORE UPDATE ON register
          FOR EACH ROW EXECUTE FUNCTION trg_set_updated_meta()
        """
    )
    op.execute("ALTER TABLE register ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON register
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )


def downgrade() -> None:
    # Reverse order. CASCADE to clean up any future objects that might
    # reference these tables when downgrading all the way to base.
    op.execute("DROP TABLE IF EXISTS register CASCADE")
    op.execute("DROP TABLE IF EXISTS branch CASCADE")
    op.execute("DROP TABLE IF EXISTS tenant_settings CASCADE")
    op.execute(
        "ALTER TABLE app_user DROP CONSTRAINT IF EXISTS fk_app_user_home_tenant"
    )
    op.execute("DROP TABLE IF EXISTS tenant CASCADE")
