"""add secure platform staff lifecycle operations

Revision ID: 0089
Revises: 0088
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0089"
down_revision: str | None = "0088"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ACTOR_HAS_CAPABILITY_SQL = """
CREATE OR REPLACE FUNCTION public.platform_actor_has_capability(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_capability TEXT
)
RETURNS BOOLEAN AS $$
BEGIN
  RETURN public.current_app_user_id() = p_actor_user_id
    AND public.current_tenant_id() IS NULL
    AND public.is_support_session()
    AND NULLIF(pg_catalog.current_setting('app.auth_session_id', true), '')::UUID
      = p_actor_session_id
    AND EXISTS (
      SELECT 1
      FROM public.session AS auth_session
      JOIN public.app_user AS actor
        ON actor.id = auth_session.user_id
       AND actor.status = 'active'
       AND (actor.is_developer OR actor.is_administrator)
      JOIN public.platform_access_grant AS actor_grant
        ON actor_grant.user_id = actor.id
       AND actor_grant.status = 'active'
      JOIN public.platform_access_grant_permission AS assignment
        ON assignment.grant_id = actor_grant.id
       AND assignment.permission_code = p_capability
      JOIN public.permission AS permission
        ON permission.code = assignment.permission_code
       AND permission.is_active
       AND permission.scope_type = 'PLATFORM'
       AND permission.target_role_type = 'platform'
      WHERE auth_session.id = p_actor_session_id
        AND auth_session.user_id = p_actor_user_id
        AND auth_session.revoked_at IS NULL
        AND auth_session.expires_at > pg_catalog.statement_timestamp()
        AND (
          p_capability <> 'platform.accounts.manage'
          OR (
            auth_session.mfa_verified_at IS NOT NULL
            AND auth_session.mfa_verified_at
              >= pg_catalog.statement_timestamp() - INTERVAL '15 minutes'
            AND auth_session.mfa_verified_at
              <= pg_catalog.statement_timestamp() + INTERVAL '1 minute'
          )
        )
    );
END;
$$ LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
"""


ACTOR_HAS_RECENT_CAPABILITY_SQL = """
CREATE FUNCTION public.platform_actor_has_recent_capability(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_capability TEXT
)
RETURNS BOOLEAN AS $$
BEGIN
  RETURN public.platform_actor_has_capability(
    p_actor_user_id,
    p_actor_session_id,
    p_capability
  )
    AND EXISTS (
      SELECT 1
      FROM public.session AS auth_session
      WHERE auth_session.id = p_actor_session_id
        AND auth_session.user_id = p_actor_user_id
        AND auth_session.mfa_verified_at IS NOT NULL
        AND auth_session.mfa_verified_at
          >= pg_catalog.statement_timestamp() - INTERVAL '15 minutes'
        AND auth_session.mfa_verified_at
          <= pg_catalog.statement_timestamp() + INTERVAL '1 minute'
    );
END;
$$ LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
"""


REINVITE_SQL = """
CREATE FUNCTION public.reinvite_platform_staff_account(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_target_user_id UUID,
  p_expected_version INTEGER,
  p_operation_id UUID,
  p_reason_code TEXT,
  p_reason TEXT,
  p_token_hash TEXT,
  p_expires_at TIMESTAMPTZ
)
RETURNS TABLE(
  user_id UUID,
  email TEXT,
  full_name TEXT,
  status TEXT,
  version INTEGER,
  invited_at TIMESTAMPTZ,
  invitation_expires_at TIMESTAMPTZ,
  activated_at TIMESTAMPTZ,
  blocked_at TIMESTAMPTZ,
  offboarded_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ,
  applied BOOLEAN
) AS $$
DECLARE
  v_now TIMESTAMPTZ := pg_catalog.statement_timestamp();
  v_profile_status TEXT;
  v_profile_version INTEGER;
  v_account_status TEXT;
  v_account_email TEXT;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(7148, 1);

  IF NOT public.platform_actor_has_recent_capability(
    p_actor_user_id,
    p_actor_session_id,
    'platform.accounts.manage'
  ) THEN
    RAISE EXCEPTION 'Recent platform account management capability required'
      USING ERRCODE = '42501';
  END IF;

  IF p_target_user_id = p_actor_user_id THEN
    RAISE EXCEPTION 'Platform account cannot reinvite itself'
      USING ERRCODE = '42501';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM public.platform_staff_account_event AS event
    WHERE event.operation_id = p_operation_id
      AND event.user_id = p_target_user_id
      AND event.event_type = 'reinvited'
  ) THEN
    RETURN QUERY
    SELECT
      account.id, account.email, account.full_name, profile.status,
      profile.version, profile.invited_at, profile.invitation_expires_at,
      profile.activated_at, profile.blocked_at, profile.offboarded_at,
      profile.created_at, profile.updated_at, false
    FROM public.platform_staff_account AS profile
    JOIN public.app_user AS account ON account.id = profile.user_id
    WHERE profile.user_id = p_target_user_id;
    RETURN;
  ELSIF EXISTS (
    SELECT 1 FROM public.platform_staff_account_event AS event
    WHERE event.operation_id = p_operation_id
  ) THEN
    RAISE EXCEPTION 'Operation identifier is already used'
      USING ERRCODE = '23505';
  END IF;

  IF p_expected_version < 1
    OR p_operation_id IS NULL
    OR p_reason_code NOT IN (
      'invitation_delivery', 'responsibility_change', 'security_incident',
      'access_review', 'employment_ended', 'other'
    )
    OR pg_catalog.char_length(pg_catalog.btrim(p_reason)) NOT BETWEEN 10 AND 500
    OR p_token_hash !~ '^[0-9a-f]{64}$'
    OR p_expires_at <= v_now
    OR p_expires_at > v_now + INTERVAL '48 hours'
  THEN
    RAISE EXCEPTION 'Invalid platform reinvitation data'
      USING ERRCODE = '22023';
  END IF;

  SELECT profile.status, profile.version, account.status, account.email
  INTO v_profile_status, v_profile_version, v_account_status, v_account_email
  FROM public.platform_staff_account AS profile
  JOIN public.app_user AS account ON account.id = profile.user_id
  WHERE profile.user_id = p_target_user_id
  FOR UPDATE OF profile, account;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Platform staff account not found'
      USING ERRCODE = 'P0002';
  END IF;
  IF v_profile_version <> p_expected_version THEN
    RETURN;
  END IF;
  IF v_profile_status <> 'invited' OR v_account_status <> 'invited' THEN
    RAISE EXCEPTION 'Only an invited platform account can be reinvited'
      USING ERRCODE = '55000';
  END IF;

  UPDATE public.email_code
  SET used_at = COALESCE(used_at, v_now)
  WHERE email_lower = pg_catalog.lower(v_account_email)
    AND used_at IS NULL;

  UPDATE public.platform_staff_account AS profile
  SET
    version = profile.version + 1,
    invited_by = p_actor_user_id,
    invited_at = v_now,
    invitation_token_hash = p_token_hash,
    invitation_expires_at = p_expires_at,
    updated_at = v_now
  WHERE profile.user_id = p_target_user_id;

  UPDATE public.app_user
  SET invited_at = v_now, updated_at = v_now
  WHERE id = p_target_user_id;

  INSERT INTO public.platform_staff_account_event (
    user_id, actor_user_id, event_type, account_version,
    operation_id, reason_code, reason, created_at
  ) VALUES (
    p_target_user_id, p_actor_user_id, 'reinvited', v_profile_version + 1,
    p_operation_id, p_reason_code, pg_catalog.btrim(p_reason), v_now
  );

  RETURN QUERY
  SELECT
    account.id, account.email, account.full_name, profile.status,
    profile.version, profile.invited_at, profile.invitation_expires_at,
    profile.activated_at, profile.blocked_at, profile.offboarded_at,
    profile.created_at, profile.updated_at, true
  FROM public.platform_staff_account AS profile
  JOIN public.app_user AS account ON account.id = profile.user_id
  WHERE profile.user_id = p_target_user_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
"""


CHANGE_STATUS_SQL = """
CREATE FUNCTION public.change_platform_staff_account_status(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_target_user_id UUID,
  p_expected_version INTEGER,
  p_operation_id UUID,
  p_action TEXT,
  p_reason_code TEXT,
  p_reason TEXT
)
RETURNS TABLE(
  user_id UUID,
  email TEXT,
  full_name TEXT,
  status TEXT,
  version INTEGER,
  invited_at TIMESTAMPTZ,
  invitation_expires_at TIMESTAMPTZ,
  activated_at TIMESTAMPTZ,
  blocked_at TIMESTAMPTZ,
  offboarded_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ,
  applied BOOLEAN
) AS $$
DECLARE
  v_now TIMESTAMPTZ := pg_catalog.statement_timestamp();
  v_event_type TEXT;
  v_profile_status TEXT;
  v_profile_version INTEGER;
  v_account_status TEXT;
  v_account_email TEXT;
BEGIN
  PERFORM pg_catalog.pg_advisory_xact_lock(7148, 1);

  IF NOT public.platform_actor_has_recent_capability(
    p_actor_user_id,
    p_actor_session_id,
    'platform.accounts.manage'
  ) THEN
    RAISE EXCEPTION 'Recent platform account management capability required'
      USING ERRCODE = '42501';
  END IF;

  IF p_target_user_id = p_actor_user_id THEN
    RAISE EXCEPTION 'Platform account cannot change its own lifecycle state'
      USING ERRCODE = '42501';
  END IF;

  v_event_type := CASE p_action
    WHEN 'block' THEN 'blocked'
    WHEN 'unblock' THEN 'unblocked'
    WHEN 'offboard' THEN 'offboarded'
    ELSE NULL
  END;

  IF v_event_type IS NULL
    OR p_expected_version < 1
    OR p_operation_id IS NULL
    OR p_reason_code NOT IN (
      'invitation_delivery', 'responsibility_change', 'security_incident',
      'access_review', 'employment_ended', 'other'
    )
    OR pg_catalog.char_length(pg_catalog.btrim(p_reason)) NOT BETWEEN 10 AND 500
  THEN
    RAISE EXCEPTION 'Invalid platform lifecycle request'
      USING ERRCODE = '22023';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM public.platform_staff_account_event AS event
    WHERE event.operation_id = p_operation_id
      AND event.user_id = p_target_user_id
      AND event.event_type = v_event_type
  ) THEN
    RETURN QUERY
    SELECT
      account.id, account.email, account.full_name, profile.status,
      profile.version, profile.invited_at, profile.invitation_expires_at,
      profile.activated_at, profile.blocked_at, profile.offboarded_at,
      profile.created_at, profile.updated_at, false
    FROM public.platform_staff_account AS profile
    JOIN public.app_user AS account ON account.id = profile.user_id
    WHERE profile.user_id = p_target_user_id;
    RETURN;
  ELSIF EXISTS (
    SELECT 1 FROM public.platform_staff_account_event AS event
    WHERE event.operation_id = p_operation_id
  ) THEN
    RAISE EXCEPTION 'Operation identifier is already used'
      USING ERRCODE = '23505';
  END IF;

  SELECT profile.status, profile.version, account.status, account.email
  INTO v_profile_status, v_profile_version, v_account_status, v_account_email
  FROM public.platform_staff_account AS profile
  JOIN public.app_user AS account ON account.id = profile.user_id
  WHERE profile.user_id = p_target_user_id
  FOR UPDATE OF profile, account;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Platform staff account not found'
      USING ERRCODE = 'P0002';
  END IF;
  IF v_profile_version <> p_expected_version THEN
    RETURN;
  END IF;

  IF EXISTS (
    SELECT 1
    FROM public.platform_access_grant AS grant_history
    WHERE grant_history.user_id = p_target_user_id
  ) AND NOT EXISTS (
    SELECT 1
    FROM public.platform_access_grant AS actor_grant
    JOIN public.app_user AS actor ON actor.id = actor_grant.user_id
    WHERE actor_grant.user_id = p_actor_user_id
      AND actor_grant.access_kind = 'developer'
      AND actor_grant.status = 'active'
      AND actor.status = 'active'
  ) THEN
    RAISE EXCEPTION 'Developer required for a privileged platform account'
      USING ERRCODE = '42501';
  END IF;

  IF p_action = 'block' THEN
    IF v_profile_status <> 'active' OR v_account_status <> 'active' THEN
      RAISE EXCEPTION 'Only an active platform account can be blocked'
        USING ERRCODE = '55000';
    END IF;
    UPDATE public.app_user
    SET status = 'blocked', blocked_at = v_now, updated_at = v_now
    WHERE id = p_target_user_id;
    UPDATE public.platform_staff_account AS profile
    SET status = 'blocked', version = profile.version + 1, blocked_at = v_now,
        invitation_token_hash = NULL, invitation_expires_at = NULL,
        updated_at = v_now
    WHERE profile.user_id = p_target_user_id;
  ELSIF p_action = 'unblock' THEN
    IF v_profile_status <> 'blocked' OR v_account_status <> 'blocked' THEN
      RAISE EXCEPTION 'Only a blocked platform account can be unblocked'
        USING ERRCODE = '55000';
    END IF;
    UPDATE public.app_user
    SET status = 'active', blocked_at = NULL, updated_at = v_now
    WHERE id = p_target_user_id;
    UPDATE public.platform_staff_account AS profile
    SET status = 'active', version = profile.version + 1, blocked_at = NULL,
        updated_at = v_now
    WHERE profile.user_id = p_target_user_id;
  ELSE
    IF NOT (
      (v_profile_status = 'invited' AND v_account_status = 'invited')
      OR (v_profile_status = 'active' AND v_account_status = 'active')
      OR (v_profile_status = 'blocked' AND v_account_status = 'blocked')
    ) THEN
      RAISE EXCEPTION 'Platform account cannot be offboarded from its current state'
        USING ERRCODE = '55000';
    END IF;
    UPDATE public.app_user
    SET status = 'archived', archived_at = v_now, updated_at = v_now
    WHERE id = p_target_user_id;
    UPDATE public.platform_staff_account AS profile
    SET status = 'offboarded', version = profile.version + 1,
        invitation_token_hash = NULL, invitation_expires_at = NULL,
        offboarded_at = v_now, updated_at = v_now
    WHERE profile.user_id = p_target_user_id;
  END IF;

  IF p_action IN ('block', 'offboard') THEN
    UPDATE public.session AS auth_session
    SET revoked_at = COALESCE(auth_session.revoked_at, v_now),
        revoked_reason = COALESCE(auth_session.revoked_reason, 'platform_account_disabled'),
        last_used_at = v_now
    WHERE auth_session.user_id = p_target_user_id
      AND auth_session.revoked_at IS NULL;

    UPDATE public.support_access_session AS support_session
    SET revoked_at = COALESCE(support_session.revoked_at, v_now),
        revoked_by_user_id = COALESCE(support_session.revoked_by_user_id, p_actor_user_id),
        updated_at = v_now,
        updated_by = COALESCE(support_session.updated_by, p_actor_user_id)
    WHERE support_session.actor_user_id = p_target_user_id
      AND support_session.revoked_at IS NULL;

    UPDATE public.email_code
    SET used_at = COALESCE(used_at, v_now)
    WHERE email_lower = pg_catalog.lower(v_account_email)
      AND used_at IS NULL;

    UPDATE public.auth_mfa_challenge AS challenge
    SET consumed_at = COALESCE(challenge.consumed_at, v_now)
    WHERE challenge.user_id = p_target_user_id
      AND challenge.consumed_at IS NULL;
  END IF;

  INSERT INTO public.platform_staff_account_event (
    user_id, actor_user_id, event_type, account_version,
    operation_id, reason_code, reason, created_at
  ) VALUES (
    p_target_user_id, p_actor_user_id, v_event_type, v_profile_version + 1,
    p_operation_id, p_reason_code, pg_catalog.btrim(p_reason), v_now
  );

  RETURN QUERY
  SELECT
    account.id, account.email, account.full_name, profile.status,
    profile.version, profile.invited_at, profile.invitation_expires_at,
    profile.activated_at, profile.blocked_at, profile.offboarded_at,
    profile.created_at, profile.updated_at, true
  FROM public.platform_staff_account AS profile
  JOIN public.app_user AS account ON account.id = profile.user_id
  WHERE profile.user_id = p_target_user_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
"""


STATUS_CONSISTENCY_SQL = """
CREATE FUNCTION public.trg_validate_platform_staff_status_consistency()
RETURNS TRIGGER AS $$
DECLARE
  v_user_id UUID;
  v_profile_status TEXT;
  v_account_status TEXT;
BEGIN
  IF TG_TABLE_NAME = 'app_user' THEN
    v_user_id := NEW.id;
  ELSE
    v_user_id := NEW.user_id;
  END IF;

  SELECT profile.status, account.status
  INTO v_profile_status, v_account_status
  FROM public.platform_staff_account AS profile
  JOIN public.app_user AS account ON account.id = profile.user_id
  WHERE profile.user_id = v_user_id;

  IF NOT FOUND THEN
    RETURN NEW;
  END IF;
  IF v_account_status IS DISTINCT FROM (
    CASE v_profile_status
      WHEN 'invited' THEN 'invited'
      WHEN 'active' THEN 'active'
      WHEN 'blocked' THEN 'blocked'
      WHEN 'offboarded' THEN 'archived'
      ELSE NULL
    END
  ) THEN
    RAISE EXCEPTION 'Platform staff account status is inconsistent'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
"""


LOOKUP_LOGIN_USER_SQL = """
CREATE OR REPLACE FUNCTION public.lookup_login_user_by_email(
  p_email TEXT,
  p_code_id UUID,
  p_candidate_hash TEXT
) RETURNS TABLE(
  id UUID, email TEXT, full_name TEXT, password_hash TEXT,
  is_developer BOOLEAN, is_administrator BOOLEAN, home_tenant_id UUID,
  status TEXT, last_login_at TIMESTAMPTZ, password_required BOOLEAN,
  membership_status TEXT, mfa_status TEXT
) AS $$
  SELECT
    app_user.id, app_user.email, app_user.full_name, app_user.password_hash,
    app_user.is_developer, app_user.is_administrator, app_user.home_tenant_id,
    app_user.status, app_user.last_login_at,
    EXISTS (
      SELECT 1 FROM public.user_assignment AS assignment
      WHERE assignment.user_id = app_user.id
        AND assignment.tenant_id = app_user.home_tenant_id
        AND assignment.is_active AND assignment.password_required
    ) AS password_required,
    membership.status AS membership_status,
    support_mfa.status AS mfa_status
  FROM public.app_user AS app_user
  LEFT JOIN public.tenant_membership AS membership
    ON membership.tenant_id = app_user.home_tenant_id
   AND membership.user_id = app_user.id
  LEFT JOIN public.support_mfa AS support_mfa ON support_mfa.user_id = app_user.id
  WHERE app_user.email_lower = pg_catalog.lower(pg_catalog.btrim(p_email))
    AND public.auth_email_code_matches(p_code_id, p_email, p_candidate_hash)
    AND NOT EXISTS (
      SELECT 1 FROM public.platform_staff_account AS profile
      WHERE profile.user_id = app_user.id AND profile.status = 'invited'
    )
  LIMIT 1
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


LOOKUP_LOGIN_USER_DOWN_SQL = LOOKUP_LOGIN_USER_SQL.replace(
    "    AND NOT EXISTS (\n"
    "      SELECT 1 FROM public.platform_staff_account AS profile\n"
    "      WHERE profile.user_id = app_user.id AND profile.status = 'invited'\n"
    "    )\n",
    "",
)


def _secure_function(signature: str, *, grant_to: str | None = None) -> None:
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} " "FROM PUBLIC, aurum_app, aurum_support"
    )
    if grant_to is not None:
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {grant_to}")


def upgrade() -> None:
    op.execute("""
        ALTER TABLE public.platform_staff_account_event
          ADD COLUMN operation_id UUID NOT NULL DEFAULT gen_random_uuid(),
          ADD COLUMN reason_code TEXT,
          ADD COLUMN reason TEXT,
          ADD CONSTRAINT ck_platform_staff_event_reason CHECK (
            (reason_code IS NULL AND reason IS NULL)
            OR (
              reason_code IN (
                'invitation_delivery', 'responsibility_change', 'security_incident',
                'access_review', 'employment_ended', 'other'
              )
              AND char_length(btrim(reason)) BETWEEN 10 AND 500
            )
          )
        """)
    op.execute(
        "CREATE UNIQUE INDEX uq_platform_staff_event_operation "
        "ON public.platform_staff_account_event(operation_id)"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_platform_staff_event_version "
        "ON public.platform_staff_account_event(user_id, account_version)"
    )

    op.execute(ACTOR_HAS_CAPABILITY_SQL)
    _secure_function(
        "public.platform_actor_has_capability(UUID, UUID, TEXT)",
        grant_to="aurum_support",
    )
    op.execute(ACTOR_HAS_RECENT_CAPABILITY_SQL)
    _secure_function(
        "public.platform_actor_has_recent_capability(UUID, UUID, TEXT)",
        grant_to="aurum_support",
    )
    op.execute(REINVITE_SQL)
    _secure_function(
        "public.reinvite_platform_staff_account("
        "UUID, UUID, UUID, INTEGER, UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ)",
        grant_to="aurum_support",
    )
    op.execute(CHANGE_STATUS_SQL)
    _secure_function(
        "public.change_platform_staff_account_status("
        "UUID, UUID, UUID, INTEGER, UUID, TEXT, TEXT, TEXT)",
        grant_to="aurum_support",
    )
    op.execute(STATUS_CONSISTENCY_SQL)
    _secure_function("public.trg_validate_platform_staff_status_consistency()")
    op.execute("""
        CREATE CONSTRAINT TRIGGER trg_platform_staff_profile_status_consistency
        AFTER INSERT OR UPDATE OF status ON public.platform_staff_account
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public.trg_validate_platform_staff_status_consistency()
        """)
    op.execute("""
        CREATE CONSTRAINT TRIGGER trg_platform_staff_user_status_consistency
        AFTER UPDATE OF status ON public.app_user
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION public.trg_validate_platform_staff_status_consistency()
        """)
    op.execute(LOOKUP_LOGIN_USER_SQL)
    _secure_function(
        "public.lookup_login_user_by_email(TEXT, UUID, TEXT)",
        grant_to="aurum_app, aurum_support",
    )


def downgrade() -> None:
    op.execute(LOOKUP_LOGIN_USER_DOWN_SQL)
    _secure_function(
        "public.lookup_login_user_by_email(TEXT, UUID, TEXT)",
        grant_to="aurum_app, aurum_support",
    )
    op.execute("DROP TRIGGER trg_platform_staff_user_status_consistency ON public.app_user")
    op.execute(
        "DROP TRIGGER trg_platform_staff_profile_status_consistency "
        "ON public.platform_staff_account"
    )
    op.execute("DROP FUNCTION public.trg_validate_platform_staff_status_consistency()")
    op.execute(
        "DROP FUNCTION public.change_platform_staff_account_status("
        "UUID, UUID, UUID, INTEGER, UUID, TEXT, TEXT, TEXT)"
    )
    op.execute(
        "DROP FUNCTION public.reinvite_platform_staff_account("
        "UUID, UUID, UUID, INTEGER, UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ)"
    )
    op.execute("DROP FUNCTION public.platform_actor_has_recent_capability(UUID, UUID, TEXT)")
    op.execute("DROP INDEX public.uq_platform_staff_event_version")
    op.execute("DROP INDEX public.uq_platform_staff_event_operation")
    op.execute("""
        ALTER TABLE public.platform_staff_account_event
          DROP CONSTRAINT ck_platform_staff_event_reason,
          DROP COLUMN reason,
          DROP COLUMN reason_code,
          DROP COLUMN operation_id
        """)
