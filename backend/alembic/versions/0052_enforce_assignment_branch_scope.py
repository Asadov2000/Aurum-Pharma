"""security: enforce assignment permission branch scope

Revision ID: 0052
Revises: 0051

Assignment writes already checked tenant membership, permission presence, and
role level. This revision also binds that permission to the target branch.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0052"
down_revision: str | Sequence[str] | None = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SCOPED_PERMISSION_SQL = """
CREATE FUNCTION public.tenant_actor_has_scoped_permission(
  p_tenant_id UUID,
  p_permission_code TEXT,
  p_branch_id UUID
) RETURNS BOOLEAN AS $$
  SELECT session_user = 'aurum_support'
    OR (
      p_tenant_id IS NOT NULL
      AND public.current_app_user_id() IS NOT NULL
      AND p_tenant_id IS NOT DISTINCT FROM public.current_tenant_id()
      AND EXISTS (
        SELECT 1
        FROM public.user_assignment AS assignment
        JOIN public.role AS assigned_role
          ON assigned_role.id = assignment.role_id
         AND assigned_role.is_active
        JOIN public.role_permission AS role_permission
          ON role_permission.role_id = assigned_role.id
        JOIN public.permission AS granted_permission
          ON granted_permission.code = role_permission.permission_code
         AND granted_permission.is_active
        WHERE assignment.tenant_id = p_tenant_id
          AND assignment.user_id = public.current_app_user_id()
          AND assignment.is_active
          AND granted_permission.code = p_permission_code
          AND (
            assignment.branch_id IS NULL
            OR assignment.branch_id = p_branch_id
          )
      )
    )
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ASSIGNMENT_SCOPE_TRIGGER_SQL = """
CREATE FUNCTION public.trg_guard_user_assignment_scope()
RETURNS TRIGGER AS $$
BEGIN
  IF session_user = 'aurum_support' THEN
    RETURN NEW;
  END IF;

  IF TG_OP = 'UPDATE' THEN
    IF OLD.tenant_id IS DISTINCT FROM NEW.tenant_id
      OR OLD.user_id IS DISTINCT FROM NEW.user_id
    THEN
      RAISE EXCEPTION 'Assignment identity cannot be changed'
        USING ERRCODE = '42501';
    END IF;

    IF OLD.is_active AND NOT NEW.is_active THEN
      IF NOT public.tenant_actor_has_scoped_permission(
        OLD.tenant_id,
        'roles.assign',
        OLD.branch_id
      ) THEN
        RAISE EXCEPTION 'Assignment revocation is outside actor branch scope'
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
      ) THEN
        RAISE EXCEPTION 'Assignment update is outside actor branch scope'
          USING ERRCODE = '42501';
      END IF;
      RETURN NEW;
    END IF;

    IF NOT OLD.is_active AND NEW.is_active THEN
      IF NOT (
        public.tenant_actor_has_scoped_permission(
          NEW.tenant_id,
          'users.invite',
          NEW.branch_id
        )
        OR public.tenant_actor_has_scoped_permission(
          NEW.tenant_id,
          'roles.assign',
          NEW.branch_id
        )
      ) THEN
        RAISE EXCEPTION 'Assignment reactivation is outside actor branch scope'
          USING ERRCODE = '42501';
      END IF;
    END IF;

    RETURN NEW;
  END IF;

  IF NOT (
    public.tenant_actor_has_scoped_permission(
      NEW.tenant_id,
      'users.invite',
      NEW.branch_id
    )
    OR public.tenant_actor_has_scoped_permission(
      NEW.tenant_id,
      'roles.assign',
      NEW.branch_id
    )
  ) THEN
    RAISE EXCEPTION 'Assignment creation is outside actor branch scope'
      USING ERRCODE = '42501';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


def upgrade() -> None:
    op.execute(SCOPED_PERMISSION_SQL)
    op.execute(ASSIGNMENT_SCOPE_TRIGGER_SQL)

    scoped_permission = "public.tenant_actor_has_scoped_permission(UUID, TEXT, UUID)"
    op.execute(f"ALTER FUNCTION {scoped_permission} OWNER TO aurum_support")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {scoped_permission} FROM PUBLIC, aurum_app"
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION {scoped_permission} TO aurum_support, aurum_app")

    trigger_function = "public.trg_guard_user_assignment_scope()"
    op.execute(f"ALTER FUNCTION {trigger_function} OWNER TO aurum_support")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {trigger_function} FROM PUBLIC, aurum_app"
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION {trigger_function} TO aurum_support")

    op.execute("DROP TRIGGER IF EXISTS trg_guard_user_assignment_scope ON public.user_assignment")
    op.execute("""
        CREATE TRIGGER trg_guard_user_assignment_scope
        BEFORE INSERT OR UPDATE ON public.user_assignment
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_user_assignment_scope()
        """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_guard_user_assignment_scope ON public.user_assignment")
    op.execute("DROP FUNCTION public.trg_guard_user_assignment_scope()")
    op.execute("DROP FUNCTION public.tenant_actor_has_scoped_permission(UUID, TEXT, UUID)")
