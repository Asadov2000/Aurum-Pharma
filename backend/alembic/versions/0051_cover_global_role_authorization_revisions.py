"""security: cover global roles in authorization revisions

Revision ID: 0051
Revises: 0050
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0051"
down_revision: str | Sequence[str] | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


POLICY_MUTATION_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION public.trg_authorization_policy_mutation()
RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.tenant_id IS NULL THEN
      PERFORM public.bump_all_authorization_policy_revisions();
    ELSE
      PERFORM public.bump_authorization_policy_revision(OLD.tenant_id);
    END IF;
    RETURN OLD;
  END IF;

  IF TG_OP = 'INSERT' THEN
    IF NEW.tenant_id IS NULL THEN
      PERFORM public.bump_all_authorization_policy_revisions();
    ELSE
      PERFORM public.bump_authorization_policy_revision(NEW.tenant_id);
    END IF;
    RETURN NEW;
  END IF;

  IF OLD.tenant_id IS NULL OR NEW.tenant_id IS NULL THEN
    PERFORM public.bump_all_authorization_policy_revisions();
  ELSE
    PERFORM public.bump_authorization_policy_revision(NEW.tenant_id);
    IF OLD.tenant_id IS DISTINCT FROM NEW.tenant_id THEN
      PERFORM public.bump_authorization_policy_revision(OLD.tenant_id);
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ROLE_PERMISSION_MUTATION_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION public.trg_authorization_role_permission_mutation()
RETURNS TRIGGER AS $$
DECLARE
  v_old_tenant_id UUID;
  v_new_tenant_id UUID;
BEGIN
  IF TG_OP = 'DELETE' THEN
    SELECT tenant_id INTO v_old_tenant_id
    FROM public.role
    WHERE id = OLD.role_id;

    IF v_old_tenant_id IS NULL THEN
      PERFORM public.bump_all_authorization_policy_revisions();
    ELSE
      PERFORM public.bump_authorization_policy_revision(v_old_tenant_id);
    END IF;
    RETURN OLD;
  END IF;

  IF TG_OP = 'INSERT' THEN
    SELECT tenant_id INTO v_new_tenant_id
    FROM public.role
    WHERE id = NEW.role_id;

    IF v_new_tenant_id IS NULL THEN
      PERFORM public.bump_all_authorization_policy_revisions();
    ELSE
      PERFORM public.bump_authorization_policy_revision(v_new_tenant_id);
    END IF;
    RETURN NEW;
  END IF;

  SELECT tenant_id INTO v_old_tenant_id
  FROM public.role
  WHERE id = OLD.role_id;
  SELECT tenant_id INTO v_new_tenant_id
  FROM public.role
  WHERE id = NEW.role_id;

  IF v_old_tenant_id IS NULL OR v_new_tenant_id IS NULL THEN
    PERFORM public.bump_all_authorization_policy_revisions();
  ELSE
    PERFORM public.bump_authorization_policy_revision(v_new_tenant_id);
    IF v_old_tenant_id IS DISTINCT FROM v_new_tenant_id THEN
      PERFORM public.bump_authorization_policy_revision(v_old_tenant_id);
    END IF;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LEGACY_POLICY_MUTATION_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION public.trg_authorization_policy_mutation()
RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    PERFORM public.bump_authorization_policy_revision(OLD.tenant_id);
    RETURN OLD;
  END IF;

  PERFORM public.bump_authorization_policy_revision(NEW.tenant_id);
  IF TG_OP = 'UPDATE' AND OLD.tenant_id IS DISTINCT FROM NEW.tenant_id THEN
    PERFORM public.bump_authorization_policy_revision(OLD.tenant_id);
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LEGACY_ROLE_PERMISSION_MUTATION_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION public.trg_authorization_role_permission_mutation()
RETURNS TRIGGER AS $$
DECLARE
  v_old_tenant_id UUID;
  v_new_tenant_id UUID;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    SELECT tenant_id INTO v_old_tenant_id
    FROM public.role
    WHERE id = OLD.role_id;
    PERFORM public.bump_authorization_policy_revision(v_old_tenant_id);
  END IF;

  IF TG_OP <> 'DELETE' THEN
    SELECT tenant_id INTO v_new_tenant_id
    FROM public.role
    WHERE id = NEW.role_id;
    IF v_new_tenant_id IS DISTINCT FROM v_old_tenant_id THEN
      PERFORM public.bump_authorization_policy_revision(v_new_tenant_id);
    END IF;
  END IF;

  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


def _replace_trigger_functions(policy_sql: str, role_permission_sql: str) -> None:
    op.execute(policy_sql)
    op.execute(role_permission_sql)
    for signature in (
        "public.trg_authorization_policy_mutation()",
        "public.trg_authorization_role_permission_mutation()",
    ):
        op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_support")
        op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} FROM PUBLIC, aurum_app")


def upgrade() -> None:
    _replace_trigger_functions(
        POLICY_MUTATION_TRIGGER_SQL,
        ROLE_PERMISSION_MUTATION_TRIGGER_SQL,
    )


def downgrade() -> None:
    _replace_trigger_functions(
        LEGACY_POLICY_MUTATION_TRIGGER_SQL,
        LEGACY_ROLE_PERMISSION_MUTATION_TRIGGER_SQL,
    )
