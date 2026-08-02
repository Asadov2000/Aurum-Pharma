"""protect active owner assignments from generic role workflows

Revision ID: 0077
Revises: 0076
Create Date: 2026-08-02
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0077"
down_revision: str | Sequence[str] | None = "0076"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _assignment_scope_trigger_sql(*, protect_owner_accounts: bool) -> str:
    owner_guard = (
        """
  -- Ownership changes have their own protected workflow. The generic role
  -- functions may only create the initial tenant-wide protected owner role.
  PERFORM tenant.id
  FROM public.tenant AS tenant
  WHERE tenant.id = NEW.tenant_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Assignment tenant is unavailable'
      USING ERRCODE = '42501';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM public.tenant_ownership AS ownership
    WHERE ownership.tenant_id = NEW.tenant_id
      AND ownership.membership_id = NEW.membership_id
      AND ownership.is_active
  ) AND (
    TG_OP <> 'INSERT'
    OR NOT NEW.is_active
    OR NEW.branch_id IS NOT NULL
    OR NOT v_role_is_protected
    OR v_role_protected_kind IS DISTINCT FROM 'tenant_owner'
    OR EXISTS (
      SELECT 1
      FROM public.user_assignment AS existing_assignment
      JOIN public.role AS existing_role
        ON existing_role.id = existing_assignment.role_id
      WHERE existing_assignment.tenant_id = NEW.tenant_id
        AND existing_assignment.membership_id = NEW.membership_id
        AND existing_assignment.is_active
        AND existing_role.is_protected
        AND existing_role.protected_kind = 'tenant_owner'
    )
  ) THEN
    RAISE EXCEPTION 'Owner assignments require the protected ownership workflow'
      USING ERRCODE = '42501';
  END IF;
"""
        if protect_owner_accounts
        else ""
    )
    return f"""
CREATE OR REPLACE FUNCTION public.trg_guard_user_assignment_scope()
RETURNS TRIGGER AS $$
DECLARE
  v_membership_status TEXT;
  v_role_is_protected BOOLEAN;
  v_role_protected_kind TEXT;
  v_role_tenant_id UUID;
BEGIN
  IF NEW.membership_id IS NULL THEN
    SELECT membership.id
    INTO NEW.membership_id
    FROM public.tenant_membership AS membership
    WHERE membership.tenant_id = NEW.tenant_id
      AND membership.user_id = NEW.user_id;
  END IF;

  SELECT membership.status
  INTO v_membership_status
  FROM public.tenant_membership AS membership
  WHERE membership.id = NEW.membership_id
    AND membership.tenant_id = NEW.tenant_id
    AND membership.user_id = NEW.user_id;

  IF v_membership_status IS NULL
    OR (
      NEW.is_active
      AND v_membership_status NOT IN ('pending', 'active')
    )
  THEN
    RAISE EXCEPTION 'Assignment requires a pending or active tenant membership'
      USING ERRCODE = '42501';
  END IF;

  SELECT
    assigned_role.is_protected,
    assigned_role.protected_kind,
    assigned_role.tenant_id
  INTO
    v_role_is_protected,
    v_role_protected_kind,
    v_role_tenant_id
  FROM public.role AS assigned_role
  WHERE assigned_role.id = NEW.role_id
    AND assigned_role.is_active;

  IF v_role_is_protected IS NULL THEN
    RAISE EXCEPTION 'Assignment role is unavailable'
      USING ERRCODE = '42501';
  END IF;

{owner_guard}
  IF public.is_support_session() AND NOT public.is_tenant_support_session() THEN
    RETURN NEW;
  END IF;

  IF (
      NOT public.is_tenant_support_session()
      AND NOT public.tenant_actor_is_owner(NEW.tenant_id)
    )
    OR NEW.user_id = public.current_app_user_id()
    OR v_role_is_protected
    OR v_role_tenant_id IS DISTINCT FROM NEW.tenant_id
  THEN
    RAISE EXCEPTION 'Assignment target is protected or outside actor scope'
      USING ERRCODE = '42501';
  END IF;

  IF TG_OP = 'UPDATE' THEN
    IF OLD.tenant_id IS DISTINCT FROM NEW.tenant_id
      OR OLD.user_id IS DISTINCT FROM NEW.user_id
      OR OLD.membership_id IS DISTINCT FROM NEW.membership_id
    THEN
      RAISE EXCEPTION 'Assignment identity cannot be changed'
        USING ERRCODE = '42501';
    END IF;

    IF OLD.is_active AND NOT NEW.is_active THEN
      IF NOT public.tenant_actor_has_scoped_permission(
        OLD.tenant_id,
        'roles.assign',
        OLD.branch_id
      ) OR NOT public.tenant_actor_can_delegate_role(
        OLD.tenant_id,
        OLD.role_id,
        OLD.branch_id
      ) THEN
        RAISE EXCEPTION 'Assignment revocation is outside actor delegation scope'
          USING ERRCODE = '42501';
      END IF;
      RETURN NEW;
    END IF;

    IF OLD.is_active AND NEW.is_active AND (
      OLD.branch_id IS DISTINCT FROM NEW.branch_id
      OR OLD.role_id IS DISTINCT FROM NEW.role_id
      OR OLD.password_required IS DISTINCT FROM NEW.password_required
    ) THEN
      IF NOT public.tenant_actor_has_scoped_permission(
        OLD.tenant_id,
        'roles.assign',
        OLD.branch_id
      ) OR NOT public.tenant_actor_has_scoped_permission(
        NEW.tenant_id,
        'roles.assign',
        NEW.branch_id
      ) OR NOT public.tenant_actor_can_delegate_role(
        OLD.tenant_id,
        OLD.role_id,
        OLD.branch_id
      ) OR NOT public.tenant_actor_can_delegate_role(
        NEW.tenant_id,
        NEW.role_id,
        NEW.branch_id
      ) THEN
        RAISE EXCEPTION 'Assignment update is outside actor delegation scope'
          USING ERRCODE = '42501';
      END IF;
      RETURN NEW;
    END IF;

    IF NOT OLD.is_active AND NEW.is_active
      AND (
        NOT public.tenant_actor_has_scoped_permission(
          NEW.tenant_id,
          'roles.assign',
          NEW.branch_id
        )
        OR NOT public.tenant_actor_can_delegate_role(
          NEW.tenant_id,
          NEW.role_id,
          NEW.branch_id
        )
      )
    THEN
      RAISE EXCEPTION 'Assignment reactivation is outside actor delegation scope'
        USING ERRCODE = '42501';
    END IF;

    RETURN NEW;
  END IF;

  IF NOT public.tenant_actor_has_scoped_permission(
    NEW.tenant_id,
    'roles.assign',
    NEW.branch_id
  ) OR NOT public.tenant_actor_can_delegate_role(
    NEW.tenant_id,
    NEW.role_id,
    NEW.branch_id
  ) THEN
    RAISE EXCEPTION 'Assignment creation is outside actor delegation scope'
      USING ERRCODE = '42501';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


def _ownership_guard_trigger_sql(*, protect_owner_accounts: bool) -> str:
    assignment_guard = (
        """
  PERFORM tenant.id
  FROM public.tenant AS tenant
  WHERE tenant.id = NEW.tenant_id
  FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Ownership tenant is unavailable'
      USING ERRCODE = '23514';
  END IF;

  IF TG_OP = 'INSERT' THEN
    v_activating := NEW.is_active;
  ELSE
    v_activating := NEW.is_active AND NOT OLD.is_active;
  END IF;

  IF v_activating AND EXISTS (
    SELECT 1
    FROM public.user_assignment AS assignment
    JOIN public.role AS assigned_role
      ON assigned_role.id = assignment.role_id
    WHERE assignment.tenant_id = NEW.tenant_id
      AND assignment.membership_id = NEW.membership_id
      AND assignment.is_active
      AND NOT (
        assignment.branch_id IS NULL
        AND assigned_role.is_protected
        AND assigned_role.protected_kind = 'tenant_owner'
      )
  ) THEN
    RAISE EXCEPTION 'Ownership activation requires protected owner assignments only'
      USING ERRCODE = '42501';
  END IF;
"""
        if protect_owner_accounts
        else ""
    )
    return f"""
CREATE OR REPLACE FUNCTION public.trg_guard_tenant_ownership()
RETURNS TRIGGER AS $$
DECLARE
  v_activating BOOLEAN := FALSE;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF pg_catalog.pg_trigger_depth() <= 1 THEN
      RAISE EXCEPTION 'Ownership history cannot be deleted directly'
        USING ERRCODE = '42501';
    END IF;
    RETURN OLD;
  END IF;

  IF TG_OP = 'UPDATE' AND (
    OLD.tenant_id IS DISTINCT FROM NEW.tenant_id
    OR OLD.membership_id IS DISTINCT FROM NEW.membership_id
  ) THEN
    RAISE EXCEPTION 'Ownership identity cannot be changed'
      USING ERRCODE = '42501';
  END IF;

{assignment_guard}
  IF NOT EXISTS (
    SELECT 1
    FROM public.tenant_membership AS membership
    WHERE membership.id = NEW.membership_id
      AND membership.tenant_id = NEW.tenant_id
      AND (NOT NEW.is_active OR membership.status = 'active')
  ) THEN
    RAISE EXCEPTION 'Ownership requires an active tenant membership'
      USING ERRCODE = '23514';
  END IF;

  IF TG_OP = 'UPDATE'
    AND OLD.is_active
    AND NOT NEW.is_active
  THEN
    PERFORM tenant.id
    FROM public.tenant AS tenant
    WHERE tenant.id = OLD.tenant_id
    FOR UPDATE;

    IF NOT EXISTS (
      SELECT 1
      FROM public.tenant_ownership AS other_ownership
      JOIN public.tenant_membership AS other_membership
        ON other_membership.id = other_ownership.membership_id
       AND other_membership.tenant_id = other_ownership.tenant_id
       AND other_membership.status = 'active'
      WHERE other_ownership.tenant_id = OLD.tenant_id
        AND other_ownership.is_active
        AND other_ownership.id <> OLD.id
    )
    THEN
      RAISE EXCEPTION 'The last active owner cannot be revoked'
        USING ERRCODE = '23514';
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


def upgrade() -> None:
    op.execute(_assignment_scope_trigger_sql(protect_owner_accounts=True))
    op.execute(_ownership_guard_trigger_sql(protect_owner_accounts=True))


def downgrade() -> None:
    op.execute(_ownership_guard_trigger_sql(protect_owner_accounts=False))
    op.execute(_assignment_scope_trigger_sql(protect_owner_accounts=False))
