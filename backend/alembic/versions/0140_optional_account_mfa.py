"""Optional account MFA and explicit password confirmation for sensitive actions.

Revision ID: 0140
Revises: 0139
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0140"
down_revision = "0139"
branch_labels = None
depends_on = None


def _source(filename: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load migration {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _replace(sql: str) -> str:
    return sql.replace("CREATE FUNCTION public.", "CREATE OR REPLACE FUNCTION public.", 1)


def _secure(signature: str, *, app: bool = False) -> None:
    op.execute(f"ALTER FUNCTION public.{signature} OWNER TO aurum_schema_owner")
    op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC, aurum_app, aurum_support")
    grantees = "aurum_support, aurum_app" if app else "aurum_support"
    op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO {grantees}")


ACCOUNT_POLICY_SQL = """
CREATE OR REPLACE FUNCTION public.auth_account_requires_mfa(p_user_id UUID)
RETURNS BOOLEAN AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.app_user AS actor
    JOIN public.support_mfa AS factor ON factor.user_id = actor.id
    WHERE actor.id = p_user_id AND actor.status IN ('invited', 'active')
      AND factor.status IN ('active', 'recovery_pending')
  )
$$ LANGUAGE SQL STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
"""


ACCOUNT_LOOKUP_SQL = """
CREATE OR REPLACE FUNCTION public.lookup_auth_account_mfa_requirement(
  p_user_id UUID, p_session_id UUID
) RETURNS BOOLEAN AS $$
  SELECT public.auth_account_requires_mfa(identity.id)
  FROM public.lookup_auth_user_by_id(p_user_id, p_session_id) AS identity
  WHERE p_session_id IS NULL OR EXISTS (
    SELECT 1 FROM public.session AS auth_session
    WHERE auth_session.id = p_session_id AND auth_session.user_id = p_user_id
      AND auth_session.revoked_at IS NULL AND auth_session.expires_at > now()
  )
$$ LANGUAGE SQL STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
"""


LEGACY_ACCOUNT_LOOKUP_SQL = """
CREATE OR REPLACE FUNCTION public.lookup_auth_account_mfa_requirement(
  p_user_id UUID, p_session_id UUID
) RETURNS BOOLEAN AS $$
  SELECT public.auth_account_requires_mfa(identity.id)
  FROM public.lookup_auth_user_by_id(p_user_id, p_session_id) AS identity
$$ LANGUAGE SQL STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
"""


SESSION_IDENTITY_SQL = """
CREATE FUNCTION public.auth_settings_session_matches(p_user_id UUID, p_session_id UUID)
RETURNS BOOLEAN AS $$
  SELECT COALESCE(p_user_id IS NOT DISTINCT FROM public.current_app_user_id()
    AND p_session_id IS NOT NULL
    AND p_session_id = NULLIF(current_setting('app.auth_session_id', true), '')::UUID
    AND EXISTS (
      SELECT 1 FROM public.session AS auth_session
      JOIN public.app_user AS actor ON actor.id = auth_session.user_id
      WHERE auth_session.id = p_session_id AND actor.id = p_user_id
        AND actor.status IN ('invited', 'active')
        AND auth_session.revoked_at IS NULL AND auth_session.expires_at > now()
    ), false)
$$ LANGUAGE SQL STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
"""


CONFIRMATION_SQL = r"""
CREATE FUNCTION public.current_auth_confirmation_at()
RETURNS TIMESTAMPTZ AS $$
DECLARE
  v_password_claim TEXT := NULLIF(current_setting('app.password_verified_at', true), '');
  v_mfa_claim TEXT := NULLIF(current_setting('app.mfa_verified_at', true), '');
  v_password_at TIMESTAMPTZ;
  v_mfa_at TIMESTAMPTZ;
  v_session public.session%ROWTYPE;
BEGIN
  SELECT auth_session.* INTO v_session FROM public.session AS auth_session
  JOIN public.app_user AS actor ON actor.id = auth_session.user_id
  WHERE auth_session.id = NULLIF(current_setting('app.auth_session_id', true), '')::UUID
    AND auth_session.user_id = public.current_app_user_id()
    AND actor.status IN ('invited', 'active')
    AND auth_session.revoked_at IS NULL AND auth_session.expires_at > now();
  IF NOT FOUND THEN RETURN NULL; END IF;
  IF v_password_claim ~ '^[0-9]{1,12}$' THEN
    v_password_at := to_timestamp(v_password_claim::DOUBLE PRECISION);
    IF v_session.password_verified_at IS NULL
      OR date_trunc('second', v_session.password_verified_at) <> v_password_at
      OR v_password_at < statement_timestamp() - INTERVAL '10 minutes'
      OR v_password_at > statement_timestamp() + INTERVAL '1 minute'
    THEN v_password_at := NULL; END IF;
  END IF;
  IF v_mfa_claim ~ '^[0-9]{1,12}$' THEN
    v_mfa_at := to_timestamp(v_mfa_claim::DOUBLE PRECISION);
    IF v_session.mfa_verified_at IS NULL
      OR v_mfa_at < statement_timestamp() - INTERVAL '10 minutes'
      OR v_mfa_at > statement_timestamp() + INTERVAL '1 minute'
    THEN v_mfa_at := NULL; END IF;
  END IF;
  RETURN GREATEST(v_password_at, v_mfa_at);
END
$$ LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
"""


SETTINGS_SQL = """
CREATE FUNCTION public.lookup_account_mfa_settings(p_user_id UUID, p_session_id UUID)
RETURNS TABLE(status TEXT, prompt_dismissed_at TIMESTAMPTZ, password_configured BOOLEAN) AS $$
  SELECT factor.status, actor.mfa_prompt_dismissed_at, actor.password_hash IS NOT NULL
  FROM public.app_user AS actor
  LEFT JOIN public.support_mfa AS factor ON factor.user_id = actor.id
  WHERE actor.id = p_user_id AND public.auth_settings_session_matches(p_user_id, p_session_id)
$$ LANGUAGE SQL STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
"""


DISMISS_SQL = """
CREATE FUNCTION public.dismiss_account_mfa_prompt(p_user_id UUID, p_session_id UUID)
RETURNS BOOLEAN AS $$
BEGIN
  IF NOT public.auth_settings_session_matches(p_user_id, p_session_id) THEN RETURN false; END IF;
  UPDATE public.app_user SET mfa_prompt_dismissed_at = COALESCE(mfa_prompt_dismissed_at, now())
  WHERE id = p_user_id;
  RETURN FOUND;
END
$$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
"""


PASSWORD_LOOKUP_SQL = """
CREATE FUNCTION public.lookup_account_password_hash(p_user_id UUID, p_session_id UUID)
RETURNS TEXT AS $$
  SELECT actor.password_hash FROM public.app_user AS actor
  WHERE actor.id = p_user_id AND public.auth_settings_session_matches(p_user_id, p_session_id)
$$ LANGUAGE SQL STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
"""


PASSWORD_CONFIRM_SQL = """
CREATE FUNCTION public.confirm_account_password(
  p_user_id UUID, p_session_id UUID, p_verified_password_hash TEXT
) RETURNS TIMESTAMPTZ AS $$
DECLARE v_now TIMESTAMPTZ := clock_timestamp();
BEGIN
  IF p_verified_password_hash IS NULL
    OR NOT public.auth_settings_session_matches(p_user_id, p_session_id)
  THEN RETURN NULL; END IF;
  PERFORM 1 FROM public.app_user AS actor
  WHERE actor.id = p_user_id AND actor.password_hash = p_verified_password_hash
    AND actor.status IN ('invited', 'active') FOR UPDATE;
  IF NOT FOUND THEN RETURN NULL; END IF;
  UPDATE public.session SET password_verified_at = v_now, last_used_at = v_now
  WHERE id = p_session_id AND user_id = p_user_id AND revoked_at IS NULL AND expires_at > v_now;
  IF NOT FOUND THEN RETURN NULL; END IF;
  INSERT INTO public.audit_log(user_id, action, table_name, record_id, metadata)
  VALUES(p_user_id, 'VIEW', 'app_user', p_user_id,
    jsonb_build_object('event', 'password_confirmed', 'method', 'password',
      'session_id', p_session_id));
  RETURN v_now;
END
$$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
"""


PASSWORD_TIME_SQL = """
CREATE FUNCTION public.lookup_auth_session_password(p_user_id UUID, p_session_id UUID)
RETURNS TIMESTAMPTZ AS $$
  SELECT auth_session.password_verified_at FROM public.session AS auth_session
  WHERE auth_session.id = p_session_id AND auth_session.user_id = p_user_id
    AND auth_session.revoked_at IS NULL AND auth_session.expires_at > now()
    AND (public.current_app_user_id() IS NULL OR public.current_app_user_id() = p_user_id)
$$ LANGUAGE SQL STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
"""


PASSWORD_SET_SQL = """
CREATE FUNCTION public.set_initial_account_password(
  p_user_id UUID, p_session_id UUID, p_code_id UUID, p_candidate_hash TEXT, p_password_hash TEXT
) RETURNS TIMESTAMPTZ AS $$
DECLARE v_now TIMESTAMPTZ := clock_timestamp(); v_email TEXT;
BEGIN
  IF NOT public.auth_settings_session_matches(p_user_id, p_session_id)
    OR p_password_hash IS NULL OR length(p_password_hash) < 30
  THEN RETURN NULL; END IF;
  SELECT actor.email_lower INTO v_email FROM public.app_user AS actor
  WHERE actor.id = p_user_id AND actor.password_hash IS NULL
    AND actor.status IN ('invited', 'active') FOR UPDATE;
  IF NOT FOUND THEN RETURN NULL; END IF;
  PERFORM 1 FROM public.session WHERE id = p_session_id AND user_id = p_user_id
    AND revoked_at IS NULL AND expires_at > v_now FOR UPDATE;
  IF NOT FOUND THEN RETURN NULL; END IF;
  UPDATE public.email_code SET used_at = v_now
  WHERE id = p_code_id AND email_lower = v_email AND code_hash = p_candidate_hash
    AND purpose = 'login' AND used_at IS NULL AND expires_at > v_now
    AND created_at >= v_now - INTERVAL '10 minutes';
  IF NOT FOUND THEN RETURN NULL; END IF;
  UPDATE public.app_user SET password_hash = p_password_hash, updated_at = v_now
  WHERE id = p_user_id;
  UPDATE public.session SET revoked_at = v_now, revoked_reason = 'password_configured'
  WHERE user_id = p_user_id AND id <> p_session_id AND revoked_at IS NULL;
  UPDATE public.session SET password_verified_at = v_now, last_used_at = v_now
  WHERE id = p_session_id;
  INSERT INTO public.audit_log(user_id, action, table_name, record_id, metadata)
  VALUES(p_user_id, 'UPDATE', 'app_user', p_user_id,
    jsonb_build_object('event', 'password_configured'));
  RETURN v_now;
END
$$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
"""


ENROLL_BEGIN_SQL = """
CREATE FUNCTION public.create_authenticated_mfa_challenge(
  p_user_id UUID, p_session_id UUID, p_token_hash TEXT, p_verified_password_hash TEXT,
  p_ip_address TEXT, p_user_agent TEXT, p_expires_at TIMESTAMPTZ
) RETURNS TABLE(id UUID, purpose TEXT) AS $$
DECLARE v_id UUID;
BEGIN
  IF NOT public.auth_settings_session_matches(p_user_id, p_session_id)
    OR p_verified_password_hash IS NULL OR p_token_hash !~ '^[0-9a-f]{64}$'
    OR p_expires_at IS NULL OR p_expires_at <= now() OR p_expires_at > now() + INTERVAL '10 minutes'
  THEN RETURN; END IF;
  PERFORM 1 FROM public.app_user AS actor
  WHERE actor.id = p_user_id AND actor.password_hash = p_verified_password_hash
    AND actor.status IN ('invited', 'active') FOR UPDATE;
  IF NOT FOUND THEN RETURN; END IF;
  PERFORM 1 FROM public.session WHERE session.id = p_session_id AND user_id = p_user_id
    AND revoked_at IS NULL AND expires_at > now() FOR UPDATE;
  IF NOT FOUND OR public.auth_account_requires_mfa(p_user_id) THEN RETURN; END IF;
  UPDATE public.auth_mfa_challenge SET consumed_at = now()
  WHERE user_id = p_user_id AND consumed_at IS NULL;
  INSERT INTO public.auth_mfa_challenge(token_hash,user_id,purpose,ip_address,user_agent,
    expires_at,initiating_session_id)
  VALUES(p_token_hash,p_user_id,'enroll',p_ip_address::INET,left(p_user_agent,1024),
    p_expires_at,p_session_id)
  RETURNING auth_mfa_challenge.id INTO v_id;
  RETURN QUERY SELECT v_id, 'enroll'::TEXT;
END
$$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
"""


ENROLL_MATCH_SQL = """
CREATE FUNCTION public.authenticated_mfa_challenge_matches(
  p_user_id UUID,p_session_id UUID,p_token_hash TEXT)
RETURNS BOOLEAN AS $$
  SELECT public.auth_settings_session_matches(p_user_id,p_session_id) AND EXISTS (
    SELECT 1 FROM public.auth_mfa_challenge AS challenge WHERE challenge.token_hash=p_token_hash
      AND challenge.user_id=p_user_id AND challenge.initiating_session_id=p_session_id
      AND challenge.consumed_at IS NULL AND challenge.expires_at>now()
      AND challenge.failed_attempts<5
  )
$$ LANGUAGE SQL STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
"""


DISABLE_SQL = """
CREATE FUNCTION public.disable_account_mfa(
  p_user_id UUID,p_session_id UUID,p_verified_password_hash TEXT,p_refresh_token_hash TEXT,
  p_user_agent TEXT,p_ip_address TEXT,p_expires_at TIMESTAMPTZ
) RETURNS UUID AS $$
DECLARE v_factor public.support_mfa%ROWTYPE; v_id UUID; v_now TIMESTAMPTZ:=clock_timestamp();
BEGIN
  IF NOT public.auth_settings_session_matches(p_user_id,p_session_id)
    OR p_verified_password_hash IS NULL OR p_refresh_token_hash !~ '^[0-9a-f]{64}$'
    OR p_expires_at IS NULL OR p_expires_at<=v_now OR p_expires_at>v_now+INTERVAL '31 days'
  THEN RETURN NULL; END IF;
  PERFORM 1 FROM public.app_user AS actor WHERE actor.id=p_user_id
    AND actor.password_hash=p_verified_password_hash
    AND actor.status IN ('invited','active') FOR UPDATE;
  IF NOT FOUND THEN RETURN NULL; END IF;
  PERFORM 1 FROM public.session WHERE id=p_session_id AND user_id=p_user_id
    AND revoked_at IS NULL AND expires_at>v_now AND mfa_verified_at IS NOT NULL FOR UPDATE;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT * INTO v_factor FROM public.support_mfa WHERE user_id=p_user_id FOR UPDATE;
  IF NOT FOUND OR v_factor.status NOT IN ('active','recovery_pending') THEN RETURN NULL; END IF;
  UPDATE public.session SET revoked_at=v_now,revoked_reason='mfa_disabled',last_used_at=v_now
  WHERE user_id=p_user_id AND revoked_at IS NULL;
  UPDATE public.auth_mfa_challenge SET consumed_at=v_now
  WHERE user_id=p_user_id AND consumed_at IS NULL;
  DELETE FROM public.support_mfa_recovery_code WHERE user_id=p_user_id;
  DELETE FROM public.support_mfa WHERE user_id=p_user_id;
  UPDATE public.app_user SET mfa_prompt_dismissed_at=v_now WHERE id=p_user_id;
  INSERT INTO public.session(user_id,refresh_token_hash,user_agent,ip_address,expires_at)
  VALUES(p_user_id,p_refresh_token_hash,left(p_user_agent,1024),p_ip_address::INET,p_expires_at)
  RETURNING id INTO v_id;
  INSERT INTO public.audit_log(user_id,action,table_name,record_id,metadata)
  VALUES(p_user_id,'UPDATE','support_mfa',p_user_id,
    jsonb_build_object('event','mfa_disabled','method','password','session_id',p_session_id));
  RETURN v_id;
END
$$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = pg_catalog, pg_temp
"""


NEW_FUNCTIONS = (
    (SESSION_IDENTITY_SQL, "auth_settings_session_matches(UUID,UUID)"),
    (CONFIRMATION_SQL, "current_auth_confirmation_at()"),
    (SETTINGS_SQL, "lookup_account_mfa_settings(UUID,UUID)"),
    (DISMISS_SQL, "dismiss_account_mfa_prompt(UUID,UUID)"),
    (PASSWORD_LOOKUP_SQL, "lookup_account_password_hash(UUID,UUID)"),
    (PASSWORD_CONFIRM_SQL, "confirm_account_password(UUID,UUID,TEXT)"),
    (PASSWORD_TIME_SQL, "lookup_auth_session_password(UUID,UUID)"),
    (PASSWORD_SET_SQL, "set_initial_account_password(UUID,UUID,UUID,TEXT,TEXT)"),
    (
        ENROLL_BEGIN_SQL,
        "create_authenticated_mfa_challenge(UUID,UUID,TEXT,TEXT,TEXT,TEXT,TIMESTAMPTZ)",
    ),
    (ENROLL_MATCH_SQL, "authenticated_mfa_challenge_matches(UUID,UUID,TEXT)"),
    (
        DISABLE_SQL,
        "disable_account_mfa(UUID,UUID,TEXT,TEXT,TEXT,TEXT,TIMESTAMPTZ)",
    ),
)


def _optional_mfa(sql: str) -> str:
    sql = _replace(sql).replace("AND (app_user.is_developer OR app_user.is_administrator)", "")
    # Session-backed enrollments are invalidated by logout or account replacement.
    if "challenge.consumed_at IS NULL" in sql:
        origin_lock = "FOR SHARE OF originating" if "LANGUAGE plpgsql" in sql else ""
        sql = sql.replace(
            "AND challenge.consumed_at IS NULL",
            f"""AND challenge.consumed_at IS NULL
    AND (challenge.initiating_session_id IS NULL OR EXISTS (
      SELECT 1 FROM public.session AS originating
      WHERE originating.id = challenge.initiating_session_id
        AND originating.user_id = challenge.user_id AND originating.revoked_at IS NULL
        AND originating.expires_at > now() {origin_lock}
    ))""",
        )
    if "v_purpose = 'recovery_enroll' THEN" in sql and "v_distinct_codes" in sql:
        sql = sql.replace(
            "  SELECT (\n    GREATEST(",
            """  IF v_purpose = 'enroll' AND public.auth_account_requires_mfa(v_user_id) THEN
    RETURN false;
  END IF;
  SELECT (
    GREATEST(""",
            1,
        )
    return sql


def _critical_functions(*, restore: bool = False) -> None:
    source_0088 = _source("0088_add_platform_staff_accounts.py")
    source_0089 = _source("0089_add_platform_staff_lifecycle.py")
    source_0095 = _source("0095_add_billing_pricing_commands.py")
    source_0116 = _source("0116_add_ownership_transfer.py")
    recent = _replace(source_0116.MFA_RECENT_SQL)
    capability = source_0089.ACTOR_HAS_CAPABILITY_SQL
    recent_capability = _replace(source_0089.ACTOR_HAS_RECENT_CAPABILITY_SQL)
    assertion = _replace(source_0095.ASSERT_RECENT_CAPABILITY_SQL)
    transfer = _replace(source_0116.ACCEPT_TRANSFER_SQL)
    invitation = _replace(source_0088.CREATE_INVITATION_SQL)
    if not restore:
        recent = """CREATE OR REPLACE FUNCTION public.ownership_transfer_mfa_is_recent()
RETURNS BOOLEAN AS $$ SELECT public.current_auth_confirmation_at() IS NOT NULL
$$ LANGUAGE SQL STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp"""
        start = capability.index("        AND (\n          p_capability <>")
        end = capability.index("        )\n    );", start) + len("        )\n")
        capability = capability[:start] + capability[end:]
        invitation = invitation.replace(
            "public.platform_actor_has_capability(", "public.platform_actor_has_recent_capability("
        )
        start = recent_capability.index("auth_session.mfa_verified_at IS NOT NULL")
        end = recent_capability.index("+ INTERVAL '1 minute'", start) + len("+ INTERVAL '1 minute'")
        recent_capability = (
            recent_capability[:start]
            + "public.current_auth_confirmation_at() IS NOT NULL"
            + recent_capability[end:]
        )
        start = assertion.index("  v_mfa_claim :=")
        end = assertion.index("\n\n  IF public.current_app_user_id()", start)
        assertion = (
            assertion[:start] + """  v_mfa_verified_at := public.current_auth_confirmation_at();
  IF v_mfa_verified_at IS NULL THEN
    RAISE EXCEPTION 'Recent account confirmation is required' USING ERRCODE='42501';
  END IF;""" + assertion[end:]
        )
        assertion = assertion.replace("    AND auth_session.mfa_verified_at IS NOT NULL\n", "")
        start = transfer.index("    AND public.auth_account_requires_mfa(target.id)")
        end = transfer.index("    );", start) + len("    )")
        transfer = transfer[:start] + transfer[end:]
    for sql in (recent, capability, recent_capability, assertion, transfer, invitation):
        op.execute(sql)


def upgrade() -> None:
    op.execute("ALTER TABLE public.app_user ADD COLUMN mfa_prompt_dismissed_at TIMESTAMPTZ")
    op.execute("ALTER TABLE public.session ADD COLUMN password_verified_at TIMESTAMPTZ")
    op.execute(
        "ALTER TABLE public.auth_mfa_challenge ADD COLUMN initiating_session_id UUID "
        "REFERENCES public.session(id) ON DELETE CASCADE"
    )
    op.execute(ACCOUNT_POLICY_SQL)
    op.execute(ACCOUNT_LOOKUP_SQL)
    for sql, signature in NEW_FUNCTIONS:
        op.execute(sql)
        _secure(signature, app=signature == "lookup_auth_session_password(UUID,UUID)")
    source_0056 = _source("0056_add_support_mfa.py")
    source_0057 = _source("0057_harden_support_mfa.py")
    source_0115 = _source("0115_require_owner_mfa.py")
    standard = (
        _replace(source_0056.CREATE_STANDARD_SESSION_SQL)
        .replace(
            "    AND NOT app_user.is_developer\n    AND NOT app_user.is_administrator;",
            "  FOR UPDATE;",
        )
        .replace(
            "  IF v_user_id IS NULL THEN",
            "  IF v_user_id IS NULL OR public.auth_account_requires_mfa(v_user_id) THEN",
            1,
        )
    )
    op.execute(standard)
    for sql, _signature in source_0115._mfa_functions(source_0057):
        op.execute(_optional_mfa(sql))
    # Login challenges require enabled MFA; settings enrollment has a separate entry point.
    op.execute(source_0115._account_mfa_challenge(source_0056.CREATE_MFA_CHALLENGE_SQL))
    _critical_functions()


def downgrade() -> None:
    _critical_functions(restore=True)
    source_0056 = _source("0056_add_support_mfa.py")
    source_0057 = _source("0057_harden_support_mfa.py")
    source_0115 = _source("0115_require_owner_mfa.py")
    source_0116 = _source("0116_add_ownership_transfer.py")
    op.execute(source_0116.ACCOUNT_REQUIRES_MFA_SQL)
    op.execute(LEGACY_ACCOUNT_LOOKUP_SQL)
    op.execute(_replace(source_0056.CREATE_STANDARD_SESSION_SQL))
    for sql, _signature in source_0115._mfa_functions(source_0057):
        op.execute(source_0115._allow_required_accounts(sql))
    for _sql, signature in reversed(NEW_FUNCTIONS):
        op.execute(f"DROP FUNCTION public.{signature}")
    op.execute("ALTER TABLE public.auth_mfa_challenge DROP COLUMN initiating_session_id")
    op.execute("ALTER TABLE public.session DROP COLUMN password_verified_at")
    op.execute("ALTER TABLE public.app_user DROP COLUMN mfa_prompt_dismissed_at")
