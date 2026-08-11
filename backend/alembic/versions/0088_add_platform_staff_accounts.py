"""add protected platform staff account invitations

Revision ID: 0088
Revises: 0087
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0088"
down_revision: str | None = "0087"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PLATFORM_ACCOUNT_PERMISSION_CODES = (
    "platform.accounts.view",
    "platform.accounts.manage",
)


ACTOR_HAS_CAPABILITY_SQL = """
CREATE FUNCTION public.platform_actor_has_capability(
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
      WHERE auth_session.id = p_actor_session_id
        AND auth_session.user_id = p_actor_user_id
        AND auth_session.revoked_at IS NULL
        AND auth_session.expires_at > pg_catalog.statement_timestamp()
    );
END;
$$ LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
"""


CREATE_INVITATION_SQL = """
CREATE FUNCTION public.create_platform_staff_invitation(
  p_actor_user_id UUID,
  p_actor_session_id UUID,
  p_email TEXT,
  p_full_name TEXT,
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
  updated_at TIMESTAMPTZ
) AS $$
DECLARE
  v_user_id UUID;
  v_now TIMESTAMPTZ := pg_catalog.statement_timestamp();
BEGIN
  IF NOT public.platform_actor_has_capability(
    p_actor_user_id,
    p_actor_session_id,
    'platform.accounts.manage'
  ) THEN
    RAISE EXCEPTION 'Platform account management capability required'
      USING ERRCODE = '42501';
  END IF;

  IF NULLIF(pg_catalog.btrim(p_email), '') IS NULL
    OR NULLIF(pg_catalog.btrim(p_full_name), '') IS NULL
    OR p_token_hash !~ '^[0-9a-f]{64}$'
    OR p_expires_at <= v_now
    OR p_expires_at > v_now + INTERVAL '48 hours'
  THEN
    RAISE EXCEPTION 'Invalid platform invitation data'
      USING ERRCODE = '22023';
  END IF;

  INSERT INTO public.app_user (
    email,
    full_name,
    password_hash,
    is_developer,
    is_administrator,
    home_tenant_id,
    status,
    invited_at,
    created_at,
    updated_at
  ) VALUES (
    pg_catalog.lower(pg_catalog.btrim(p_email)),
    pg_catalog.btrim(p_full_name),
    NULL,
    false,
    false,
    NULL,
    'invited',
    v_now,
    v_now,
    v_now
  )
  RETURNING id INTO v_user_id;

  INSERT INTO public.platform_staff_account (
    user_id,
    status,
    version,
    invited_by,
    invited_at,
    invitation_token_hash,
    invitation_expires_at,
    created_at,
    updated_at
  ) VALUES (
    v_user_id,
    'invited',
    1,
    p_actor_user_id,
    v_now,
    p_token_hash,
    p_expires_at,
    v_now,
    v_now
  );

  INSERT INTO public.platform_staff_account_event (
    user_id,
    actor_user_id,
    event_type,
    account_version,
    created_at
  ) VALUES (
    v_user_id,
    p_actor_user_id,
    'invited',
    1,
    v_now
  );

  RETURN QUERY
  SELECT
    account.id,
    account.email,
    account.full_name,
    profile.status,
    profile.version,
    profile.invited_at,
    profile.invitation_expires_at,
    profile.activated_at,
    profile.blocked_at,
    profile.offboarded_at,
    profile.created_at,
    profile.updated_at
  FROM public.platform_staff_account AS profile
  JOIN public.app_user AS account ON account.id = profile.user_id
  WHERE profile.user_id = v_user_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
"""


INVITATION_USABLE_SQL = """
CREATE FUNCTION public.platform_staff_invitation_is_usable(p_token_hash TEXT)
RETURNS BOOLEAN AS $$
BEGIN
  RETURN EXISTS (
    SELECT 1
    FROM public.platform_staff_account AS profile
    JOIN public.app_user AS account ON account.id = profile.user_id
    WHERE profile.invitation_token_hash = p_token_hash
      AND profile.status = 'invited'
      AND profile.invitation_expires_at > pg_catalog.statement_timestamp()
      AND account.status = 'invited'
      AND account.password_hash IS NULL
      AND account.home_tenant_id IS NULL
      AND NOT account.is_developer
      AND NOT account.is_administrator
  );
END;
$$ LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
"""


ACCEPT_INVITATION_SQL = """
CREATE FUNCTION public.accept_platform_staff_invitation(
  p_token_hash TEXT,
  p_password_hash TEXT
)
RETURNS UUID AS $$
DECLARE
  v_user_id UUID;
  v_version INTEGER;
  v_now TIMESTAMPTZ := pg_catalog.statement_timestamp();
BEGIN
  IF p_token_hash !~ '^[0-9a-f]{64}$'
    OR NULLIF(p_password_hash, '') IS NULL
  THEN
    RETURN NULL;
  END IF;

  SELECT profile.user_id, profile.version
  INTO v_user_id, v_version
  FROM public.platform_staff_account AS profile
  JOIN public.app_user AS account ON account.id = profile.user_id
  WHERE profile.invitation_token_hash = p_token_hash
    AND profile.status = 'invited'
    AND profile.invitation_expires_at > v_now
    AND account.status = 'invited'
    AND account.password_hash IS NULL
    AND account.home_tenant_id IS NULL
    AND NOT account.is_developer
    AND NOT account.is_administrator
  FOR UPDATE OF profile, account;

  IF v_user_id IS NULL THEN
    RETURN NULL;
  END IF;

  UPDATE public.app_user
  SET
    password_hash = p_password_hash,
    status = 'active',
    activated_at = v_now,
    updated_at = v_now
  WHERE id = v_user_id;

  UPDATE public.platform_staff_account
  SET
    status = 'active',
    version = version + 1,
    invitation_token_hash = NULL,
    invitation_expires_at = NULL,
    activated_at = v_now,
    updated_at = v_now
  WHERE user_id = v_user_id;

  INSERT INTO public.platform_staff_account_event (
    user_id,
    actor_user_id,
    event_type,
    account_version,
    created_at
  ) VALUES (
    v_user_id,
    NULL,
    'activated',
    v_version + 1,
    v_now
  );

  RETURN v_user_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
"""


IMMUTABLE_EVENT_SQL = """
CREATE FUNCTION public.trg_reject_platform_staff_event_mutation()
RETURNS TRIGGER AS $$
BEGIN
  RAISE EXCEPTION 'Platform staff account events are immutable'
    USING ERRCODE = '42501';
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
"""


def _secure_function(signature: str, *, grant_to: str | None = None) -> None:
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    if grant_to is not None:
        op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {grant_to}")


def upgrade() -> None:
    op.execute("""
        INSERT INTO public.permission (
          code, group_code, name, description, min_level_required,
          is_dangerous, is_active, scope_type, target_role_type, risk_level,
          developer_grantable, administrator_grantable, owner_grantable,
          developer_delegable, administrator_delegable, owner_delegable,
          requires_step_up, requires_confirmation
        ) VALUES
          ('platform.accounts.view', 'platform_accounts', 'Просмотр команды Aurum',
           'Просмотр непривилегированных кандидатов команды Aurum Pharma',
           2, false, true, 'PLATFORM', 'platform', 'sensitive',
           true, true, false, true, false, false, false, false),
          ('platform.accounts.manage', 'platform_accounts', 'Приглашение команды Aurum',
           'Создание непривилегированных кандидатов без выдачи platform-доступа',
           2, true, true, 'PLATFORM', 'platform', 'critical',
           true, true, false, true, false, false, true, true)
        """)

    op.execute("""
        DO $$
        DECLARE
          v_user_had_references BOOLEAN := pg_catalog.has_table_privilege(
            'aurum_schema_owner',
            'public.app_user',
            'REFERENCES'
          );
        BEGIN
          IF NOT v_user_had_references THEN
            GRANT REFERENCES ON TABLE public.app_user TO aurum_schema_owner;
          END IF;

          CREATE TABLE public.platform_staff_account (
          user_id UUID PRIMARY KEY
            REFERENCES public.app_user(id) ON DELETE RESTRICT,
          status TEXT NOT NULL
            CHECK (status IN ('invited', 'active', 'blocked', 'offboarded')),
          version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
          invited_by UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
          invited_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          invitation_token_hash TEXT UNIQUE,
          invitation_expires_at TIMESTAMPTZ,
          activated_at TIMESTAMPTZ,
          blocked_at TIMESTAMPTZ,
          offboarded_at TIMESTAMPTZ,
          created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp(),
          CONSTRAINT ck_platform_staff_invitation_state CHECK (
            (status = 'invited'
             AND invitation_token_hash IS NOT NULL
             AND invitation_expires_at IS NOT NULL)
            OR
            (status <> 'invited'
             AND invitation_token_hash IS NULL
             AND invitation_expires_at IS NULL)
          )
          );

          CREATE TABLE public.platform_staff_account_event (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL
              REFERENCES public.platform_staff_account(user_id) ON DELETE RESTRICT,
            actor_user_id UUID REFERENCES public.app_user(id) ON DELETE SET NULL,
            event_type TEXT NOT NULL
              CHECK (event_type IN (
                'invited', 'reinvited', 'activated', 'blocked',
                'unblocked', 'offboarded'
              )),
            account_version INTEGER NOT NULL CHECK (account_version >= 1),
            created_at TIMESTAMPTZ NOT NULL DEFAULT statement_timestamp()
          );

          IF NOT v_user_had_references THEN
            REVOKE REFERENCES ON TABLE public.app_user FROM aurum_schema_owner;
          END IF;
        END
        $$
        """)
    op.execute(
        "CREATE INDEX ix_platform_staff_account_status "
        "ON public.platform_staff_account(status, invited_at DESC, user_id)"
    )
    op.execute(
        "CREATE INDEX ix_platform_staff_event_user_created "
        "ON public.platform_staff_account_event(user_id, created_at DESC, id)"
    )

    op.execute("""
        INSERT INTO public.platform_staff_account (
          user_id, status, version, invited_by, invited_at,
          invitation_token_hash, invitation_expires_at,
          activated_at, created_at, updated_at
        )
        SELECT DISTINCT ON (account.id)
          account.id,
          'active',
          1,
          grant_row.requested_by,
          account.invited_at,
          NULL,
          NULL,
          COALESCE(account.activated_at, account.invited_at),
          account.created_at,
          account.updated_at
        FROM public.platform_access_grant AS grant_row
        JOIN public.app_user AS account ON account.id = grant_row.user_id
        WHERE grant_row.status IN ('pending', 'active')
        ORDER BY account.id, grant_row.created_at
        """)

    op.execute(ACTOR_HAS_CAPABILITY_SQL)
    _secure_function(
        "public.platform_actor_has_capability(UUID, UUID, TEXT)",
        grant_to="aurum_support",
    )
    op.execute(CREATE_INVITATION_SQL)
    _secure_function(
        "public.create_platform_staff_invitation(UUID, UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ)",
        grant_to="aurum_support",
    )
    op.execute(INVITATION_USABLE_SQL)
    _secure_function(
        "public.platform_staff_invitation_is_usable(TEXT)",
        grant_to="aurum_app, aurum_support",
    )
    op.execute(ACCEPT_INVITATION_SQL)
    _secure_function(
        "public.accept_platform_staff_invitation(TEXT, TEXT)",
        grant_to="aurum_app, aurum_support",
    )
    op.execute(IMMUTABLE_EVENT_SQL)
    _secure_function("public.trg_reject_platform_staff_event_mutation()")
    op.execute("""
        CREATE TRIGGER trg_immutable_platform_staff_account_event
        BEFORE UPDATE OR DELETE ON public.platform_staff_account_event
        FOR EACH ROW EXECUTE FUNCTION public.trg_reject_platform_staff_event_mutation()
        """)

    op.execute(
        "REVOKE ALL PRIVILEGES ON TABLE public.platform_staff_account, "
        "public.platform_staff_account_event FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute(
        "GRANT SELECT ON TABLE public.platform_staff_account, "
        "public.platform_staff_account_event TO aurum_support"
    )

    # Existing Developers receive the new account-management capabilities.
    # Administrator grants remain unchanged and must be explicitly replaced by
    # a Developer if an administrator should invite platform candidates.
    op.execute(
        "ALTER TABLE public.platform_access_grant_permission "
        "DISABLE TRIGGER trg_10_guard_platform_access_grant_permission"
    )
    op.execute("""
        INSERT INTO public.platform_access_grant_permission (
          grant_id, permission_code, created_by
        )
        SELECT grant_row.id, permission.code, grant_row.requested_by
        FROM public.platform_access_grant AS grant_row
        CROSS JOIN public.permission AS permission
        WHERE grant_row.access_kind = 'developer'
          AND grant_row.status = 'active'
          AND permission.code IN (
            'platform.accounts.view',
            'platform.accounts.manage'
          )
        ON CONFLICT DO NOTHING
        """)
    op.execute(
        "ALTER TABLE public.platform_access_grant_permission "
        "ENABLE TRIGGER trg_10_guard_platform_access_grant_permission"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE public.platform_access_grant_permission "
        "DISABLE TRIGGER trg_10_guard_platform_access_grant_permission"
    )
    codes = ", ".join(f"'{code}'" for code in PLATFORM_ACCOUNT_PERMISSION_CODES)
    op.execute(
        "DELETE FROM public.platform_access_grant_permission "
        f"WHERE permission_code IN ({codes})"
    )
    op.execute(
        "ALTER TABLE public.platform_access_grant_permission "
        "ENABLE TRIGGER trg_10_guard_platform_access_grant_permission"
    )
    op.execute(
        "DROP TRIGGER trg_immutable_platform_staff_account_event "
        "ON public.platform_staff_account_event"
    )
    op.execute("DROP FUNCTION public.trg_reject_platform_staff_event_mutation()")
    op.execute("DROP FUNCTION public.accept_platform_staff_invitation(TEXT, TEXT)")
    op.execute("DROP FUNCTION public.platform_staff_invitation_is_usable(TEXT)")
    op.execute(
        "DROP FUNCTION public.create_platform_staff_invitation("
        "UUID, UUID, TEXT, TEXT, TEXT, TIMESTAMPTZ)"
    )
    op.execute("DROP FUNCTION public.platform_actor_has_capability(UUID, UUID, TEXT)")
    op.execute("DROP TABLE public.platform_staff_account_event")
    op.execute("DROP TABLE public.platform_staff_account")
    op.execute(f"DELETE FROM public.permission WHERE code IN ({codes})")
