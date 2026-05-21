"""onboarding: wizard_state, onboarding_checklist

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-20

Two tenant-scoped tables (RLS on) that drive the 8-step setup wizard
and the post-wizard task checklist. Both are populated automatically
when a tenant is created (FoundationService.create_tenant calls the
onboarding hook); the wizard starts at step 1, the checklist at
setup_ends_at = now() + 60 days.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

from alembic import op

revision: str = "0011"
down_revision: Union[str, Sequence[str], None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- wizard_state -------------------------------------------------------
    op.execute(
        """
        CREATE TABLE wizard_state (
          tenant_id        UUID PRIMARY KEY REFERENCES tenant(id) ON DELETE CASCADE,
          current_step     INT NOT NULL DEFAULT 1 CHECK (current_step BETWEEN 1 AND 8),
          steps_completed  JSONB NOT NULL DEFAULT '[]'::jsonb,
          wizard_data      JSONB NOT NULL DEFAULT '{}'::jsonb,
          is_completed     BOOLEAN NOT NULL DEFAULT false,
          started_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
          completed_at     TIMESTAMPTZ,
          updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_wizard_state_updated BEFORE UPDATE ON wizard_state
          FOR EACH ROW EXECUTE FUNCTION trg_set_updated_meta()
        """
    )
    op.execute("ALTER TABLE wizard_state ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON wizard_state
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )

    # ---- onboarding_checklist ----------------------------------------------
    op.execute(
        """
        CREATE TABLE onboarding_checklist (
          tenant_id           UUID PRIMARY KEY REFERENCES tenant(id) ON DELETE CASCADE,
          completed_tasks     JSONB NOT NULL DEFAULT '[]'::jsonb,
          catalog_items_count INT NOT NULL DEFAULT 0,
          trial_eligible      BOOLEAN NOT NULL DEFAULT false,
          trial_started_at    TIMESTAMPTZ,
          setup_ends_at       TIMESTAMPTZ NOT NULL,
          updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_oc_setup_ends ON onboarding_checklist (setup_ends_at) "
        "WHERE trial_started_at IS NULL"
    )
    op.execute(
        """
        CREATE TRIGGER trg_oc_updated BEFORE UPDATE ON onboarding_checklist
          FOR EACH ROW EXECUTE FUNCTION trg_set_updated_meta()
        """
    )
    op.execute("ALTER TABLE onboarding_checklist ENABLE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation ON onboarding_checklist
          USING (tenant_id = current_tenant_id() OR is_support_session())
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS onboarding_checklist CASCADE")
    op.execute("DROP TABLE IF EXISTS wizard_state CASCADE")
