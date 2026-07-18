"""authorization: add tenant memberships, ownership, and delegation metadata

Revision ID: 0053
Revises: 0052

This is the additive backend phase of ADR-0007. Legacy account, role level, and
assignment columns remain available while tenant lifecycle and delegation move
to explicit server-owned records.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0053"
down_revision: str | Sequence[str] | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


AUDIT_ACTIONS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "VIEW",
    "EXPORT",
    "IMPERSONATE",
    "MEMBERSHIP_CREATED",
    "MEMBERSHIP_UPDATED",
    "MEMBERSHIP_ACTIVATED",
    "MEMBERSHIP_SUSPENDED",
    "MEMBERSHIP_OFFBOARDED",
    "OWNERSHIP_GRANTED",
    "OWNERSHIP_REVOKED",
    "ROLE_PERMISSIONS_CHANGED",
)


ACTIVE_PERMISSION_GATE_SQL = """
CREATE OR REPLACE FUNCTION public.tenant_actor_has_permission(
  p_tenant_id UUID,
  p_permission_code TEXT
) RETURNS BOOLEAN AS $$
  SELECT public.is_support_session()
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


LEGACY_ACTIVE_PERMISSION_GATE_SQL = """
CREATE OR REPLACE FUNCTION public.tenant_actor_has_permission(
  p_tenant_id UUID,
  p_permission_code TEXT
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
  SELECT public.is_support_session()
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


LEGACY_SCOPED_PERMISSION_SQL = """
CREATE OR REPLACE FUNCTION public.tenant_actor_has_scoped_permission(
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


ACTIVE_LOOKUP_LOGIN_USER_BY_EMAIL_SQL = """
CREATE FUNCTION public.lookup_login_user_by_email(
  p_email TEXT,
  p_code_id UUID,
  p_candidate_hash TEXT
) RETURNS TABLE(
  id UUID,
  email TEXT,
  full_name TEXT,
  password_hash TEXT,
  is_developer BOOLEAN,
  is_administrator BOOLEAN,
  home_tenant_id UUID,
  status TEXT,
  last_login_at TIMESTAMPTZ,
  password_required BOOLEAN,
  membership_status TEXT
) AS $$
  SELECT
    app_user.id,
    app_user.email,
    app_user.full_name,
    app_user.password_hash,
    app_user.is_developer,
    app_user.is_administrator,
    app_user.home_tenant_id,
    app_user.status,
    app_user.last_login_at,
    EXISTS (
      SELECT 1
      FROM public.user_assignment AS assignment
      WHERE assignment.user_id = app_user.id
        AND assignment.tenant_id = app_user.home_tenant_id
        AND assignment.is_active
        AND assignment.password_required
    ) AS password_required,
    membership.status AS membership_status
  FROM public.app_user AS app_user
  LEFT JOIN public.tenant_membership AS membership
    ON membership.tenant_id = app_user.home_tenant_id
   AND membership.user_id = app_user.id
  WHERE app_user.email_lower = pg_catalog.lower(pg_catalog.btrim(p_email))
    AND public.auth_email_code_matches(
      p_code_id,
      p_email,
      p_candidate_hash
    )
  LIMIT 1
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ACTIVE_LOOKUP_AUTH_USER_BY_ID_SQL = """
CREATE FUNCTION public.lookup_auth_user_by_id(
  p_user_id UUID,
  p_session_id UUID
) RETURNS TABLE(
  id UUID,
  email TEXT,
  full_name TEXT,
  password_hash TEXT,
  is_developer BOOLEAN,
  is_administrator BOOLEAN,
  home_tenant_id UUID,
  status TEXT,
  last_login_at TIMESTAMPTZ,
  password_required BOOLEAN,
  membership_status TEXT
) AS $$
BEGIN
  IF NOT public.is_support_session()
    AND NOT (
      (
        p_session_id IS NULL
        AND p_user_id IS NOT DISTINCT FROM public.current_app_user_id()
      )
      OR EXISTS (
        SELECT 1
        FROM public.session AS auth_session
        WHERE auth_session.id = p_session_id
          AND auth_session.user_id = p_user_id
          AND auth_session.revoked_at IS NULL
          AND auth_session.expires_at > pg_catalog.now()
      )
    )
  THEN
    RAISE EXCEPTION 'Authentication user is unavailable'
      USING ERRCODE = '42501';
  END IF;

  RETURN QUERY
  SELECT
    app_user.id,
    app_user.email,
    app_user.full_name,
    NULL::TEXT AS password_hash,
    app_user.is_developer,
    app_user.is_administrator,
    app_user.home_tenant_id,
    app_user.status,
    app_user.last_login_at,
    EXISTS (
      SELECT 1
      FROM public.user_assignment AS assignment
      WHERE assignment.user_id = app_user.id
        AND assignment.tenant_id = app_user.home_tenant_id
        AND assignment.is_active
        AND assignment.password_required
    ) AS password_required,
    membership.status AS membership_status
  FROM public.app_user AS app_user
  LEFT JOIN public.tenant_membership AS membership
    ON membership.tenant_id = app_user.home_tenant_id
   AND membership.user_id = app_user.id
  WHERE app_user.id = p_user_id;
END;
$$ LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LEGACY_LOOKUP_LOGIN_USER_BY_EMAIL_SQL = """
CREATE FUNCTION public.lookup_login_user_by_email(
  p_email TEXT,
  p_code_id UUID,
  p_candidate_hash TEXT
) RETURNS TABLE(
  id UUID,
  email TEXT,
  full_name TEXT,
  password_hash TEXT,
  is_developer BOOLEAN,
  is_administrator BOOLEAN,
  home_tenant_id UUID,
  status TEXT,
  last_login_at TIMESTAMPTZ,
  password_required BOOLEAN
) AS $$
  SELECT
    app_user.id,
    app_user.email,
    app_user.full_name,
    app_user.password_hash,
    app_user.is_developer,
    app_user.is_administrator,
    app_user.home_tenant_id,
    app_user.status,
    app_user.last_login_at,
    EXISTS (
      SELECT 1
      FROM public.user_assignment AS assignment
      WHERE assignment.user_id = app_user.id
        AND assignment.tenant_id = app_user.home_tenant_id
        AND assignment.is_active
        AND assignment.password_required
    ) AS password_required
  FROM public.app_user AS app_user
  WHERE app_user.email_lower = pg_catalog.lower(pg_catalog.btrim(p_email))
    AND public.auth_email_code_matches(
      p_code_id,
      p_email,
      p_candidate_hash
    )
  LIMIT 1
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LEGACY_LOOKUP_AUTH_USER_BY_ID_SQL = """
CREATE FUNCTION public.lookup_auth_user_by_id(
  p_user_id UUID,
  p_session_id UUID
) RETURNS TABLE(
  id UUID,
  email TEXT,
  full_name TEXT,
  password_hash TEXT,
  is_developer BOOLEAN,
  is_administrator BOOLEAN,
  home_tenant_id UUID,
  status TEXT,
  last_login_at TIMESTAMPTZ,
  password_required BOOLEAN
) AS $$
BEGIN
  IF session_user <> 'aurum_support'
    AND NOT (
      (
        p_session_id IS NULL
        AND p_user_id IS NOT DISTINCT FROM public.current_app_user_id()
      )
      OR EXISTS (
        SELECT 1
        FROM public.session AS auth_session
        WHERE auth_session.id = p_session_id
          AND auth_session.user_id = p_user_id
          AND auth_session.revoked_at IS NULL
          AND auth_session.expires_at > pg_catalog.now()
      )
    )
  THEN
    RAISE EXCEPTION 'Authentication user is unavailable'
      USING ERRCODE = '42501';
  END IF;

  RETURN QUERY
  SELECT
    app_user.id,
    app_user.email,
    app_user.full_name,
    NULL::TEXT AS password_hash,
    app_user.is_developer,
    app_user.is_administrator,
    app_user.home_tenant_id,
    app_user.status,
    app_user.last_login_at,
    EXISTS (
      SELECT 1
      FROM public.user_assignment AS assignment
      WHERE assignment.user_id = app_user.id
        AND assignment.tenant_id = app_user.home_tenant_id
        AND assignment.is_active
        AND assignment.password_required
    ) AS password_required
  FROM public.app_user AS app_user
  WHERE app_user.id = p_user_id;
END;
$$ LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ROLE_DELEGATION_GATE_SQL = """
CREATE FUNCTION public.tenant_actor_can_delegate_role(
  p_tenant_id UUID,
  p_role_id UUID,
  p_branch_id UUID
) RETURNS BOOLEAN AS $$
  SELECT public.is_support_session()
    OR (
      public.tenant_actor_is_owner(p_tenant_id)
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


TENANT_ACTOR_IS_OWNER_SQL = """
CREATE FUNCTION public.tenant_actor_is_owner(
  p_tenant_id UUID
) RETURNS BOOLEAN AS $$
  SELECT public.is_support_session()
    OR (
      p_tenant_id IS NOT NULL
      AND p_tenant_id IS NOT DISTINCT FROM public.current_tenant_id()
      AND public.current_app_user_id() IS NOT NULL
      AND EXISTS (
        SELECT 1
        FROM public.tenant_membership AS membership
        JOIN public.tenant_ownership AS ownership
          ON ownership.membership_id = membership.id
         AND ownership.tenant_id = membership.tenant_id
         AND ownership.is_active
        WHERE membership.tenant_id = p_tenant_id
          AND membership.user_id = public.current_app_user_id()
          AND membership.status = 'active'
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
    OR (
      NEW.is_active
      AND v_membership_status NOT IN ('pending', 'active')
    )
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

  IF public.is_support_session() THEN
    RETURN NEW;
  END IF;

  IF NOT public.tenant_actor_is_owner(NEW.tenant_id)
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


LEGACY_ASSIGNMENT_SCOPE_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION public.trg_guard_user_assignment_scope()
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


ROLE_MUTATION_GUARD_SQL = """
CREATE FUNCTION public.trg_guard_tenant_role_mutation()
RETURNS TRIGGER AS $$
DECLARE
  v_role_id UUID;
  v_tenant_id UUID;
BEGIN
  IF public.is_support_session()
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
    OR NOT public.tenant_actor_is_owner(v_tenant_id)
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
          NOT permission.is_active
          OR permission.target_role_type <> 'tenant'
          OR NOT permission.owner_delegable
          OR NOT public.tenant_actor_has_permission(
            v_tenant_id,
            permission.code
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
CREATE FUNCTION public.trg_guard_role_permission_mutation()
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
  IF public.is_support_session()
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

  SELECT
    role.tenant_id,
    role.is_system,
    role.is_protected
  INTO
    v_role_tenant_id,
    v_role_is_system,
    v_role_is_protected
  FROM public.role AS role
  WHERE role.id = v_role_id
    AND role.is_active;

  IF v_role_tenant_id IS NULL
    OR v_role_is_system
    OR v_role_is_protected
    OR v_role_tenant_id IS DISTINCT FROM public.current_tenant_id()
    OR NOT public.tenant_actor_is_owner(v_role_tenant_id)
    OR NOT public.tenant_actor_has_permission(
      v_role_tenant_id,
      'roles.update'
    )
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

  SELECT
    permission.is_active,
    permission.owner_delegable,
    permission.target_role_type
  INTO
    v_permission_active,
    v_permission_owner_delegable,
    v_permission_target_role_type
  FROM public.permission AS permission
  WHERE permission.code = CASE
    WHEN TG_OP = 'DELETE' THEN OLD.permission_code
    ELSE NEW.permission_code
  END;

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
      CASE
        WHEN TG_OP = 'DELETE' THEN OLD.permission_code
        ELSE NEW.permission_code
      END
    )
  THEN
    RAISE EXCEPTION 'Permission is outside actor delegation scope'
      USING ERRCODE = '42501';
  END IF;

  IF TG_OP = 'INSERT' AND NOT EXISTS (
    SELECT 1
    FROM public.permission AS actor_permission
    WHERE actor_permission.is_active
      AND actor_permission.target_role_type = 'tenant'
      AND actor_permission.owner_delegable
      AND actor_permission.code <> NEW.permission_code
      AND public.tenant_actor_has_permission(
        v_role_tenant_id,
        actor_permission.code
      )
      AND NOT EXISTS (
        SELECT 1
        FROM public.role_permission AS existing_permission
        WHERE existing_permission.role_id = v_role_id
          AND existing_permission.permission_code = actor_permission.code
      )
  ) THEN
    RAISE EXCEPTION 'Role cannot reproduce the owner delegation scope'
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


MEMBERSHIP_GUARD_TRIGGER_SQL = """
CREATE FUNCTION public.trg_guard_tenant_membership()
RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'UPDATE' THEN
    IF OLD.tenant_id IS DISTINCT FROM NEW.tenant_id
      OR OLD.user_id IS DISTINCT FROM NEW.user_id
    THEN
      RAISE EXCEPTION 'Membership identity cannot be changed'
        USING ERRCODE = '42501';
    END IF;

    IF OLD.status = 'active'
      AND NEW.status <> 'active'
      AND EXISTS (
        SELECT 1
        FROM public.tenant_ownership AS ownership
        WHERE ownership.tenant_id = OLD.tenant_id
          AND ownership.membership_id = OLD.id
          AND ownership.is_active
      )
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
          AND other_ownership.membership_id <> OLD.id
      )
      THEN
        RAISE EXCEPTION 'The last active owner cannot be suspended or offboarded'
          USING ERRCODE = '23514';
      END IF;
    END IF;
  END IF;

  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


OWNERSHIP_GUARD_TRIGGER_SQL = """
CREATE FUNCTION public.trg_guard_tenant_ownership()
RETURNS TRIGGER AS $$
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


UPDATE_MEMBERSHIP_PROFILE_SQL = """
CREATE FUNCTION public.update_tenant_membership_profile(
  p_tenant_id UUID,
  p_user_id UUID,
  p_full_name TEXT,
  p_phone TEXT
) RETURNS BOOLEAN AS $$
DECLARE
  v_updated INTEGER;
BEGIN
  IF NOT public.tenant_actor_is_owner(p_tenant_id)
    OR NOT public.tenant_actor_has_permission(p_tenant_id, 'users.update')
  THEN
    RAISE EXCEPTION 'Membership profile update is not allowed'
      USING ERRCODE = '42501';
  END IF;

  IF NULLIF(pg_catalog.btrim(p_full_name), '') IS NULL THEN
    RAISE EXCEPTION 'Full name is required'
      USING ERRCODE = '22023';
  END IF;

  UPDATE public.tenant_membership AS membership
  SET
    full_name = pg_catalog.btrim(p_full_name),
    phone = NULLIF(pg_catalog.btrim(p_phone), ''),
    updated_by = public.current_app_user_id()
  WHERE membership.tenant_id = p_tenant_id
    AND membership.user_id = p_user_id
    AND membership.status <> 'offboarded';

  GET DIAGNOSTICS v_updated = ROW_COUNT;
  RETURN v_updated = 1;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


SET_MEMBERSHIP_STATUS_SQL = """
CREATE FUNCTION public.set_tenant_membership_status(
  p_tenant_id UUID,
  p_user_id UUID,
  p_status TEXT,
  p_changed_at TIMESTAMPTZ
) RETURNS BOOLEAN AS $$
DECLARE
  v_actor_id UUID;
  v_current_status TEXT;
  v_required_permission TEXT;
  v_updated INTEGER;
BEGIN
  v_actor_id := public.current_app_user_id();

  IF p_changed_at IS NULL THEN
    RAISE EXCEPTION 'Status timestamp is required'
      USING ERRCODE = '22004';
  END IF;

  IF p_status = 'active' THEN
    v_required_permission := 'users.update';
  ELSIF p_status = 'suspended' THEN
    v_required_permission := 'users.block';
  ELSIF p_status = 'offboarded' THEN
    v_required_permission := 'users.delete';
  ELSE
    RAISE EXCEPTION 'Unsupported membership status transition'
      USING ERRCODE = '22023';
  END IF;

  IF NOT public.tenant_actor_is_owner(p_tenant_id)
    OR NOT public.tenant_actor_has_permission(p_tenant_id, v_required_permission)
  THEN
    RAISE EXCEPTION 'Membership status update is not allowed'
      USING ERRCODE = '42501';
  END IF;

  SELECT membership.status
  INTO v_current_status
  FROM public.tenant_membership AS membership
  WHERE membership.tenant_id = p_tenant_id
    AND membership.user_id = p_user_id
  FOR UPDATE;

  IF v_current_status IS NULL
    OR p_user_id = v_actor_id
    OR v_current_status = 'offboarded'
    OR (v_current_status = 'pending' AND p_status = 'suspended')
  THEN
    RAISE EXCEPTION 'Membership status transition is not allowed'
      USING ERRCODE = '42501';
  END IF;

  IF NOT public.is_support_session() AND EXISTS (
    SELECT 1
    FROM public.tenant_ownership AS ownership
    JOIN public.tenant_membership AS membership
      ON membership.id = ownership.membership_id
     AND membership.tenant_id = ownership.tenant_id
    WHERE ownership.tenant_id = p_tenant_id
      AND membership.user_id = p_user_id
      AND ownership.is_active
  ) THEN
    RAISE EXCEPTION 'An owner membership cannot be changed through user lifecycle'
      USING ERRCODE = '42501';
  END IF;

  UPDATE public.tenant_membership AS membership
  SET
    status = p_status,
    activated_at = CASE
      WHEN p_status = 'active' THEN p_changed_at
      ELSE membership.activated_at
    END,
    suspended_at = CASE
      WHEN p_status = 'suspended' THEN p_changed_at
      WHEN p_status = 'active' THEN NULL
      ELSE membership.suspended_at
    END,
    offboarded_at = CASE
      WHEN p_status = 'offboarded' THEN p_changed_at
      ELSE membership.offboarded_at
    END,
    updated_by = v_actor_id
  WHERE membership.tenant_id = p_tenant_id
    AND membership.user_id = p_user_id;

  GET DIAGNOSTICS v_updated = ROW_COUNT;

  IF v_updated = 1 AND p_status IN ('suspended', 'offboarded') THEN
    UPDATE public.user_assignment AS assignment
    SET
      is_active = false,
      updated_by = v_actor_id
    WHERE assignment.tenant_id = p_tenant_id
      AND assignment.user_id = p_user_id
      AND assignment.is_active;

    UPDATE public.session AS auth_session
    SET
      revoked_at = COALESCE(auth_session.revoked_at, p_changed_at),
      revoked_reason = COALESCE(
        auth_session.revoked_reason,
        'membership_' || p_status
      )
    WHERE auth_session.user_id = p_user_id
      AND auth_session.revoked_at IS NULL;
  END IF;

  RETURN v_updated = 1;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ACCEPT_TENANT_INVITATION_SQL = """
CREATE FUNCTION public.accept_tenant_invitation(
  p_session_id UUID,
  p_tenant_id UUID,
  p_accepted_at TIMESTAMPTZ
) RETURNS INTEGER AS $$
DECLARE
  v_memberships INTEGER;
  v_home_tenant_id UUID;
  v_user_id UUID;
BEGIN
  IF p_accepted_at IS NULL THEN
    RAISE EXCEPTION 'Acceptance timestamp is required'
      USING ERRCODE = '22004';
  END IF;

  SELECT auth_session.user_id, app_user.home_tenant_id
  INTO v_user_id, v_home_tenant_id
  FROM public.session AS auth_session
  JOIN public.app_user AS app_user
    ON app_user.id = auth_session.user_id
  WHERE auth_session.id = p_session_id
    AND auth_session.revoked_at IS NULL;

  IF v_user_id IS NULL THEN
    RAISE EXCEPTION 'Authenticated session is unavailable'
      USING ERRCODE = '42501';
  END IF;

  PERFORM pg_catalog.set_config('app.user_id', v_user_id::TEXT, true);

  IF p_tenant_id IS NULL OR v_home_tenant_id IS DISTINCT FROM p_tenant_id THEN
    RETURN 0;
  END IF;

  UPDATE public.tenant_membership AS membership
  SET
    status = 'active',
    activated_at = COALESCE(membership.activated_at, p_accepted_at),
    suspended_at = NULL,
    updated_by = v_user_id
  WHERE membership.user_id = v_user_id
    AND membership.tenant_id = p_tenant_id
    AND membership.status = 'pending';

  GET DIAGNOSTICS v_memberships = ROW_COUNT;

  IF v_memberships = 1 THEN
    UPDATE public.app_user AS app_user
    SET
      status = 'active',
      activated_at = COALESCE(app_user.activated_at, p_accepted_at)
    WHERE app_user.id = v_user_id
      AND app_user.status = 'invited';
  END IF;

  RETURN v_memberships;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


MEMBERSHIP_AUDIT_TRIGGER_SQL = """
CREATE FUNCTION public.trg_audit_tenant_membership_event()
RETURNS TRIGGER AS $$
DECLARE
  v_action TEXT;
  v_metadata JSONB;
BEGIN
  IF TG_OP = 'INSERT' THEN
    v_action := 'MEMBERSHIP_CREATED';
    v_metadata := pg_catalog.jsonb_build_object(
      'membership_id', NEW.id,
      'user_id', NEW.user_id,
      'status', NEW.status
    );
  ELSIF OLD.status IS DISTINCT FROM NEW.status THEN
    v_action := CASE NEW.status
      WHEN 'active' THEN 'MEMBERSHIP_ACTIVATED'
      WHEN 'suspended' THEN 'MEMBERSHIP_SUSPENDED'
      WHEN 'offboarded' THEN 'MEMBERSHIP_OFFBOARDED'
      ELSE 'MEMBERSHIP_UPDATED'
    END;
    v_metadata := pg_catalog.jsonb_build_object(
      'membership_id', NEW.id,
      'user_id', NEW.user_id,
      'before_status', OLD.status,
      'after_status', NEW.status
    );
  ELSIF OLD.full_name IS DISTINCT FROM NEW.full_name
    OR OLD.phone IS DISTINCT FROM NEW.phone
  THEN
    v_action := 'MEMBERSHIP_UPDATED';
    v_metadata := pg_catalog.jsonb_build_object(
      'membership_id', NEW.id,
      'user_id', NEW.user_id,
      'changed_fields',
      pg_catalog.array_remove(ARRAY[
        CASE WHEN OLD.full_name IS DISTINCT FROM NEW.full_name THEN 'full_name' END,
        CASE WHEN OLD.phone IS DISTINCT FROM NEW.phone THEN 'phone' END
      ], NULL)
    );
  ELSE
    RETURN NEW;
  END IF;

  INSERT INTO public.audit_log (
    tenant_id,
    user_id,
    action,
    table_name,
    record_id,
    metadata,
    created_at
  ) VALUES (
    NEW.tenant_id,
    public.current_app_user_id(),
    v_action,
    'tenant_membership',
    NEW.id,
    v_metadata,
    now()
  );

  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


OWNERSHIP_AUDIT_TRIGGER_SQL = """
CREATE FUNCTION public.trg_audit_tenant_ownership_event()
RETURNS TRIGGER AS $$
DECLARE
  v_action TEXT;
BEGIN
  IF TG_OP = 'INSERT' THEN
    v_action := 'OWNERSHIP_GRANTED';
  ELSIF OLD.is_active AND NOT NEW.is_active THEN
    v_action := 'OWNERSHIP_REVOKED';
  ELSE
    RETURN NEW;
  END IF;

  INSERT INTO public.audit_log (
    tenant_id,
    user_id,
    action,
    table_name,
    record_id,
    metadata,
    created_at
  ) VALUES (
    NEW.tenant_id,
    public.current_app_user_id(),
    v_action,
    'tenant_ownership',
    NEW.id,
    pg_catalog.jsonb_build_object(
      'ownership_id', NEW.id,
      'membership_id', NEW.membership_id,
      'is_active', NEW.is_active
    ),
    now()
  );

  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ROLE_PERMISSION_AUDIT_SQL = """
CREATE FUNCTION public.record_role_permission_change(
  p_role_id UUID,
  p_before_permissions TEXT[],
  p_after_permissions TEXT[]
) RETURNS VOID AS $$
DECLARE
  v_role_tenant_id UUID;
  v_role_version INTEGER;
BEGIN
  SELECT role.tenant_id, role.version
  INTO v_role_tenant_id, v_role_version
  FROM public.role AS role
  WHERE role.id = p_role_id;

  IF v_role_tenant_id IS NULL THEN
    RAISE EXCEPTION 'Tenant role is unavailable for audit'
      USING ERRCODE = '42501';
  END IF;

  IF NOT public.is_support_session()
    AND v_role_tenant_id IS DISTINCT FROM public.current_tenant_id()
  THEN
    RAISE EXCEPTION 'Role audit tenant does not match active context'
      USING ERRCODE = '42501';
  END IF;

  IF p_before_permissions IS NOT DISTINCT FROM p_after_permissions THEN
    RETURN;
  END IF;

  INSERT INTO public.audit_log (
    tenant_id,
    user_id,
    action,
    table_name,
    record_id,
    metadata,
    created_at
  ) VALUES (
    v_role_tenant_id,
    public.current_app_user_id(),
    'ROLE_PERMISSIONS_CHANGED',
    'role',
    p_role_id,
    pg_catalog.jsonb_build_object(
      'role_id', p_role_id,
      'role_version', v_role_version,
      'before_permissions', pg_catalog.to_jsonb(p_before_permissions),
      'after_permissions', pg_catalog.to_jsonb(p_after_permissions)
    ),
    now()
  );
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


AUTHORIZATION_MEMBERSHIP_TRIGGER_SQL = """
CREATE FUNCTION public.trg_authorization_membership_mutation()
RETURNS TRIGGER AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    PERFORM public.bump_authorization_subject_revision(OLD.tenant_id, OLD.user_id);
    RETURN OLD;
  END IF;

  PERFORM public.bump_authorization_subject_revision(NEW.tenant_id, NEW.user_id);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


AUTHORIZATION_OWNERSHIP_TRIGGER_SQL = """
CREATE FUNCTION public.trg_authorization_ownership_mutation()
RETURNS TRIGGER AS $$
DECLARE
  v_user_id UUID;
BEGIN
  SELECT membership.user_id
  INTO v_user_id
  FROM public.tenant_membership AS membership
  WHERE membership.id = CASE WHEN TG_OP = 'DELETE' THEN OLD.membership_id ELSE NEW.membership_id END;

  PERFORM public.bump_authorization_subject_revision(
    CASE WHEN TG_OP = 'DELETE' THEN OLD.tenant_id ELSE NEW.tenant_id END,
    v_user_id
  );
  PERFORM public.bump_authorization_policy_revision(
    CASE WHEN TG_OP = 'DELETE' THEN OLD.tenant_id ELSE NEW.tenant_id END
  );

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


def _secure_function(signature: str, *, app_access: bool = False) -> None:
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_support")
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} FROM PUBLIC, aurum_app")
    grantees = "aurum_support, aurum_app" if app_access else "aurum_support"
    op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {grantees}")


def _replace_auth_user_lookup_functions(
    *,
    login_lookup_sql: str,
    user_lookup_sql: str,
) -> None:
    login_signature = "public.lookup_login_user_by_email(TEXT, UUID, TEXT)"
    user_signature = "public.lookup_auth_user_by_id(UUID, UUID)"
    op.execute(f"DROP FUNCTION {login_signature}")
    op.execute(f"DROP FUNCTION {user_signature}")
    op.execute(login_lookup_sql)
    op.execute(user_lookup_sql)
    _secure_function(login_signature, app_access=True)
    _secure_function(user_signature, app_access=True)


def _add_metadata_columns() -> None:
    op.execute("""
        ALTER TABLE public.permission
          ADD COLUMN scope_type TEXT NOT NULL DEFAULT 'TENANT_ALL',
          ADD COLUMN target_role_type TEXT NOT NULL DEFAULT 'tenant',
          ADD COLUMN risk_level TEXT NOT NULL DEFAULT 'normal',
          ADD COLUMN developer_grantable BOOLEAN NOT NULL DEFAULT false,
          ADD COLUMN administrator_grantable BOOLEAN NOT NULL DEFAULT false,
          ADD COLUMN owner_grantable BOOLEAN NOT NULL DEFAULT false,
          ADD COLUMN developer_delegable BOOLEAN NOT NULL DEFAULT false,
          ADD COLUMN administrator_delegable BOOLEAN NOT NULL DEFAULT false,
          ADD COLUMN owner_delegable BOOLEAN NOT NULL DEFAULT false,
          ADD COLUMN requires_step_up BOOLEAN NOT NULL DEFAULT false,
          ADD COLUMN requires_confirmation BOOLEAN NOT NULL DEFAULT false,
          ADD CONSTRAINT ck_permission_scope_type
            CHECK (scope_type IN ('PLATFORM','TENANT_ALL','BRANCH_SET','OWN')),
          ADD CONSTRAINT ck_permission_target_role_type
            CHECK (target_role_type IN ('platform','tenant')),
          ADD CONSTRAINT ck_permission_risk_level
            CHECK (risk_level IN ('normal','sensitive','critical'))
        """)
    op.execute("""
        UPDATE public.permission
        SET
          scope_type = CASE
            WHEN code = 'audit.view.global' THEN 'PLATFORM'
            WHEN code IN ('audit.view.own', 'sales.view.own') THEN 'OWN'
            WHEN code IN (
              'branches.view',
              'branches.update',
              'branches.delete',
              'registers.view',
               'registers.create',
               'registers.update',
               'registers.delete',
               'batches.write_off',
              'incoming.view',
              'incoming.create',
              'incoming.return',
              'pos.shift_open',
              'pos.shift_close',
              'pos.sell',
              'pos.refund',
              'pos.handle_prescription',
              'reports.view',
              'sales.view.tenant'
            ) THEN 'BRANCH_SET'
            ELSE 'TENANT_ALL'
          END,
          target_role_type = CASE
            WHEN code = 'audit.view.global' THEN 'platform'
            ELSE 'tenant'
          END,
          risk_level = CASE
            WHEN code IN ('audit.view.global', 'tenant.export.full') THEN 'critical'
            WHEN is_dangerous THEN 'sensitive'
            ELSE 'normal'
          END,
          developer_grantable = code <> 'audit.view.global',
          administrator_grantable = code <> 'audit.view.global',
          owner_grantable = code <> 'audit.view.global' AND min_level_required >= 3,
          developer_delegable = code <> 'audit.view.global',
          administrator_delegable = code <> 'audit.view.global',
          owner_delegable = code <> 'audit.view.global' AND min_level_required >= 3,
          requires_step_up = code IN ('audit.view.global', 'tenant.export.full'),
          requires_confirmation = is_dangerous
            OR code IN ('audit.view.global', 'tenant.export.full')
        """)

    op.execute("""
        ALTER TABLE public.role
          ADD COLUMN is_protected BOOLEAN NOT NULL DEFAULT false,
          ADD COLUMN protected_kind TEXT,
          ADD COLUMN version INTEGER NOT NULL DEFAULT 1,
          ADD CONSTRAINT ck_role_version CHECK (version >= 1),
          ADD CONSTRAINT ck_role_protected_kind CHECK (
            (
              is_protected
              AND protected_kind IN ('developer','administrator','tenant_owner')
            )
            OR (NOT is_protected AND protected_kind IS NULL)
          )
        """)
    op.execute("""
        UPDATE public.role
        SET
          is_protected = true,
          protected_kind = CASE
            WHEN is_system AND name = 'developer' THEN 'developer'
            ELSE 'administrator'
          END
        WHERE is_system AND name IN ('developer', 'administrator')
        """)
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM public.role AS owner_role
            WHERE NOT owner_role.is_system
              AND owner_role.tenant_id IS NOT NULL
              AND owner_role.name = 'Владелец'
              AND EXISTS (
                SELECT 1
                FROM public.user_assignment AS assignment
                WHERE assignment.role_id = owner_role.id
                  AND assignment.is_active
              )
              AND NOT (
                owner_role.level = 3
                AND EXISTS (
                  SELECT 1
                  FROM public.role_template AS template
                  WHERE template.slug = 'owner'
                    AND template.is_active
                )
                AND NOT EXISTS (
                  SELECT role_permission.permission_code
                  FROM public.role_permission AS role_permission
                  WHERE role_permission.role_id = owner_role.id
                    AND NOT EXISTS (
                      SELECT 1
                      FROM public.role_template AS template
                      JOIN public.role_template_permission AS template_permission
                        ON template_permission.template_id = template.id
                      WHERE template.slug = 'owner'
                        AND template.is_active
                        AND template_permission.permission_code =
                            role_permission.permission_code
                    )
                )
                AND NOT EXISTS (
                  SELECT template_permission.permission_code
                  FROM public.role_template AS template
                  JOIN public.role_template_permission AS template_permission
                    ON template_permission.template_id = template.id
                  WHERE template.slug = 'owner'
                    AND template.is_active
                    AND NOT EXISTS (
                      SELECT 1
                      FROM public.role_permission AS role_permission
                      WHERE role_permission.role_id = owner_role.id
                        AND role_permission.permission_code =
                            template_permission.permission_code
                    )
                )
              )
          ) THEN
            RAISE EXCEPTION
              'Assigned role named Владелец does not match the owner template';
          END IF;
        END
        $$
        """)
    op.execute("""
        UPDATE public.role AS owner_role
        SET
          is_protected = true,
          protected_kind = 'tenant_owner'
        WHERE NOT owner_role.is_system
          AND owner_role.tenant_id IS NOT NULL
          AND owner_role.name = 'Владелец'
          AND owner_role.level = 3
          AND EXISTS (
            SELECT 1
            FROM public.role_template AS template
            WHERE template.slug = 'owner'
              AND template.is_active
          )
          AND NOT EXISTS (
            SELECT role_permission.permission_code
            FROM public.role_permission AS role_permission
            WHERE role_permission.role_id = owner_role.id
              AND NOT EXISTS (
                SELECT 1
                FROM public.role_template AS template
                JOIN public.role_template_permission AS template_permission
                  ON template_permission.template_id = template.id
                WHERE template.slug = 'owner'
                  AND template.is_active
                  AND template_permission.permission_code =
                      role_permission.permission_code
              )
          )
          AND NOT EXISTS (
            SELECT template_permission.permission_code
            FROM public.role_template AS template
            JOIN public.role_template_permission AS template_permission
              ON template_permission.template_id = template.id
            WHERE template.slug = 'owner'
              AND template.is_active
              AND NOT EXISTS (
                SELECT 1
                FROM public.role_permission AS role_permission
                WHERE role_permission.role_id = owner_role.id
                  AND role_permission.permission_code =
                      template_permission.permission_code
              )
          )
        """)


def _create_membership_tables() -> None:
    op.execute("""
        CREATE TABLE public.tenant_membership (
          id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id       UUID NOT NULL REFERENCES public.tenant(id) ON DELETE CASCADE,
          user_id         UUID NOT NULL REFERENCES public.app_user(id) ON DELETE CASCADE,
          full_name       TEXT NOT NULL,
          phone           TEXT,
          status          TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending','active','suspended','offboarded')),
          invited_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          activated_at    TIMESTAMPTZ,
          suspended_at    TIMESTAMPTZ,
          offboarded_at   TIMESTAMPTZ,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by      UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          updated_by      UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          CONSTRAINT uq_tenant_membership_tenant_user UNIQUE (tenant_id, user_id)
        )
        """)
    op.execute("""
        CREATE TABLE public.tenant_ownership (
          id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          tenant_id       UUID NOT NULL REFERENCES public.tenant(id) ON DELETE CASCADE,
          membership_id   UUID NOT NULL
                          REFERENCES public.tenant_membership(id) ON DELETE RESTRICT,
          is_active       BOOLEAN NOT NULL DEFAULT true,
          granted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          revoked_at      TIMESTAMPTZ,
          created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
          created_by      UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          updated_by      UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          CONSTRAINT uq_tenant_ownership_tenant_membership
            UNIQUE (tenant_id, membership_id)
        )
        """)
    op.execute("CREATE INDEX ix_tenant_membership_user ON public.tenant_membership (user_id)")
    op.execute(
        "CREATE INDEX ix_tenant_membership_tenant_status "
        "ON public.tenant_membership (tenant_id, status)"
    )
    op.execute(
        "CREATE INDEX ix_tenant_ownership_active "
        "ON public.tenant_ownership (tenant_id, membership_id) WHERE is_active"
    )

    op.execute("""
        INSERT INTO public.tenant_membership (
          tenant_id,
          user_id,
          full_name,
          phone,
          status,
          invited_at,
          activated_at,
          suspended_at,
          offboarded_at,
          created_at,
          updated_at
        )
        SELECT
          account_tenants.tenant_id,
          app_user.id,
          app_user.full_name,
          app_user.phone,
          CASE app_user.status
            WHEN 'active' THEN 'active'
            WHEN 'blocked' THEN 'suspended'
            WHEN 'archived' THEN 'offboarded'
            ELSE 'pending'
          END,
          app_user.invited_at,
          app_user.activated_at,
          app_user.blocked_at,
          app_user.archived_at,
          app_user.created_at,
          app_user.updated_at
        FROM public.app_user AS app_user
        JOIN (
          SELECT home_tenant_id AS tenant_id, id AS user_id
          FROM public.app_user
          WHERE home_tenant_id IS NOT NULL
          UNION
          SELECT tenant_id, user_id
          FROM public.user_assignment
        ) AS account_tenants
          ON account_tenants.user_id = app_user.id
        ON CONFLICT (tenant_id, user_id) DO NOTHING
        """)

    op.execute("""
        INSERT INTO public.tenant_ownership (
          tenant_id,
          membership_id,
          is_active,
          granted_at,
          created_at,
          updated_at,
          created_by,
          updated_by
        )
        SELECT
          assignment.tenant_id,
          membership.id,
          true,
          pg_catalog.min(assignment.created_at),
          pg_catalog.min(assignment.created_at),
          pg_catalog.max(assignment.updated_at),
          NULL,
          NULL
        FROM public.user_assignment AS assignment
        JOIN public.role AS assigned_role
          ON assigned_role.id = assignment.role_id
         AND assigned_role.is_protected
         AND assigned_role.protected_kind = 'tenant_owner'
        JOIN public.tenant_membership AS membership
          ON membership.tenant_id = assignment.tenant_id
         AND membership.user_id = assignment.user_id
         AND membership.status = 'active'
        WHERE assignment.is_active
        GROUP BY assignment.tenant_id, membership.id
        ON CONFLICT (tenant_id, membership_id) DO NOTHING
        """)

    op.execute("ALTER TABLE public.user_assignment ADD COLUMN membership_id UUID")
    op.execute("""
        UPDATE public.user_assignment AS assignment
        SET membership_id = membership.id
        FROM public.tenant_membership AS membership
        WHERE membership.tenant_id = assignment.tenant_id
          AND membership.user_id = assignment.user_id
        """)
    op.execute("ALTER TABLE public.user_assignment ALTER COLUMN membership_id SET NOT NULL")
    op.execute("""
        ALTER TABLE public.user_assignment
          ADD CONSTRAINT fk_user_assignment_membership
          FOREIGN KEY (membership_id)
          REFERENCES public.tenant_membership(id)
          ON DELETE RESTRICT
        """)
    op.execute(
        "CREATE INDEX ix_user_assignment_membership " "ON public.user_assignment (membership_id)"
    )


def _configure_rls_and_grants() -> None:
    for table in ("tenant_membership", "tenant_ownership"):
        op.execute(f"ALTER TABLE public.{table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY tenant_read ON public.{table}
              FOR SELECT TO aurum_app
              USING (tenant_id = public.current_tenant_id())
            """)
        op.execute(
            f"REVOKE ALL PRIVILEGES ON TABLE public.{table} "
            "FROM PUBLIC, aurum_app, aurum_support"
        )
        op.execute(f"GRANT SELECT ON TABLE public.{table} TO aurum_app")
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE " f"ON TABLE public.{table} TO aurum_support"
        )

    op.execute("DROP POLICY app_user_directory_read ON public.app_user")
    op.execute("""
        CREATE POLICY app_user_directory_read ON public.app_user
          FOR SELECT TO aurum_app
          USING (
            id = public.current_app_user_id()
            OR EXISTS (
              SELECT 1
              FROM public.tenant_membership AS membership
              WHERE membership.user_id = app_user.id
                AND membership.tenant_id = public.current_tenant_id()
            )
          )
        """)

    op.execute("DROP POLICY role_write ON public.role")
    op.execute("""
        CREATE POLICY role_write ON public.role
          FOR ALL
          USING (
            NOT is_system
            AND NOT is_protected
            AND tenant_id = public.current_tenant_id()
            AND public.tenant_actor_is_owner(tenant_id)
          )
          WITH CHECK (
            NOT is_system
            AND NOT is_protected
            AND tenant_id = public.current_tenant_id()
            AND public.tenant_actor_is_owner(tenant_id)
          )
        """)
    op.execute("DROP POLICY role_permission_write ON public.role_permission")
    op.execute("""
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
                AND public.tenant_actor_is_owner(scoped_role.tenant_id)
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
                AND public.tenant_actor_is_owner(scoped_role.tenant_id)
            )
          )
        """)


def _create_triggers() -> None:
    op.execute("""
        CREATE TRIGGER trg_guard_tenant_role_mutation
        BEFORE INSERT OR DELETE OR UPDATE ON public.role
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_tenant_role_mutation()
        """)
    op.execute("""
        CREATE TRIGGER trg_guard_role_permission_mutation
        BEFORE INSERT OR DELETE OR UPDATE ON public.role_permission
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_role_permission_mutation()
        """)

    for table in ("tenant_membership", "tenant_ownership"):
        op.execute(f"""
            CREATE TRIGGER trg_{table}_created
            BEFORE INSERT ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_meta()
            """)
        op.execute(f"""
            CREATE TRIGGER trg_{table}_updated
            BEFORE UPDATE ON public.{table}
            FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_meta()
            """)

    op.execute("""
        CREATE TRIGGER trg_guard_tenant_membership
        BEFORE UPDATE ON public.tenant_membership
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_tenant_membership()
        """)
    op.execute("""
        CREATE TRIGGER trg_guard_tenant_ownership
        BEFORE INSERT OR DELETE OR UPDATE ON public.tenant_ownership
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_tenant_ownership()
        """)
    op.execute("""
        CREATE TRIGGER trg_audit_tenant_membership_event
        AFTER INSERT OR UPDATE ON public.tenant_membership
        FOR EACH ROW EXECUTE FUNCTION public.trg_audit_tenant_membership_event()
        """)
    op.execute("""
        CREATE TRIGGER trg_audit_tenant_ownership_event
        AFTER INSERT OR UPDATE ON public.tenant_ownership
        FOR EACH ROW EXECUTE FUNCTION public.trg_audit_tenant_ownership_event()
        """)
    op.execute("""
        CREATE TRIGGER trg_authorization_membership_subject
        AFTER INSERT OR DELETE OR UPDATE ON public.tenant_membership
        FOR EACH ROW EXECUTE FUNCTION public.trg_authorization_membership_mutation()
        """)
    op.execute("""
        CREATE TRIGGER trg_authorization_ownership_subject
        AFTER INSERT OR DELETE OR UPDATE ON public.tenant_ownership
        FOR EACH ROW EXECUTE FUNCTION public.trg_authorization_ownership_mutation()
        """)

    op.execute("DROP TRIGGER trg_authorization_role_policy ON public.role")
    op.execute("""
        CREATE TRIGGER trg_authorization_role_policy
        AFTER INSERT OR DELETE OR UPDATE OF
          tenant_id, level, is_active, is_protected, protected_kind, version
        ON public.role
        FOR EACH ROW EXECUTE FUNCTION public.trg_authorization_policy_mutation()
        """)
    op.execute("DROP TRIGGER trg_authorization_permission_policy ON public.permission")
    op.execute("""
        CREATE TRIGGER trg_authorization_permission_policy
        AFTER INSERT OR DELETE OR UPDATE OF
          min_level_required,
          is_active,
          scope_type,
          target_role_type,
          risk_level,
          developer_grantable,
          administrator_grantable,
          owner_grantable,
          developer_delegable,
          administrator_delegable,
          owner_delegable
        ON public.permission
        FOR EACH ROW EXECUTE FUNCTION public.trg_authorization_permission_mutation()
        """)


def _expand_audit_actions() -> None:
    values = ", ".join(f"'{action}'" for action in AUDIT_ACTIONS)
    op.execute("ALTER TABLE public.audit_log DROP CONSTRAINT audit_log_action_check")
    op.execute(
        "ALTER TABLE public.audit_log "
        f"ADD CONSTRAINT audit_log_action_check CHECK (action IN ({values}))"
    )


def upgrade() -> None:
    _add_metadata_columns()
    _create_membership_tables()
    _replace_auth_user_lookup_functions(
        login_lookup_sql=ACTIVE_LOOKUP_LOGIN_USER_BY_EMAIL_SQL,
        user_lookup_sql=ACTIVE_LOOKUP_AUTH_USER_BY_ID_SQL,
    )

    op.execute(TENANT_ACTOR_IS_OWNER_SQL)
    _secure_function("public.tenant_actor_is_owner(UUID)", app_access=True)
    for legacy_function in (
        "public.find_invitable_user_id(UUID, TEXT)",
        "public.create_invited_app_user(UUID, TEXT, TEXT)",
        "public.update_tenant_user_profile(UUID, UUID, TEXT, TEXT)",
        "public.set_tenant_user_status(UUID, UUID, TEXT, TIMESTAMP WITH TIME ZONE)",
    ):
        op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {legacy_function} FROM aurum_app")

    op.execute(ACTIVE_PERMISSION_GATE_SQL)
    _secure_function("public.tenant_actor_has_permission(UUID, TEXT)")
    op.execute(SCOPED_PERMISSION_SQL)
    _secure_function(
        "public.tenant_actor_has_scoped_permission(UUID, TEXT, UUID)",
        app_access=True,
    )
    op.execute(ROLE_DELEGATION_GATE_SQL)
    _secure_function("public.tenant_actor_can_delegate_role(UUID, UUID, UUID)")
    op.execute(ASSIGNMENT_SCOPE_TRIGGER_SQL)
    _secure_function("public.trg_guard_user_assignment_scope()")
    op.execute(ROLE_MUTATION_GUARD_SQL)
    op.execute(ROLE_PERMISSION_MUTATION_GUARD_SQL)
    _secure_function("public.trg_guard_tenant_role_mutation()")
    _secure_function("public.trg_guard_role_permission_mutation()")

    op.execute(MEMBERSHIP_GUARD_TRIGGER_SQL)
    op.execute(OWNERSHIP_GUARD_TRIGGER_SQL)
    op.execute(UPDATE_MEMBERSHIP_PROFILE_SQL)
    op.execute(SET_MEMBERSHIP_STATUS_SQL)
    op.execute(ACCEPT_TENANT_INVITATION_SQL)
    op.execute(MEMBERSHIP_AUDIT_TRIGGER_SQL)
    op.execute(OWNERSHIP_AUDIT_TRIGGER_SQL)
    op.execute(ROLE_PERMISSION_AUDIT_SQL)
    op.execute(AUTHORIZATION_MEMBERSHIP_TRIGGER_SQL)
    op.execute(AUTHORIZATION_OWNERSHIP_TRIGGER_SQL)

    for signature, app_access in (
        ("public.trg_guard_tenant_membership()", False),
        ("public.trg_guard_tenant_ownership()", False),
        ("public.update_tenant_membership_profile(UUID, UUID, TEXT, TEXT)", True),
        (
            "public.set_tenant_membership_status(" "UUID, UUID, TEXT, TIMESTAMP WITH TIME ZONE)",
            True,
        ),
        (
            "public.accept_tenant_invitation(UUID, UUID, TIMESTAMP WITH TIME ZONE)",
            True,
        ),
        ("public.trg_audit_tenant_membership_event()", False),
        ("public.trg_audit_tenant_ownership_event()", False),
        ("public.record_role_permission_change(UUID, TEXT[], TEXT[])", True),
        ("public.trg_authorization_membership_mutation()", False),
        ("public.trg_authorization_ownership_mutation()", False),
    ):
        _secure_function(signature, app_access=app_access)

    _expand_audit_actions()
    _create_triggers()
    _configure_rls_and_grants()


def _restore_legacy_policies() -> None:
    op.execute("DROP POLICY role_permission_write ON public.role_permission")
    op.execute("""
        CREATE POLICY role_permission_write ON public.role_permission
          FOR ALL
          USING (
            EXISTS (
              SELECT 1
              FROM public.role AS scoped_role
              WHERE scoped_role.id = role_permission.role_id
                AND scoped_role.tenant_id = public.current_tenant_id()
                AND NOT scoped_role.is_system
            )
          )
          WITH CHECK (
            EXISTS (
              SELECT 1
              FROM public.role AS scoped_role
              WHERE scoped_role.id = role_permission.role_id
                AND scoped_role.tenant_id = public.current_tenant_id()
                AND NOT scoped_role.is_system
            )
          )
        """)
    op.execute("DROP POLICY role_write ON public.role")
    op.execute("""
        CREATE POLICY role_write ON public.role
          FOR ALL
          USING (
            NOT is_system
            AND tenant_id = public.current_tenant_id()
          )
          WITH CHECK (
            NOT is_system
            AND tenant_id = public.current_tenant_id()
          )
        """)
    op.execute("DROP POLICY app_user_directory_read ON public.app_user")
    op.execute("""
        CREATE POLICY app_user_directory_read ON public.app_user
          FOR SELECT TO aurum_app
          USING (
            id = public.current_app_user_id()
            OR EXISTS (
              SELECT 1
              FROM public.user_assignment AS assignment
              WHERE assignment.user_id = app_user.id
                AND assignment.tenant_id = public.current_tenant_id()
            )
          )
        """)


def _restore_legacy_audit_actions() -> None:
    op.execute("""
        UPDATE public.audit_log
        SET action = CASE
          WHEN action IN ('MEMBERSHIP_CREATED', 'OWNERSHIP_GRANTED') THEN 'INSERT'
          ELSE 'UPDATE'
        END
        WHERE action IN (
          'MEMBERSHIP_CREATED',
          'MEMBERSHIP_UPDATED',
          'MEMBERSHIP_ACTIVATED',
          'MEMBERSHIP_SUSPENDED',
          'MEMBERSHIP_OFFBOARDED',
          'OWNERSHIP_GRANTED',
          'OWNERSHIP_REVOKED',
          'ROLE_PERMISSIONS_CHANGED'
        )
        """)
    op.execute("ALTER TABLE public.audit_log DROP CONSTRAINT audit_log_action_check")
    op.execute("""
        ALTER TABLE public.audit_log
        ADD CONSTRAINT audit_log_action_check CHECK (
          action IN ('INSERT','UPDATE','DELETE','VIEW','EXPORT','IMPERSONATE')
        )
        """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_guard_role_permission_mutation " "ON public.role_permission"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_guard_tenant_role_mutation ON public.role")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_authorization_ownership_subject " "ON public.tenant_ownership"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_authorization_membership_subject " "ON public.tenant_membership"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_audit_tenant_ownership_event " "ON public.tenant_ownership"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_audit_tenant_membership_event " "ON public.tenant_membership"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_guard_tenant_ownership ON public.tenant_ownership")
    op.execute("DROP TRIGGER IF EXISTS trg_guard_tenant_membership ON public.tenant_membership")
    for table in ("tenant_ownership", "tenant_membership"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_updated ON public.{table}")
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_created ON public.{table}")

    op.execute("DROP TRIGGER trg_authorization_permission_policy ON public.permission")
    op.execute("""
        CREATE TRIGGER trg_authorization_permission_policy
        AFTER INSERT OR DELETE OR UPDATE OF min_level_required, is_active
        ON public.permission
        FOR EACH ROW EXECUTE FUNCTION public.trg_authorization_permission_mutation()
        """)
    op.execute("DROP TRIGGER trg_authorization_role_policy ON public.role")
    op.execute("""
        CREATE TRIGGER trg_authorization_role_policy
        AFTER INSERT OR DELETE OR UPDATE OF tenant_id, level, is_active
        ON public.role
        FOR EACH ROW EXECUTE FUNCTION public.trg_authorization_policy_mutation()
        """)

    _restore_legacy_policies()
    _restore_legacy_audit_actions()
    for legacy_function in (
        "public.find_invitable_user_id(UUID, TEXT)",
        "public.create_invited_app_user(UUID, TEXT, TEXT)",
        "public.update_tenant_user_profile(UUID, UUID, TEXT, TEXT)",
        "public.set_tenant_user_status(UUID, UUID, TEXT, TIMESTAMP WITH TIME ZONE)",
    ):
        op.execute(f"GRANT EXECUTE ON FUNCTION {legacy_function} TO aurum_app")

    op.execute(LEGACY_ASSIGNMENT_SCOPE_TRIGGER_SQL)
    _secure_function("public.trg_guard_user_assignment_scope()")
    op.execute(LEGACY_SCOPED_PERMISSION_SQL)
    _secure_function(
        "public.tenant_actor_has_scoped_permission(UUID, TEXT, UUID)",
        app_access=True,
    )
    op.execute(LEGACY_ACTIVE_PERMISSION_GATE_SQL)
    _secure_function("public.tenant_actor_has_permission(UUID, TEXT)")

    for signature in (
        "public.trg_authorization_ownership_mutation()",
        "public.trg_authorization_membership_mutation()",
        "public.trg_guard_role_permission_mutation()",
        "public.trg_guard_tenant_role_mutation()",
        "public.tenant_actor_can_delegate_role(UUID, UUID, UUID)",
        "public.record_role_permission_change(UUID, TEXT[], TEXT[])",
        "public.trg_audit_tenant_ownership_event()",
        "public.trg_audit_tenant_membership_event()",
        "public.set_tenant_membership_status(" "UUID, UUID, TEXT, TIMESTAMP WITH TIME ZONE)",
        "public.accept_tenant_invitation(UUID, UUID, TIMESTAMP WITH TIME ZONE)",
        "public.update_tenant_membership_profile(UUID, UUID, TEXT, TEXT)",
        "public.trg_guard_tenant_ownership()",
        "public.trg_guard_tenant_membership()",
        "public.tenant_actor_is_owner(UUID)",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS {signature}")

    op.execute("DROP INDEX IF EXISTS public.ix_user_assignment_membership")
    op.execute("ALTER TABLE public.user_assignment DROP CONSTRAINT fk_user_assignment_membership")
    op.execute("ALTER TABLE public.user_assignment DROP COLUMN membership_id")

    _replace_auth_user_lookup_functions(
        login_lookup_sql=LEGACY_LOOKUP_LOGIN_USER_BY_EMAIL_SQL,
        user_lookup_sql=LEGACY_LOOKUP_AUTH_USER_BY_ID_SQL,
    )
    op.execute("DROP TABLE public.tenant_ownership")
    op.execute("DROP TABLE public.tenant_membership")

    op.execute("""
        ALTER TABLE public.role
          DROP CONSTRAINT ck_role_protected_kind,
          DROP CONSTRAINT ck_role_version,
          DROP COLUMN version,
          DROP COLUMN protected_kind,
          DROP COLUMN is_protected
        """)
    op.execute("""
        ALTER TABLE public.permission
          DROP CONSTRAINT ck_permission_risk_level,
          DROP CONSTRAINT ck_permission_target_role_type,
          DROP CONSTRAINT ck_permission_scope_type,
          DROP COLUMN requires_confirmation,
          DROP COLUMN requires_step_up,
          DROP COLUMN owner_delegable,
          DROP COLUMN administrator_delegable,
          DROP COLUMN developer_delegable,
          DROP COLUMN owner_grantable,
          DROP COLUMN administrator_grantable,
          DROP COLUMN developer_grantable,
          DROP COLUMN risk_level,
          DROP COLUMN target_role_type,
          DROP COLUMN scope_type
        """)
