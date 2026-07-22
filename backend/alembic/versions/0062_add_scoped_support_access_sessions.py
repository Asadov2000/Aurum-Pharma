"""authorization: add scoped support access sessions

Revision ID: 0062
Revises: 0061
Create Date: 2026-07-22

Developer and administrator tenant access is now represented by a short-lived,
tenant-bound session. The support DB pool remains an implementation detail: a
tenant support request is privileged only while the session is active and only
for capabilities explicitly attached to it.
"""

from __future__ import annotations

from collections.abc import Sequence
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0062"
down_revision: str | Sequence[str] | None = "0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SUPPORT_ROLE_CAPABILITIES = (
    "roles.assign",
    "roles.create",
    "roles.update",
    "users.view",
)


IS_SUPPORT_SESSION_SQL = """
CREATE OR REPLACE FUNCTION public.is_support_session()
RETURNS BOOLEAN AS $$
  SELECT session_user = 'aurum_support'
    AND COALESCE(pg_catalog.current_setting('app.support_session', true), '') = 'true'
    AND NULLIF(
      pg_catalog.current_setting('app.support_access_session_id', true),
      ''
    ) IS NULL
$$ LANGUAGE SQL
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
"""


IS_TENANT_SUPPORT_SESSION_SQL = """
CREATE OR REPLACE FUNCTION public.is_tenant_support_session()
RETURNS BOOLEAN AS $$
  SELECT session_user IN ('aurum_app', 'aurum_support')
    AND COALESCE(pg_catalog.current_setting('app.support_session', true), '') = 'true'
    AND NULLIF(
      pg_catalog.current_setting('app.support_access_session_id', true),
      ''
    ) IS NOT NULL
    AND EXISTS (
      SELECT 1
      FROM public.support_access_session AS access_session
      WHERE access_session.id = NULLIF(
          pg_catalog.current_setting('app.support_access_session_id', true),
          ''
        )::UUID
        AND access_session.actor_user_id = NULLIF(
          pg_catalog.current_setting('app.user_id', true),
          ''
        )::UUID
        AND access_session.tenant_id = NULLIF(
          pg_catalog.current_setting('app.tenant_id', true),
          ''
        )::UUID
        AND access_session.revoked_at IS NULL
        AND access_session.expires_at > pg_catalog.statement_timestamp()
    )
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


CURRENT_TENANT_ID_SQL = """
CREATE OR REPLACE FUNCTION public.current_tenant_id()
RETURNS UUID AS $$
DECLARE
  v_tenant_id TEXT;
  v_support_access_session_id TEXT;
BEGIN
  v_tenant_id := pg_catalog.current_setting('app.tenant_id', true);
  IF v_tenant_id IS NULL OR v_tenant_id = '' THEN
    RETURN NULL;
  END IF;

  v_support_access_session_id := pg_catalog.current_setting(
    'app.support_access_session_id', true
  );
  IF v_support_access_session_id IS NOT NULL
    AND v_support_access_session_id <> ''
    AND NOT public.is_tenant_support_session()
  THEN
    RETURN NULL;
  END IF;

  RETURN v_tenant_id::UUID;
EXCEPTION WHEN OTHERS THEN
  RETURN NULL;
END;
$$ LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
"""


SUPPORT_HAS_CAPABILITY_SQL = """
CREATE OR REPLACE FUNCTION public.support_access_has_capability(
  p_permission_code TEXT
) RETURNS BOOLEAN AS $$
  SELECT public.is_support_session()
    OR (
      public.is_tenant_support_session()
      AND EXISTS (
        SELECT 1
        FROM public.support_access_capability AS capability
        WHERE capability.support_access_session_id = NULLIF(
            pg_catalog.current_setting('app.support_access_session_id', true),
            ''
          )::UUID
          AND capability.tenant_id = public.current_tenant_id()
          AND capability.permission_code = p_permission_code
      )
    )
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


SUPPORT_CAN_DELEGATE_SQL = """
CREATE OR REPLACE FUNCTION public.support_actor_can_delegate_permission(
  p_permission_code TEXT
) RETURNS BOOLEAN AS $$
  SELECT public.is_support_session()
    OR (
      public.is_tenant_support_session()
      AND EXISTS (
        SELECT 1
        FROM public.app_user AS actor
        JOIN public.permission AS permission
          ON permission.code = p_permission_code
         AND permission.is_active
         AND permission.target_role_type = 'tenant'
        WHERE actor.id = public.current_app_user_id()
          AND actor.status = 'active'
          AND (
            (actor.is_developer AND permission.developer_delegable)
            OR (
              actor.is_administrator
              AND permission.administrator_delegable
            )
          )
      )
    )
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ACTIVE_PERMISSION_GATE_SQL = """
CREATE OR REPLACE FUNCTION public.tenant_actor_has_permission(
  p_tenant_id UUID,
  p_permission_code TEXT
) RETURNS BOOLEAN AS $$
  SELECT (
      (public.is_support_session() OR public.is_tenant_support_session())
      AND public.support_access_has_capability(p_permission_code)
    )
    OR (
      p_tenant_id IS NOT NULL
      AND public.current_app_user_id() IS NOT NULL
      AND p_tenant_id IS NOT DISTINCT FROM public.current_tenant_id()
      AND EXISTS (
        SELECT 1
        FROM public.tenant_membership AS membership
        JOIN public.user_assignment AS assignment
          ON assignment.membership_id = membership.id
         AND assignment.tenant_id = membership.tenant_id
         AND assignment.user_id = membership.user_id
         AND assignment.is_active
        JOIN public.role AS assigned_role
          ON assigned_role.id = assignment.role_id
         AND assigned_role.is_active
        JOIN public.role_permission AS role_permission
          ON role_permission.role_id = assigned_role.id
        JOIN public.permission AS granted_permission
          ON granted_permission.code = role_permission.permission_code
         AND granted_permission.is_active
        WHERE membership.tenant_id = p_tenant_id
          AND membership.user_id = public.current_app_user_id()
          AND membership.status = 'active'
          AND assignment.branch_id IS NULL
          AND granted_permission.code = p_permission_code
      )
    )
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


SCOPED_PERMISSION_SQL = """
CREATE OR REPLACE FUNCTION public.tenant_actor_has_scoped_permission(
  p_tenant_id UUID,
  p_permission_code TEXT,
  p_branch_id UUID
) RETURNS BOOLEAN AS $$
  SELECT (
      (public.is_support_session() OR public.is_tenant_support_session())
      AND public.support_access_has_capability(p_permission_code)
    )
    OR (
      p_tenant_id IS NOT NULL
      AND public.current_app_user_id() IS NOT NULL
      AND p_tenant_id IS NOT DISTINCT FROM public.current_tenant_id()
      AND EXISTS (
        SELECT 1
        FROM public.tenant_membership AS membership
        JOIN public.user_assignment AS assignment
          ON assignment.membership_id = membership.id
         AND assignment.tenant_id = membership.tenant_id
         AND assignment.user_id = membership.user_id
         AND assignment.is_active
        JOIN public.role AS assigned_role
          ON assigned_role.id = assignment.role_id
         AND assigned_role.is_active
        JOIN public.role_permission AS role_permission
          ON role_permission.role_id = assigned_role.id
        JOIN public.permission AS granted_permission
          ON granted_permission.code = role_permission.permission_code
         AND granted_permission.is_active
        WHERE membership.tenant_id = p_tenant_id
          AND membership.user_id = public.current_app_user_id()
          AND membership.status = 'active'
          AND granted_permission.code = p_permission_code
          AND (
            granted_permission.scope_type IN ('BRANCH_SET', 'OWN')
            OR assignment.branch_id IS NULL
          )
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


ROLE_DELEGATION_GATE_SQL = """
CREATE OR REPLACE FUNCTION public.tenant_actor_can_delegate_role(
  p_tenant_id UUID,
  p_role_id UUID,
  p_branch_id UUID
) RETURNS BOOLEAN AS $$
  SELECT (
      public.is_support_session()
      AND NOT public.is_tenant_support_session()
    )
    OR (
      public.is_tenant_support_session()
      AND p_tenant_id IS NOT DISTINCT FROM public.current_tenant_id()
      AND public.support_access_has_capability('roles.assign')
      AND EXISTS (
        SELECT 1
        FROM public.role AS delegated_role
        WHERE delegated_role.id = p_role_id
          AND delegated_role.tenant_id = p_tenant_id
          AND delegated_role.is_active
          AND NOT delegated_role.is_system
          AND NOT delegated_role.is_protected
      )
      AND NOT EXISTS (
        SELECT 1
        FROM public.role_permission AS role_permission
        WHERE role_permission.role_id = p_role_id
          AND NOT public.support_actor_can_delegate_permission(
            role_permission.permission_code
          )
      )
    )
    OR (
      NOT public.is_support_session()
      AND public.tenant_actor_is_owner(p_tenant_id)
      AND EXISTS (
        SELECT 1
        FROM public.role AS delegated_role
        WHERE delegated_role.id = p_role_id
          AND delegated_role.tenant_id = p_tenant_id
          AND delegated_role.is_active
          AND NOT delegated_role.is_system
          AND NOT delegated_role.is_protected
      )
      AND NOT EXISTS (
        SELECT 1
        FROM public.role_permission AS role_permission
        JOIN public.permission AS delegated_permission
          ON delegated_permission.code = role_permission.permission_code
         AND delegated_permission.is_active
        WHERE role_permission.role_id = p_role_id
          AND (
            delegated_permission.target_role_type <> 'tenant'
            OR NOT delegated_permission.owner_delegable
            OR NOT public.tenant_actor_has_scoped_permission(
              p_tenant_id,
              delegated_permission.code,
              p_branch_id
            )
          )
      )
    )
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ASSIGNMENT_SCOPE_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION public.trg_guard_user_assignment_scope()
RETURNS TRIGGER AS $$
DECLARE
  v_membership_status TEXT;
  v_role_is_protected BOOLEAN;
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
    OR (NEW.is_active AND v_membership_status NOT IN ('pending', 'active'))
  THEN
    RAISE EXCEPTION 'Assignment requires a pending or active tenant membership'
      USING ERRCODE = '42501';
  END IF;

  SELECT assigned_role.is_protected, assigned_role.tenant_id
  INTO v_role_is_protected, v_role_tenant_id
  FROM public.role AS assigned_role
  WHERE assigned_role.id = NEW.role_id
    AND assigned_role.is_active;

  IF v_role_is_protected IS NULL THEN
    RAISE EXCEPTION 'Assignment role is unavailable'
      USING ERRCODE = '42501';
  END IF;

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
        OLD.tenant_id, 'roles.assign', OLD.branch_id
      ) OR NOT public.tenant_actor_can_delegate_role(
        OLD.tenant_id, OLD.role_id, OLD.branch_id
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
        OLD.tenant_id, 'roles.assign', OLD.branch_id
      ) OR NOT public.tenant_actor_has_scoped_permission(
        NEW.tenant_id, 'roles.assign', NEW.branch_id
      ) OR NOT public.tenant_actor_can_delegate_role(
        OLD.tenant_id, OLD.role_id, OLD.branch_id
      ) OR NOT public.tenant_actor_can_delegate_role(
        NEW.tenant_id, NEW.role_id, NEW.branch_id
      ) THEN
        RAISE EXCEPTION 'Assignment update is outside actor delegation scope'
          USING ERRCODE = '42501';
      END IF;
      RETURN NEW;
    END IF;

    IF NOT OLD.is_active AND NEW.is_active AND (
      NOT public.tenant_actor_has_scoped_permission(
        NEW.tenant_id, 'roles.assign', NEW.branch_id
      ) OR NOT public.tenant_actor_can_delegate_role(
        NEW.tenant_id, NEW.role_id, NEW.branch_id
      )
    ) THEN
      RAISE EXCEPTION 'Assignment reactivation is outside actor delegation scope'
        USING ERRCODE = '42501';
    END IF;

    RETURN NEW;
  END IF;

  IF NOT public.tenant_actor_has_scoped_permission(
    NEW.tenant_id, 'roles.assign', NEW.branch_id
  ) OR NOT public.tenant_actor_can_delegate_role(
    NEW.tenant_id, NEW.role_id, NEW.branch_id
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


ROLE_MUTATION_GUARD_SQL = """
CREATE OR REPLACE FUNCTION public.trg_guard_tenant_role_mutation()
RETURNS TRIGGER AS $$
DECLARE
  v_role_id UUID;
  v_tenant_id UUID;
BEGIN
  IF (public.is_support_session() AND NOT public.is_tenant_support_session())
    OR session_user NOT IN ('aurum_app', 'aurum_support')
  THEN
    IF TG_OP = 'DELETE' THEN
      RETURN OLD;
    END IF;
    RETURN NEW;
  END IF;

  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Role deletion requires a protected support workflow'
      USING ERRCODE = '42501';
  END IF;

  v_role_id := NEW.id;
  v_tenant_id := NEW.tenant_id;

  IF v_tenant_id IS NULL
    OR NEW.is_system
    OR NEW.is_protected
    OR v_tenant_id IS DISTINCT FROM public.current_tenant_id()
    OR (
      NOT public.is_tenant_support_session()
      AND NOT public.tenant_actor_is_owner(v_tenant_id)
    )
  THEN
    RAISE EXCEPTION 'Role mutation is outside actor scope'
      USING ERRCODE = '42501';
  END IF;

  IF TG_OP = 'INSERT' THEN
    IF NOT public.tenant_actor_has_permission(v_tenant_id, 'roles.create') THEN
      RAISE EXCEPTION 'Role creation capability is required'
        USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
  END IF;

  IF OLD.id IS DISTINCT FROM NEW.id
    OR OLD.tenant_id IS DISTINCT FROM NEW.tenant_id
    OR OLD.is_system IS DISTINCT FROM NEW.is_system
    OR OLD.is_protected IS DISTINCT FROM NEW.is_protected
    OR OLD.protected_kind IS DISTINCT FROM NEW.protected_kind
    OR OLD.level IS DISTINCT FROM NEW.level
    OR NEW.version <> OLD.version + 1
  THEN
    RAISE EXCEPTION 'Role identity or version transition is invalid'
      USING ERRCODE = '42501';
  END IF;

  IF NOT public.tenant_actor_has_permission(v_tenant_id, 'roles.update')
    OR EXISTS (
      SELECT 1
      FROM public.user_assignment AS assignment
      WHERE assignment.tenant_id = v_tenant_id
        AND assignment.user_id = public.current_app_user_id()
        AND assignment.role_id = v_role_id
        AND assignment.is_active
    )
    OR EXISTS (
      SELECT 1
      FROM public.role_permission AS role_permission
      JOIN public.permission AS permission
        ON permission.code = role_permission.permission_code
      WHERE role_permission.role_id = v_role_id
        AND (
          (
            public.is_tenant_support_session()
            AND NOT public.support_actor_can_delegate_permission(permission.code)
          )
          OR (
            NOT public.is_tenant_support_session()
            AND (
              NOT permission.is_active
              OR permission.target_role_type <> 'tenant'
              OR NOT permission.owner_delegable
              OR NOT public.tenant_actor_has_permission(
                v_tenant_id, permission.code
              )
            )
          )
        )
    )
  THEN
    RAISE EXCEPTION 'Role update is outside actor delegation scope'
      USING ERRCODE = '42501';
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ROLE_PERMISSION_MUTATION_GUARD_SQL = """
CREATE OR REPLACE FUNCTION public.trg_guard_role_permission_mutation()
RETURNS TRIGGER AS $$
DECLARE
  v_permission_active BOOLEAN;
  v_permission_owner_delegable BOOLEAN;
  v_permission_target_role_type TEXT;
  v_role_id UUID;
  v_role_is_protected BOOLEAN;
  v_role_is_system BOOLEAN;
  v_role_tenant_id UUID;
BEGIN
  IF (public.is_support_session() AND NOT public.is_tenant_support_session())
    OR session_user NOT IN ('aurum_app', 'aurum_support')
  THEN
    IF TG_OP = 'DELETE' THEN
      RETURN OLD;
    END IF;
    RETURN NEW;
  END IF;

  IF TG_OP = 'UPDATE' AND (
    OLD.role_id IS DISTINCT FROM NEW.role_id
    OR OLD.permission_code IS DISTINCT FROM NEW.permission_code
  ) THEN
    RAISE EXCEPTION 'Role permission identity cannot be changed'
      USING ERRCODE = '42501';
  END IF;

  v_role_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.role_id ELSE NEW.role_id END;

  SELECT role.tenant_id, role.is_system, role.is_protected
  INTO v_role_tenant_id, v_role_is_system, v_role_is_protected
  FROM public.role AS role
  WHERE role.id = v_role_id
    AND role.is_active;

  IF v_role_tenant_id IS NULL
    OR v_role_is_system
    OR v_role_is_protected
    OR v_role_tenant_id IS DISTINCT FROM public.current_tenant_id()
    OR (
      NOT public.is_tenant_support_session()
      AND NOT public.tenant_actor_is_owner(v_role_tenant_id)
    )
    OR NOT public.tenant_actor_has_permission(v_role_tenant_id, 'roles.update')
    OR EXISTS (
      SELECT 1
      FROM public.user_assignment AS assignment
      WHERE assignment.tenant_id = v_role_tenant_id
        AND assignment.user_id = public.current_app_user_id()
        AND assignment.role_id = v_role_id
        AND assignment.is_active
    )
  THEN
    RAISE EXCEPTION 'Role permission mutation is outside actor scope'
      USING ERRCODE = '42501';
  END IF;

  SELECT permission.is_active, permission.owner_delegable, permission.target_role_type
  INTO v_permission_active, v_permission_owner_delegable, v_permission_target_role_type
  FROM public.permission AS permission
  WHERE permission.code = CASE
    WHEN TG_OP = 'DELETE' THEN OLD.permission_code
    ELSE NEW.permission_code
  END;

  IF public.is_tenant_support_session() THEN
    IF NOT public.support_actor_can_delegate_permission(
      CASE WHEN TG_OP = 'DELETE' THEN OLD.permission_code ELSE NEW.permission_code END
    ) THEN
      RAISE EXCEPTION 'Permission is outside support delegation scope'
        USING ERRCODE = '42501';
    END IF;
    IF TG_OP = 'DELETE' THEN
      RETURN OLD;
    END IF;
    RETURN NEW;
  END IF;

  IF TG_OP = 'DELETE' AND (
    v_permission_active IS NOT TRUE
    OR v_permission_owner_delegable IS NOT TRUE
    OR v_permission_target_role_type IS DISTINCT FROM 'tenant'
  ) THEN
    RETURN OLD;
  END IF;

  IF v_permission_active IS NOT TRUE
    OR v_permission_owner_delegable IS NOT TRUE
    OR v_permission_target_role_type IS DISTINCT FROM 'tenant'
    OR NOT public.tenant_actor_has_permission(
      v_role_tenant_id,
      CASE WHEN TG_OP = 'DELETE' THEN OLD.permission_code ELSE NEW.permission_code END
    )
  THEN
    RAISE EXCEPTION 'Permission is outside actor delegation scope'
      USING ERRCODE = '42501';
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


LEGACY_IS_SUPPORT_SESSION_SQL = """
CREATE OR REPLACE FUNCTION public.is_support_session()
RETURNS BOOLEAN AS $$
  SELECT session_user = 'aurum_support'
    AND COALESCE(current_setting('app.support_session', true), '') = 'true'
$$ LANGUAGE SQL
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
"""


LEGACY_CURRENT_TENANT_ID_SQL = """
CREATE OR REPLACE FUNCTION public.current_tenant_id()
RETURNS UUID AS $$
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
$$ LANGUAGE plpgsql
STABLE
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
"""


def _load_revision(name: str) -> ModuleType:
    path = Path(__file__).with_name(name)
    spec = spec_from_file_location(f"aurum_migration_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load migration source {name}")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sql(module: ModuleType, name: str) -> str:
    value = getattr(module, name, None)
    if not isinstance(value, str):
        raise RuntimeError(f"Migration does not expose SQL constant {name}")
    return value


def _as_create_or_replace(statement: str) -> str:
    if statement.lstrip().startswith("CREATE OR REPLACE FUNCTION"):
        return statement
    return statement.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


def _secure_function(signature: str, *, app_access: bool = False) -> None:
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_support")
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} FROM PUBLIC, aurum_app")
    grantees = "aurum_support, aurum_app" if app_access else "aurum_support"
    op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {grantees}")


def _create_tables() -> None:
    op.execute("""
        CREATE TABLE public.support_access_session (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id UUID NOT NULL REFERENCES public.tenant(id) ON DELETE CASCADE,
          actor_user_id UUID NOT NULL REFERENCES public.app_user(id) ON DELETE RESTRICT,
          reason TEXT NOT NULL,
          is_read_only BOOLEAN NOT NULL DEFAULT true,
          started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          expires_at TIMESTAMPTZ NOT NULL,
          revoked_at TIMESTAMPTZ,
          revoked_by_user_id UUID REFERENCES public.app_user(id) ON DELETE RESTRICT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by UUID,
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_by UUID,
          CONSTRAINT uq_support_access_session_id_tenant UNIQUE (id, tenant_id),
          CONSTRAINT ck_support_access_reason CHECK (
            char_length(btrim(reason)) BETWEEN 10 AND 500
          ),
          CONSTRAINT ck_support_access_expiry CHECK (
            expires_at > started_at
            AND expires_at <= started_at + INTERVAL '30 minutes'
          ),
          CONSTRAINT ck_support_access_revocation CHECK (
            (revoked_at IS NULL AND revoked_by_user_id IS NULL)
            OR (revoked_at IS NOT NULL AND revoked_by_user_id IS NOT NULL)
          )
        )
        """)
    op.execute("""
        CREATE TABLE public.support_access_capability (
          support_access_session_id UUID NOT NULL,
          tenant_id UUID NOT NULL,
          permission_code TEXT NOT NULL REFERENCES public.permission(code) ON DELETE RESTRICT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by UUID,
          PRIMARY KEY (support_access_session_id, permission_code),
          CONSTRAINT fk_support_access_capability_session
            FOREIGN KEY (support_access_session_id, tenant_id)
            REFERENCES public.support_access_session(id, tenant_id)
            ON DELETE CASCADE
        )
        """)
    op.execute("""
        CREATE INDEX ix_support_access_session_actor_active
        ON public.support_access_session(actor_user_id, expires_at)
        WHERE revoked_at IS NULL
        """)
    op.execute("""
        CREATE INDEX ix_support_access_session_tenant_started
        ON public.support_access_session(tenant_id, started_at DESC)
        """)

    for table in ("support_access_session", "support_access_capability"):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{table} FROM PUBLIC, aurum_app")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.{table} TO aurum_support")

    op.execute("""
        CREATE POLICY support_access_session_actor_scope
        ON public.support_access_session
        USING (
          tenant_id = public.current_tenant_id()
          AND actor_user_id = public.current_app_user_id()
        )
        WITH CHECK (
          tenant_id = public.current_tenant_id()
          AND actor_user_id = public.current_app_user_id()
        )
        """)
    op.execute("""
        CREATE POLICY support_access_capability_actor_scope
        ON public.support_access_capability
        USING (
          tenant_id = public.current_tenant_id()
          AND EXISTS (
            SELECT 1
            FROM public.support_access_session AS access_session
            WHERE access_session.id = support_access_session_id
              AND access_session.tenant_id = support_access_capability.tenant_id
              AND access_session.actor_user_id = public.current_app_user_id()
          )
        )
        WITH CHECK (
          tenant_id = public.current_tenant_id()
          AND EXISTS (
            SELECT 1
            FROM public.support_access_session AS access_session
            WHERE access_session.id = support_access_session_id
              AND access_session.tenant_id = support_access_capability.tenant_id
              AND access_session.actor_user_id = public.current_app_user_id()
          )
        )
        """)


def _install_functions() -> None:
    definitions = (
        (IS_SUPPORT_SESSION_SQL, "public.is_support_session()", True),
        (IS_TENANT_SUPPORT_SESSION_SQL, "public.is_tenant_support_session()", True),
        (CURRENT_TENANT_ID_SQL, "public.current_tenant_id()", True),
        (
            SUPPORT_HAS_CAPABILITY_SQL,
            "public.support_access_has_capability(TEXT)",
            False,
        ),
        (
            SUPPORT_CAN_DELEGATE_SQL,
            "public.support_actor_can_delegate_permission(TEXT)",
            False,
        ),
        (
            ACTIVE_PERMISSION_GATE_SQL,
            "public.tenant_actor_has_permission(UUID, TEXT)",
            False,
        ),
        (
            SCOPED_PERMISSION_SQL,
            "public.tenant_actor_has_scoped_permission(UUID, TEXT, UUID)",
            True,
        ),
        (
            ROLE_DELEGATION_GATE_SQL,
            "public.tenant_actor_can_delegate_role(UUID, UUID, UUID)",
            False,
        ),
        (
            ASSIGNMENT_SCOPE_TRIGGER_SQL,
            "public.trg_guard_user_assignment_scope()",
            False,
        ),
        (
            ROLE_MUTATION_GUARD_SQL,
            "public.trg_guard_tenant_role_mutation()",
            False,
        ),
        (
            ROLE_PERMISSION_MUTATION_GUARD_SQL,
            "public.trg_guard_role_permission_mutation()",
            False,
        ),
    )
    for statement, signature, app_access in definitions:
        op.execute(statement)
        _secure_function(signature, app_access=app_access)


def _set_support_grantability(*, enabled: bool) -> None:
    codes = ", ".join(f"'{code}'" for code in SUPPORT_ROLE_CAPABILITIES)
    value = "true" if enabled else "false"
    op.execute(f"""
        UPDATE public.permission
        SET
          developer_grantable = {value},
          administrator_grantable = {value}
        WHERE code IN ({codes})
        """)


def _restore_pre_support_grantability() -> None:
    op.execute("""
        UPDATE public.permission
        SET
          developer_grantable = false,
          administrator_grantable = false
        WHERE code IN ('roles.assign', 'roles.create', 'roles.update')
        """)
    op.execute("""
        UPDATE public.permission
        SET
          developer_grantable = true,
          administrator_grantable = true
        WHERE code = 'users.view'
        """)


def _set_scoped_role_policies(*, enabled: bool) -> None:
    support_gate = " OR public.is_tenant_support_session()" if enabled else ""
    op.execute("DROP POLICY role_write ON public.role")
    op.execute(f"""
        CREATE POLICY role_write ON public.role
          FOR ALL
          USING (
            NOT is_system
            AND NOT is_protected
            AND tenant_id = public.current_tenant_id()
            AND (
              public.tenant_actor_is_owner(tenant_id){support_gate}
            )
          )
          WITH CHECK (
            NOT is_system
            AND NOT is_protected
            AND tenant_id = public.current_tenant_id()
            AND (
              public.tenant_actor_is_owner(tenant_id){support_gate}
            )
          )
        """)
    op.execute("DROP POLICY role_permission_write ON public.role_permission")
    op.execute(f"""
        CREATE POLICY role_permission_write ON public.role_permission
          FOR ALL
          USING (
            EXISTS (
              SELECT 1
              FROM public.role AS scoped_role
              WHERE scoped_role.id = role_permission.role_id
                AND scoped_role.tenant_id = public.current_tenant_id()
                AND NOT scoped_role.is_system
                AND NOT scoped_role.is_protected
                AND (
                  public.tenant_actor_is_owner(scoped_role.tenant_id)
                  {support_gate}
                )
            )
          )
          WITH CHECK (
            EXISTS (
              SELECT 1
              FROM public.role AS scoped_role
              WHERE scoped_role.id = role_permission.role_id
                AND scoped_role.tenant_id = public.current_tenant_id()
                AND NOT scoped_role.is_system
                AND NOT scoped_role.is_protected
                AND (
                  public.tenant_actor_is_owner(scoped_role.tenant_id)
                  {support_gate}
                )
            )
          )
        """)


def upgrade() -> None:
    _create_tables()
    _set_support_grantability(enabled=True)
    _install_functions()
    _set_scoped_role_policies(enabled=True)


def downgrade() -> None:
    source_0053 = _load_revision("0053_add_scoped_delegated_authorization.py")
    source_0055 = _load_revision("0055_protect_role_governance_and_membership_history.py")

    role_permission_guard = source_0055._without_full_catalog_guard(
        _sql(source_0053, "ROLE_PERMISSION_MUTATION_GUARD_SQL")
    )
    legacy_definitions = (
        (LEGACY_IS_SUPPORT_SESSION_SQL, "public.is_support_session()", True),
        (LEGACY_CURRENT_TENANT_ID_SQL, "public.current_tenant_id()", True),
        (
            _sql(source_0053, "ACTIVE_PERMISSION_GATE_SQL"),
            "public.tenant_actor_has_permission(UUID, TEXT)",
            False,
        ),
        (
            _sql(source_0053, "SCOPED_PERMISSION_SQL"),
            "public.tenant_actor_has_scoped_permission(UUID, TEXT, UUID)",
            True,
        ),
        (
            _sql(source_0053, "ROLE_DELEGATION_GATE_SQL"),
            "public.tenant_actor_can_delegate_role(UUID, UUID, UUID)",
            False,
        ),
        (
            _sql(source_0053, "ASSIGNMENT_SCOPE_TRIGGER_SQL"),
            "public.trg_guard_user_assignment_scope()",
            False,
        ),
        (
            _sql(source_0053, "ROLE_MUTATION_GUARD_SQL"),
            "public.trg_guard_tenant_role_mutation()",
            False,
        ),
        (
            role_permission_guard,
            "public.trg_guard_role_permission_mutation()",
            False,
        ),
    )
    for statement, signature, app_access in legacy_definitions:
        op.execute(_as_create_or_replace(statement))
        _secure_function(signature, app_access=app_access)

    _set_scoped_role_policies(enabled=False)
    for signature in (
        "public.support_actor_can_delegate_permission(TEXT)",
        "public.support_access_has_capability(TEXT)",
        "public.is_tenant_support_session()",
    ):
        op.execute(f"DROP FUNCTION {signature}")

    _restore_pre_support_grantability()
    op.execute("DROP TABLE public.support_access_capability")
    op.execute("DROP TABLE public.support_access_session")
