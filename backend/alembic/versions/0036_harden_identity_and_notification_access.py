"""security: harden identity and notification access

Revision ID: 0036
Revises: 0035
Create Date: 2026-07-14

The runtime role keeps only a tenant-scoped, non-sensitive employee directory.
Identity mutations, assignment writes, authentication lookups, and notification
writes are exposed through narrow security-definer functions.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0036"
down_revision: str | Sequence[str] | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


APP_USER_SAFE_COLUMNS = (
    "id",
    "email",
    "email_lower",
    "full_name",
    "phone",
    "home_tenant_id",
    "status",
    "last_login_at",
)

INTERNAL_FUNCTIONS = ("public.tenant_actor_has_permission(UUID, TEXT)",)

APP_FUNCTIONS = (
    "public.lookup_auth_user_by_email(TEXT)",
    "public.lookup_auth_user_by_id(UUID, UUID)",
    "public.touch_auth_user_last_login(UUID, TIMESTAMP WITH TIME ZONE)",
    "public.find_invitable_user_id(UUID, TEXT)",
    "public.create_invited_app_user(UUID, TEXT, TEXT)",
    "public.update_tenant_user_profile(UUID, UUID, TEXT, TEXT)",
    "public.set_tenant_user_status(UUID, UUID, TEXT, TIMESTAMP WITH TIME ZONE)",
    "public.create_tenant_user_assignment(UUID, UUID, UUID, UUID, BOOLEAN)",
    "public.reactivate_tenant_user_assignment(UUID, UUID, UUID, BOOLEAN)",
    "public.deactivate_tenant_user_assignment(UUID, UUID)",
    "public.create_scoped_notification(UUID, UUID, TEXT, TEXT, TEXT, JSONB, TEXT)",
    "public.mark_scoped_notification_read(UUID, UUID, TIMESTAMP WITH TIME ZONE)",
    "public.mark_all_scoped_notifications_read(UUID, TIMESTAMP WITH TIME ZONE)",
)


HARDEN_UPDATED_META_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION public.trg_set_updated_meta() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at := pg_catalog.now();
  IF TG_OP = 'UPDATE' AND public.current_app_user_id() IS NOT NULL THEN
    BEGIN
      NEW.updated_by := public.current_app_user_id();
    EXCEPTION WHEN undefined_column THEN
      NULL;
    END;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
"""


HARDEN_CREATED_META_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION public.trg_set_created_meta() RETURNS TRIGGER AS $$
BEGIN
  IF NEW.created_by IS NULL AND public.current_app_user_id() IS NOT NULL THEN
    NEW.created_by := public.current_app_user_id();
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
"""


RESTORE_UPDATED_META_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION public.trg_set_updated_meta() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at := now();
  IF TG_OP = 'UPDATE' AND current_app_user_id() IS NOT NULL THEN
    BEGIN
      NEW.updated_by := current_app_user_id();
    EXCEPTION WHEN undefined_column THEN
      NULL;
    END;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""


RESTORE_CREATED_META_TRIGGER_SQL = """
CREATE OR REPLACE FUNCTION public.trg_set_created_meta() RETURNS TRIGGER AS $$
BEGIN
  IF NEW.created_by IS NULL AND current_app_user_id() IS NOT NULL THEN
    NEW.created_by := current_app_user_id();
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""


TENANT_ACTOR_HAS_PERMISSION_SQL = """
CREATE FUNCTION public.tenant_actor_has_permission(
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
        WHERE assignment.tenant_id = p_tenant_id
          AND assignment.user_id = public.current_app_user_id()
          AND assignment.is_active
          AND role_permission.permission_code = p_permission_code
      )
    )
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LOOKUP_AUTH_USER_BY_EMAIL_SQL = """
CREATE FUNCTION public.lookup_auth_user_by_email(
  p_email TEXT
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
  LIMIT 1
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LOOKUP_AUTH_USER_BY_ID_SQL = """
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
    AND p_user_id IS DISTINCT FROM public.current_app_user_id()
    AND NOT EXISTS (
      SELECT 1
      FROM public.session AS auth_session
      WHERE auth_session.id = p_session_id
        AND auth_session.user_id = p_user_id
        AND auth_session.revoked_at IS NULL
        AND auth_session.expires_at > pg_catalog.now()
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
  WHERE app_user.id = p_user_id;
END;
$$ LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


TOUCH_AUTH_USER_LAST_LOGIN_SQL = """
CREATE FUNCTION public.touch_auth_user_last_login(
  p_user_id UUID,
  p_when TIMESTAMPTZ
) RETURNS VOID AS $$
DECLARE
  v_when TIMESTAMPTZ;
BEGIN
  IF p_when IS NULL THEN
    RAISE EXCEPTION 'Login timestamp is required'
      USING ERRCODE = '22004';
  END IF;

  IF session_user <> 'aurum_support'
    AND p_user_id IS DISTINCT FROM public.current_app_user_id()
    AND NOT EXISTS (
      SELECT 1
      FROM public.session AS auth_session
      WHERE auth_session.user_id = p_user_id
        AND auth_session.revoked_at IS NULL
        AND auth_session.expires_at > pg_catalog.now()
    )
  THEN
    RAISE EXCEPTION 'Authentication user is unavailable'
      USING ERRCODE = '42501';
  END IF;

  v_when := LEAST(p_when, pg_catalog.now());
  UPDATE public.app_user AS app_user
  SET last_login_at = GREATEST(
    COALESCE(app_user.last_login_at, v_when),
    v_when
  )
  WHERE app_user.id = p_user_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


FIND_INVITABLE_USER_ID_SQL = """
CREATE FUNCTION public.find_invitable_user_id(
  p_tenant_id UUID,
  p_email TEXT
) RETURNS UUID AS $$
DECLARE
  v_user_id UUID;
BEGIN
  IF NOT public.tenant_actor_has_permission(
    p_tenant_id,
    'users.invite'
  ) THEN
    RAISE EXCEPTION 'User invitation is not allowed'
      USING ERRCODE = '42501';
  END IF;

  SELECT app_user.id
  INTO v_user_id
  FROM public.app_user AS app_user
  WHERE app_user.email_lower = pg_catalog.lower(pg_catalog.btrim(p_email));

  RETURN v_user_id;
END;
$$ LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


CREATE_INVITED_APP_USER_SQL = """
CREATE FUNCTION public.create_invited_app_user(
  p_tenant_id UUID,
  p_email TEXT,
  p_full_name TEXT
) RETURNS UUID AS $$
DECLARE
  v_user_id UUID;
BEGIN
  IF NOT public.tenant_actor_has_permission(
    p_tenant_id,
    'users.invite'
  ) THEN
    RAISE EXCEPTION 'User invitation is not allowed'
      USING ERRCODE = '42501';
  END IF;

  IF NULLIF(pg_catalog.btrim(p_email), '') IS NULL
    OR NULLIF(pg_catalog.btrim(p_full_name), '') IS NULL
  THEN
    RAISE EXCEPTION 'Email and full name are required'
      USING ERRCODE = '22023';
  END IF;

  INSERT INTO public.app_user (
    email,
    full_name,
    home_tenant_id,
    is_developer,
    is_administrator,
    status
  ) VALUES (
    pg_catalog.btrim(p_email),
    pg_catalog.btrim(p_full_name),
    p_tenant_id,
    false,
    false,
    'invited'
  )
  RETURNING app_user.id INTO v_user_id;

  RETURN v_user_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


UPDATE_TENANT_USER_PROFILE_SQL = """
CREATE FUNCTION public.update_tenant_user_profile(
  p_tenant_id UUID,
  p_user_id UUID,
  p_full_name TEXT,
  p_phone TEXT
) RETURNS BOOLEAN AS $$
DECLARE
  v_updated INTEGER;
BEGIN
  IF NOT public.tenant_actor_has_permission(
    p_tenant_id,
    'users.update'
  ) THEN
    RAISE EXCEPTION 'User profile update is not allowed'
      USING ERRCODE = '42501';
  END IF;

  IF NULLIF(pg_catalog.btrim(p_full_name), '') IS NULL THEN
    RAISE EXCEPTION 'Full name is required'
      USING ERRCODE = '22023';
  END IF;

  UPDATE public.app_user AS app_user
  SET
    full_name = pg_catalog.btrim(p_full_name),
    phone = NULLIF(pg_catalog.btrim(p_phone), '')
  WHERE app_user.id = p_user_id
    AND (
      session_user = 'aurum_support'
      OR EXISTS (
        SELECT 1
        FROM public.user_assignment AS assignment
        WHERE assignment.tenant_id = p_tenant_id
          AND assignment.user_id = app_user.id
      )
    );

  GET DIAGNOSTICS v_updated = ROW_COUNT;
  RETURN v_updated = 1;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


SET_TENANT_USER_STATUS_SQL = """
CREATE FUNCTION public.set_tenant_user_status(
  p_tenant_id UUID,
  p_user_id UUID,
  p_status TEXT,
  p_changed_at TIMESTAMPTZ
) RETURNS BOOLEAN AS $$
DECLARE
  v_actor_id UUID;
  v_actor_level INTEGER;
  v_required_permission TEXT;
  v_target_level INTEGER;
  v_updated INTEGER;
BEGIN
  v_actor_id := public.current_app_user_id();

  IF p_status = 'blocked' THEN
    v_required_permission := 'users.block';
  ELSIF p_status = 'archived' THEN
    v_required_permission := 'users.delete';
  ELSE
    RAISE EXCEPTION 'Unsupported user status transition'
      USING ERRCODE = '22023';
  END IF;

  IF p_changed_at IS NULL THEN
    RAISE EXCEPTION 'Status timestamp is required'
      USING ERRCODE = '22004';
  END IF;

  IF NOT public.tenant_actor_has_permission(
    p_tenant_id,
    v_required_permission
  ) THEN
    RAISE EXCEPTION 'User status update is not allowed'
      USING ERRCODE = '42501';
  END IF;

  IF session_user <> 'aurum_support' THEN
    SELECT pg_catalog.min(assigned_role.level)
    INTO v_actor_level
    FROM public.user_assignment AS assignment
    JOIN public.role AS assigned_role
      ON assigned_role.id = assignment.role_id
     AND assigned_role.is_active
    WHERE assignment.tenant_id = p_tenant_id
      AND assignment.user_id = v_actor_id
      AND assignment.is_active;

    SELECT pg_catalog.min(assigned_role.level)
    INTO v_target_level
    FROM public.user_assignment AS assignment
    JOIN public.role AS assigned_role
      ON assigned_role.id = assignment.role_id
     AND assigned_role.is_active
    WHERE assignment.tenant_id = p_tenant_id
      AND assignment.user_id = p_user_id
      AND assignment.is_active;

    IF v_actor_id IS NULL
      OR p_user_id = v_actor_id
      OR v_actor_level IS NULL
      OR (v_target_level IS NOT NULL AND v_target_level < v_actor_level)
      OR NOT EXISTS (
        SELECT 1
        FROM public.app_user AS app_user
        WHERE app_user.id = p_user_id
          AND app_user.home_tenant_id = p_tenant_id
      )
      OR EXISTS (
        SELECT 1
        FROM public.user_assignment AS assignment
        WHERE assignment.user_id = p_user_id
          AND assignment.tenant_id <> p_tenant_id
          AND assignment.is_active
      )
    THEN
      RAISE EXCEPTION 'User status update is not allowed'
        USING ERRCODE = '42501';
    END IF;
  END IF;

  UPDATE public.app_user AS app_user
  SET
    status = p_status,
    blocked_at = CASE
      WHEN p_status = 'blocked' THEN p_changed_at
      ELSE app_user.blocked_at
    END,
    archived_at = CASE
      WHEN p_status = 'archived' THEN p_changed_at
      ELSE app_user.archived_at
    END
  WHERE app_user.id = p_user_id
    AND (
      session_user = 'aurum_support'
      OR EXISTS (
        SELECT 1
        FROM public.user_assignment AS assignment
        WHERE assignment.tenant_id = p_tenant_id
          AND assignment.user_id = app_user.id
      )
    );

  GET DIAGNOSTICS v_updated = ROW_COUNT;
  IF v_updated = 1 THEN
    UPDATE public.session AS auth_session
    SET
      revoked_at = COALESCE(auth_session.revoked_at, p_changed_at),
      revoked_reason = COALESCE(auth_session.revoked_reason, 'user_' || p_status)
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


CREATE_TENANT_USER_ASSIGNMENT_SQL = """
CREATE FUNCTION public.create_tenant_user_assignment(
  p_tenant_id UUID,
  p_user_id UUID,
  p_branch_id UUID,
  p_role_id UUID,
  p_password_required BOOLEAN
) RETURNS public.user_assignment AS $$
DECLARE
  v_actor_id UUID;
  v_actor_level INTEGER;
  v_assignment public.user_assignment%ROWTYPE;
  v_target_level INTEGER;
BEGIN
  v_actor_id := public.current_app_user_id();

  IF session_user <> 'aurum_support'
    AND NOT (
      public.tenant_actor_has_permission(p_tenant_id, 'users.invite')
      OR public.tenant_actor_has_permission(p_tenant_id, 'roles.assign')
    )
  THEN
    RAISE EXCEPTION 'User assignment creation is not allowed'
      USING ERRCODE = '42501';
  END IF;

  SELECT assigned_role.level
  INTO v_target_level
  FROM public.role AS assigned_role
  WHERE assigned_role.id = p_role_id
    AND assigned_role.is_active
    AND (
      (
        session_user = 'aurum_support'
        AND (assigned_role.tenant_id = p_tenant_id OR assigned_role.is_system)
      )
      OR (
        session_user <> 'aurum_support'
        AND assigned_role.tenant_id = p_tenant_id
        AND NOT assigned_role.is_system
      )
    );

  IF v_target_level IS NULL
    OR NOT EXISTS (
      SELECT 1 FROM public.app_user AS app_user WHERE app_user.id = p_user_id
    )
    OR (
      p_branch_id IS NOT NULL
      AND NOT EXISTS (
        SELECT 1
        FROM public.branch AS branch
        WHERE branch.id = p_branch_id
          AND branch.tenant_id = p_tenant_id
          AND branch.is_active
      )
    )
  THEN
    RAISE EXCEPTION 'User assignment target is unavailable'
      USING ERRCODE = '42501';
  END IF;

  IF session_user <> 'aurum_support' THEN
    SELECT pg_catalog.min(assigned_role.level)
    INTO v_actor_level
    FROM public.user_assignment AS assignment
    JOIN public.role AS assigned_role
      ON assigned_role.id = assignment.role_id
     AND assigned_role.is_active
    WHERE assignment.tenant_id = p_tenant_id
      AND assignment.user_id = v_actor_id
      AND assignment.is_active;

    IF v_actor_level IS NULL OR v_target_level < v_actor_level THEN
      RAISE EXCEPTION 'User assignment exceeds actor privileges'
        USING ERRCODE = '42501';
    END IF;
  END IF;

  INSERT INTO public.user_assignment (
    user_id,
    tenant_id,
    branch_id,
    role_id,
    password_required,
    created_by,
    updated_by
  ) VALUES (
    p_user_id,
    p_tenant_id,
    p_branch_id,
    p_role_id,
    p_password_required,
    v_actor_id,
    v_actor_id
  )
  RETURNING * INTO v_assignment;

  RETURN v_assignment;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


REACTIVATE_TENANT_USER_ASSIGNMENT_SQL = """
CREATE FUNCTION public.reactivate_tenant_user_assignment(
  p_tenant_id UUID,
  p_assignment_id UUID,
  p_role_id UUID,
  p_password_required BOOLEAN
) RETURNS public.user_assignment AS $$
DECLARE
  v_actor_id UUID;
  v_actor_level INTEGER;
  v_assignment public.user_assignment%ROWTYPE;
  v_target_level INTEGER;
BEGIN
  v_actor_id := public.current_app_user_id();

  IF session_user <> 'aurum_support'
    AND NOT (
      public.tenant_actor_has_permission(p_tenant_id, 'users.invite')
      OR public.tenant_actor_has_permission(p_tenant_id, 'roles.assign')
    )
  THEN
    RAISE EXCEPTION 'User assignment reactivation is not allowed'
      USING ERRCODE = '42501';
  END IF;

  SELECT *
  INTO v_assignment
  FROM public.user_assignment AS assignment
  WHERE assignment.id = p_assignment_id
    AND assignment.tenant_id = p_tenant_id
    AND NOT assignment.is_active
  FOR UPDATE;

  SELECT assigned_role.level
  INTO v_target_level
  FROM public.role AS assigned_role
  WHERE assigned_role.id = p_role_id
    AND assigned_role.is_active
    AND (
      (
        session_user = 'aurum_support'
        AND (assigned_role.tenant_id = p_tenant_id OR assigned_role.is_system)
      )
      OR (
        session_user <> 'aurum_support'
        AND assigned_role.tenant_id = p_tenant_id
        AND NOT assigned_role.is_system
      )
    );

  IF v_assignment.id IS NULL OR v_target_level IS NULL THEN
    RAISE EXCEPTION 'User assignment target is unavailable'
      USING ERRCODE = '42501';
  END IF;

  IF session_user <> 'aurum_support' THEN
    SELECT pg_catalog.min(assigned_role.level)
    INTO v_actor_level
    FROM public.user_assignment AS assignment
    JOIN public.role AS assigned_role
      ON assigned_role.id = assignment.role_id
     AND assigned_role.is_active
    WHERE assignment.tenant_id = p_tenant_id
      AND assignment.user_id = v_actor_id
      AND assignment.is_active;

    IF v_actor_level IS NULL OR v_target_level < v_actor_level THEN
      RAISE EXCEPTION 'User assignment exceeds actor privileges'
        USING ERRCODE = '42501';
    END IF;
  END IF;

  UPDATE public.user_assignment AS assignment
  SET
    is_active = true,
    role_id = p_role_id,
    password_required = p_password_required,
    updated_by = v_actor_id
  WHERE assignment.id = p_assignment_id
  RETURNING * INTO v_assignment;

  RETURN v_assignment;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


DEACTIVATE_TENANT_USER_ASSIGNMENT_SQL = """
CREATE FUNCTION public.deactivate_tenant_user_assignment(
  p_tenant_id UUID,
  p_assignment_id UUID
) RETURNS INTEGER AS $$
DECLARE
  v_actor_id UUID;
  v_actor_level INTEGER;
  v_target_level INTEGER;
  v_updated INTEGER;
BEGIN
  v_actor_id := public.current_app_user_id();

  IF session_user <> 'aurum_support'
    AND NOT public.tenant_actor_has_permission(
      p_tenant_id,
      'roles.assign'
    )
  THEN
    RAISE EXCEPTION 'User assignment revocation is not allowed'
      USING ERRCODE = '42501';
  END IF;

  SELECT assigned_role.level
  INTO v_target_level
  FROM public.user_assignment AS assignment
  JOIN public.role AS assigned_role ON assigned_role.id = assignment.role_id
  WHERE assignment.id = p_assignment_id
    AND assignment.tenant_id = p_tenant_id;

  IF v_target_level IS NULL THEN
    RETURN 0;
  END IF;

  IF session_user <> 'aurum_support' THEN
    SELECT pg_catalog.min(assigned_role.level)
    INTO v_actor_level
    FROM public.user_assignment AS assignment
    JOIN public.role AS assigned_role
      ON assigned_role.id = assignment.role_id
     AND assigned_role.is_active
    WHERE assignment.tenant_id = p_tenant_id
      AND assignment.user_id = v_actor_id
      AND assignment.is_active;

    IF v_actor_level IS NULL OR v_target_level < v_actor_level THEN
      RAISE EXCEPTION 'User assignment exceeds actor privileges'
        USING ERRCODE = '42501';
    END IF;
  END IF;

  UPDATE public.user_assignment AS assignment
  SET is_active = false, updated_by = v_actor_id
  WHERE assignment.id = p_assignment_id
    AND assignment.tenant_id = p_tenant_id
    AND assignment.is_active;

  GET DIAGNOSTICS v_updated = ROW_COUNT;
  RETURN v_updated;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


CREATE_SCOPED_NOTIFICATION_SQL = """
CREATE FUNCTION public.create_scoped_notification(
  p_tenant_id UUID,
  p_user_id UUID,
  p_event_type TEXT,
  p_title TEXT,
  p_body TEXT,
  p_data JSONB,
  p_severity TEXT
) RETURNS public.notification AS $$
DECLARE
  v_notification public.notification%ROWTYPE;
  v_actor_id UUID;
BEGIN
  IF NULLIF(pg_catalog.btrim(p_event_type), '') IS NULL
    OR NULLIF(pg_catalog.btrim(p_title), '') IS NULL
    OR p_severity NOT IN ('info', 'warning', 'error', 'critical')
  THEN
    RAISE EXCEPTION 'Invalid notification payload'
      USING ERRCODE = '22023';
  END IF;

  IF session_user <> 'aurum_support' THEN
    v_actor_id := public.current_app_user_id();
    IF p_tenant_id IS NULL
      OR p_tenant_id IS DISTINCT FROM public.current_tenant_id()
      OR v_actor_id IS NULL
      OR NOT EXISTS (
        SELECT 1
        FROM public.user_assignment AS assignment
        WHERE assignment.tenant_id = p_tenant_id
          AND assignment.user_id = v_actor_id
          AND assignment.is_active
      )
      OR NOT EXISTS (
        SELECT 1
        FROM public.user_assignment AS assignment
        WHERE assignment.tenant_id = p_tenant_id
          AND assignment.user_id = p_user_id
          AND assignment.is_active
      )
    THEN
      RAISE EXCEPTION 'Notification recipient is outside the active tenant'
        USING ERRCODE = '42501';
    END IF;
  END IF;

  INSERT INTO public.notification (
    tenant_id,
    user_id,
    event_type,
    title,
    body,
    data,
    severity
  ) VALUES (
    p_tenant_id,
    p_user_id,
    pg_catalog.btrim(p_event_type),
    pg_catalog.btrim(p_title),
    p_body,
    p_data,
    p_severity
  )
  RETURNING * INTO v_notification;

  RETURN v_notification;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


MARK_SCOPED_NOTIFICATION_READ_SQL = """
CREATE FUNCTION public.mark_scoped_notification_read(
  p_notification_id UUID,
  p_user_id UUID,
  p_when TIMESTAMPTZ
) RETURNS INTEGER AS $$
DECLARE
  v_updated INTEGER;
BEGIN
  IF p_when IS NULL THEN
    RAISE EXCEPTION 'Read timestamp is required'
      USING ERRCODE = '22004';
  END IF;

  IF session_user <> 'aurum_support'
    AND p_user_id IS DISTINCT FROM public.current_app_user_id()
  THEN
    RAISE EXCEPTION 'Notification is unavailable'
      USING ERRCODE = '42501';
  END IF;

  UPDATE public.notification AS notification
  SET read_at = p_when
  WHERE notification.id = p_notification_id
    AND notification.user_id = p_user_id
    AND notification.read_at IS NULL
    AND (
      session_user = 'aurum_support'
      OR notification.tenant_id = public.current_tenant_id()
    );

  GET DIAGNOSTICS v_updated = ROW_COUNT;
  RETURN v_updated;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


MARK_ALL_SCOPED_NOTIFICATIONS_READ_SQL = """
CREATE FUNCTION public.mark_all_scoped_notifications_read(
  p_user_id UUID,
  p_when TIMESTAMPTZ
) RETURNS INTEGER AS $$
DECLARE
  v_updated INTEGER;
BEGIN
  IF p_when IS NULL THEN
    RAISE EXCEPTION 'Read timestamp is required'
      USING ERRCODE = '22004';
  END IF;

  IF session_user <> 'aurum_support'
    AND p_user_id IS DISTINCT FROM public.current_app_user_id()
  THEN
    RAISE EXCEPTION 'Notifications are unavailable'
      USING ERRCODE = '42501';
  END IF;

  UPDATE public.notification AS notification
  SET read_at = p_when
  WHERE notification.user_id = p_user_id
    AND notification.read_at IS NULL
    AND (
      session_user = 'aurum_support'
      OR notification.tenant_id = public.current_tenant_id()
    );

  GET DIAGNOSTICS v_updated = ROW_COUNT;
  RETURN v_updated;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


def _function_guard_sql() -> str:
    app_functions = ",\n      ".join(f"'{function}'::REGPROCEDURE" for function in APP_FUNCTIONS)
    internal_functions = ",\n      ".join(
        f"'{function}'::REGPROCEDURE" for function in INTERNAL_FUNCTIONS
    )
    safe_columns = ", ".join(f"'{column}'" for column in APP_USER_SAFE_COLUMNS)
    return f"""
    DO $$
    DECLARE
      v_bad_column TEXT;
      v_function REGPROCEDURE;
      v_owner TEXT;
      v_security_definer BOOLEAN;
      v_settings TEXT[];
    BEGIN
      IF NOT (
        SELECT relations.relrowsecurity
        FROM pg_catalog.pg_class AS relations
        WHERE relations.oid = 'public.app_user'::REGCLASS
      ) OR NOT (
        SELECT relations.relrowsecurity
        FROM pg_catalog.pg_class AS relations
        WHERE relations.oid = 'public.notification'::REGCLASS
      ) THEN
        RAISE EXCEPTION 'Identity and notification RLS must remain enabled';
      END IF;

      IF EXISTS (
        SELECT 1
        FROM pg_catalog.unnest(ARRAY[
          'SELECT', 'INSERT', 'UPDATE', 'DELETE',
          'TRUNCATE', 'REFERENCES', 'TRIGGER'
        ]) AS checks(privilege)
        WHERE pg_catalog.has_table_privilege(
          'aurum_app', 'public.app_user', checks.privilege
        )
          OR pg_catalog.has_table_privilege(
            'aurum_app',
            'public.app_user',
            checks.privilege || ' WITH GRANT OPTION'
          )
      ) THEN
        RAISE EXCEPTION 'aurum_app must not have table-level app_user access';
      END IF;

      SELECT attributes.attname
      INTO v_bad_column
      FROM pg_catalog.pg_attribute AS attributes
      WHERE attributes.attrelid = 'public.app_user'::REGCLASS
        AND attributes.attnum > 0
        AND NOT attributes.attisdropped
        AND pg_catalog.has_column_privilege(
          'aurum_app',
          'public.app_user',
          attributes.attname,
          'SELECT'
        ) IS DISTINCT FROM (
          attributes.attname = ANY(ARRAY[{safe_columns}])
        )
      LIMIT 1;

      IF v_bad_column IS NOT NULL THEN
        RAISE EXCEPTION 'Unexpected app_user column privilege on %', v_bad_column;
      END IF;

      IF NOT pg_catalog.has_table_privilege(
        'aurum_app', 'public.user_assignment', 'SELECT'
      ) OR pg_catalog.has_table_privilege(
        'aurum_app', 'public.user_assignment', 'INSERT'
      ) OR pg_catalog.has_table_privilege(
        'aurum_app', 'public.user_assignment', 'UPDATE'
      ) OR pg_catalog.has_table_privilege(
        'aurum_app', 'public.user_assignment', 'DELETE'
      ) OR NOT pg_catalog.has_table_privilege(
        'aurum_app', 'public.notification', 'SELECT'
      ) OR pg_catalog.has_table_privilege(
        'aurum_app', 'public.notification', 'INSERT'
      ) OR pg_catalog.has_table_privilege(
        'aurum_app', 'public.notification', 'UPDATE'
      ) OR pg_catalog.has_table_privilege(
        'aurum_app', 'public.notification', 'DELETE'
      ) THEN
        RAISE EXCEPTION 'Unexpected assignment or notification privileges';
      END IF;

      FOREACH v_function IN ARRAY ARRAY[
        {app_functions}
      ] LOOP
        SELECT
          pg_catalog.pg_get_userbyid(routines.proowner),
          routines.prosecdef,
          routines.proconfig
        INTO v_owner, v_security_definer, v_settings
        FROM pg_catalog.pg_proc AS routines
        WHERE routines.oid = v_function;

        IF v_owner IS DISTINCT FROM 'aurum_support'
          OR NOT v_security_definer
          OR NOT COALESCE(
            v_settings @> ARRAY['search_path=pg_catalog, pg_temp'],
            false
          )
          OR NOT pg_catalog.has_function_privilege(
            'aurum_app', v_function, 'EXECUTE'
          )
          OR pg_catalog.has_function_privilege(
            'aurum_app', v_function, 'EXECUTE WITH GRANT OPTION'
          )
          OR EXISTS (
            SELECT 1
            FROM pg_catalog.pg_proc AS routines
            CROSS JOIN LATERAL pg_catalog.aclexplode(
              COALESCE(
                routines.proacl,
                pg_catalog.acldefault('f'::"char", routines.proowner)
              )
            ) AS privileges
            LEFT JOIN pg_catalog.pg_roles AS grantees
              ON grantees.oid = privileges.grantee
            WHERE routines.oid = v_function
              AND privileges.privilege_type = 'EXECUTE'
              AND (
                privileges.grantee = 0
                OR grantees.rolname NOT IN ('aurum_app', 'aurum_support')
              )
          )
        THEN
          RAISE EXCEPTION 'Unsafe function privileges for %', v_function;
        END IF;
      END LOOP;

      FOREACH v_function IN ARRAY ARRAY[
        {internal_functions}
      ] LOOP
        IF pg_catalog.has_function_privilege(
          'aurum_app', v_function, 'EXECUTE'
        ) OR EXISTS (
          SELECT 1
          FROM pg_catalog.pg_proc AS routines
          CROSS JOIN LATERAL pg_catalog.aclexplode(
            COALESCE(
              routines.proacl,
              pg_catalog.acldefault('f'::"char", routines.proowner)
            )
          ) AS privileges
          WHERE routines.oid = v_function
            AND privileges.grantee = 0
            AND privileges.privilege_type = 'EXECUTE'
        ) THEN
          RAISE EXCEPTION 'Internal function % must remain private', v_function;
        END IF;
      END LOOP;
    END
    $$
    """


def _configure_function_acl(function: str, *, app_access: bool) -> None:
    op.execute(f"ALTER FUNCTION {function} OWNER TO aurum_support")
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {function} FROM PUBLIC, aurum_app")
    op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO aurum_support")
    if app_access:
        op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO aurum_app")


def upgrade() -> None:
    op.execute(HARDEN_UPDATED_META_TRIGGER_SQL)
    op.execute(HARDEN_CREATED_META_TRIGGER_SQL)

    op.execute(TENANT_ACTOR_HAS_PERMISSION_SQL)
    op.execute(LOOKUP_AUTH_USER_BY_EMAIL_SQL)
    op.execute(LOOKUP_AUTH_USER_BY_ID_SQL)
    op.execute(TOUCH_AUTH_USER_LAST_LOGIN_SQL)
    op.execute(FIND_INVITABLE_USER_ID_SQL)
    op.execute(CREATE_INVITED_APP_USER_SQL)
    op.execute(UPDATE_TENANT_USER_PROFILE_SQL)
    op.execute(SET_TENANT_USER_STATUS_SQL)
    op.execute(CREATE_TENANT_USER_ASSIGNMENT_SQL)
    op.execute(REACTIVATE_TENANT_USER_ASSIGNMENT_SQL)
    op.execute(DEACTIVATE_TENANT_USER_ASSIGNMENT_SQL)
    op.execute(CREATE_SCOPED_NOTIFICATION_SQL)
    op.execute(MARK_SCOPED_NOTIFICATION_READ_SQL)
    op.execute(MARK_ALL_SCOPED_NOTIFICATIONS_READ_SQL)

    op.execute("ALTER TABLE public.app_user ENABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS app_user_directory_read ON public.app_user")
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

    op.execute("DROP POLICY tenant_isolation ON public.notification")
    op.execute("""
        CREATE POLICY notification_user_read ON public.notification
          FOR SELECT TO aurum_app
          USING (
            tenant_id = public.current_tenant_id()
            AND user_id = public.current_app_user_id()
          )
        """)

    op.execute("REVOKE ALL PRIVILEGES ON TABLE public.app_user FROM aurum_app")
    safe_columns = ", ".join(APP_USER_SAFE_COLUMNS)
    op.execute(f"GRANT SELECT ({safe_columns}) ON TABLE public.app_user TO aurum_app")
    op.execute("REVOKE ALL PRIVILEGES ON TABLE public.user_assignment FROM aurum_app")
    op.execute("GRANT SELECT ON TABLE public.user_assignment TO aurum_app")
    op.execute("REVOKE ALL PRIVILEGES ON TABLE public.notification FROM aurum_app")
    op.execute("GRANT SELECT ON TABLE public.notification TO aurum_app")

    for function in INTERNAL_FUNCTIONS:
        _configure_function_acl(function, app_access=False)
    for function in APP_FUNCTIONS:
        _configure_function_acl(function, app_access=True)

    op.execute(_function_guard_sql())


def downgrade() -> None:
    for function in reversed(APP_FUNCTIONS):
        op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {function} FROM aurum_app")
        op.execute(f"DROP FUNCTION {function}")
    for function in reversed(INTERNAL_FUNCTIONS):
        op.execute(f"DROP FUNCTION {function}")

    op.execute("DROP POLICY notification_user_read ON public.notification")
    op.execute("""
        CREATE POLICY tenant_isolation ON public.notification
          USING (
            tenant_id = public.current_tenant_id()
            OR public.is_support_session()
          )
        """)
    op.execute("DROP POLICY app_user_directory_read ON public.app_user")
    op.execute("ALTER TABLE public.app_user DISABLE ROW LEVEL SECURITY")

    op.execute("REVOKE ALL PRIVILEGES ON TABLE public.app_user FROM aurum_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.app_user TO aurum_app")
    op.execute("REVOKE ALL PRIVILEGES ON TABLE public.user_assignment FROM aurum_app")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE " "ON TABLE public.user_assignment TO aurum_app"
    )
    op.execute("REVOKE ALL PRIVILEGES ON TABLE public.notification FROM aurum_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.notification TO aurum_app")

    op.execute(RESTORE_UPDATED_META_TRIGGER_SQL)
    op.execute("ALTER FUNCTION public.trg_set_updated_meta() RESET search_path")
    op.execute(RESTORE_CREATED_META_TRIGGER_SQL)
    op.execute("ALTER FUNCTION public.trg_set_created_meta() RESET search_path")
