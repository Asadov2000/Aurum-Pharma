"""Verified phone identities and purpose-bound authentication challenges.

Revision ID: 0146
Revises: 0145
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic import op

revision = "0146"
down_revision = "0145"
branch_labels = None
depends_on = None


def _source(filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "identity_migration_source", Path(__file__).with_name(filename)
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Migration source unavailable")
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


FUNCTIONS: tuple[tuple[str, str], ...] = (
    (
        "confirm_bootstrap_email(UUID,TEXT)",
        """
CREATE FUNCTION public.confirm_bootstrap_email(p_code UUID,p_hash TEXT) RETURNS BOOLEAN AS $$
BEGIN
 UPDATE public.app_user u SET email_verified_at=COALESCE(u.email_verified_at,now())
 FROM public.email_code c WHERE c.id=p_code AND c.code_hash=p_hash AND c.identity_method='bootstrap'
 AND c.purpose='login' AND c.used_at IS NULL AND c.expires_at>now() AND u.email_lower=c.email_lower
 AND public.auth_email_code_matches(c.id,c.email_lower,p_hash)
 AND u.password_hash IS NULL AND u.status IN ('invited','active');
 RETURN FOUND;
END $$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp
""",
    ),
    (
        "lookup_identity_password(TEXT)",
        """
CREATE FUNCTION public.lookup_identity_password(p_email TEXT) RETURNS TEXT AS $$
 SELECT password_hash FROM public.app_user WHERE email_lower=lower(btrim(p_email))
 AND status IN ('invited','active') AND NOT EXISTS (
 SELECT 1 FROM public.platform_staff_account p WHERE p.user_id=app_user.id AND p.status='invited')
$$ LANGUAGE SQL STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp
""",
    ),
    (
        "lookup_account_identity(UUID,UUID)",
        """
CREATE FUNCTION public.lookup_account_identity(p_user UUID,p_session UUID) RETURNS JSONB AS $$
 SELECT jsonb_build_object('phone',u.verified_phone,'phone_verified',u.verified_phone IS NOT NULL,
 'has_password',u.password_hash IS NOT NULL,'password_prompt_pending',
 u.password_hash IS NULL AND u.password_prompt_dismissed_at IS NULL,'email',u.email)
 FROM public.app_user u WHERE u.id=p_user AND public.auth_settings_session_matches(p_user,p_session)
$$ LANGUAGE SQL STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp
""",
    ),
    (
        "dismiss_account_password_prompt(UUID,UUID)",
        """
CREATE FUNCTION public.dismiss_account_password_prompt(p_user UUID,p_session UUID) RETURNS BOOLEAN AS $$
BEGIN
 IF NOT public.auth_settings_session_matches(p_user,p_session) THEN RETURN false; END IF;
 UPDATE public.app_user SET password_prompt_dismissed_at=now() WHERE id=p_user;
 RETURN true;
END $$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp
""",
    ),
    (
        "issue_identity_code(TEXT,TEXT,TEXT,TEXT,TEXT,UUID,UUID,TEXT,TEXT,TEXT,SMALLINT,TEXT,BIGINT,TEXT,JSONB)",
        """
CREATE FUNCTION public.issue_identity_code(p_channel TEXT,p_contact TEXT,p_purpose TEXT,
 p_hash TEXT,p_salt TEXT,p_user UUID,p_session UUID,p_ip TEXT,p_agent TEXT,
 p_plaintext TEXT,p_key_version SMALLINT,p_key TEXT,p_mfa_counter BIGINT,p_mfa_secret TEXT,p_mfa_keyring JSONB) RETURNS JSONB AS $$
DECLARE v_user UUID; v_id UUID; v_email_id UUID; v_status TEXT; m public.support_mfa%ROWTYPE;
BEGIN
 IF p_channel NOT IN ('email','phone') OR p_purpose NOT IN ('login','reset','setup','reauth','bind')
 OR p_hash !~ '^[0-9a-f]{64}$' OR p_salt !~ '^[0-9a-f]{32}$'
 OR (p_channel='phone' AND p_contact !~ '^\\+992[0-9]{9}$') THEN
 RAISE EXCEPTION 'Invalid identity challenge' USING ERRCODE='22023'; END IF;
 IF p_purpose IN ('setup','reauth','bind') THEN
   IF NOT public.auth_settings_session_matches(p_user,p_session) THEN RETURN NULL; END IF;
   SELECT id INTO v_user FROM public.app_user WHERE id=p_user
   AND (p_purpose='bind' OR (p_channel='email' AND email_lower=p_contact)
     OR (p_channel='phone' AND verified_phone=p_contact));
   IF v_user IS NULL THEN RETURN NULL; END IF;
   IF p_purpose='bind' AND NOT EXISTS (SELECT 1 FROM public.auth_identity_code c
     WHERE c.user_id=p_user AND c.session_id=p_session AND c.purpose='reauth'
       AND c.used_at > now()-INTERVAL '5 minutes') AND NOT EXISTS (
       SELECT 1 FROM public.session s WHERE s.id=p_session
       AND s.password_verified_at > now()-INTERVAL '5 minutes') THEN RETURN NULL; END IF;
   IF p_purpose='bind' AND public.auth_account_requires_mfa(p_user) THEN
     SELECT * INTO m FROM public.support_mfa WHERE user_id=p_user FOR UPDATE;
     IF m.status<>'active' OR p_mfa_counter IS NULL OR p_mfa_counter<=COALESCE(m.last_used_counter,-1)
       OR p_mfa_secret IS DISTINCT FROM public.decrypt_support_mfa_secret(m.active_secret_ciphertext,m.active_key_version,p_mfa_keyring)
     THEN RETURN NULL; END IF;
   END IF;
 ELSE
   SELECT id INTO v_user FROM public.app_user WHERE status IN ('invited','active')
   AND ((p_channel='phone' AND verified_phone=p_contact)
     OR (p_channel='email' AND email_lower=p_contact AND email_verified_at IS NOT NULL));
 END IF;
 PERFORM pg_advisory_xact_lock(hashtextextended(p_channel||':'||p_contact,146));
 IF EXISTS(SELECT 1 FROM public.auth_identity_code WHERE channel=p_channel AND contact=p_contact
 AND created_at>now()-INTERVAL '1 minute') THEN RETURN jsonb_build_object('status','rate_limit_minute'); END IF;
 IF (SELECT count(*) FROM public.auth_identity_code WHERE channel=p_channel AND contact=p_contact
 AND created_at>now()-INTERVAL '1 hour')>=10 THEN RETURN jsonb_build_object('status','rate_limit_hour'); END IF;
 UPDATE public.auth_identity_code SET used_at=now() WHERE channel=p_channel AND contact=p_contact
 AND purpose=p_purpose AND used_at IS NULL;
 IF p_channel='email' THEN
   v_status:=public.issue_auth_email_code(p_contact,p_hash,p_salt,p_ip,p_agent,p_plaintext,p_key_version,p_key);
   IF v_status<>'created' THEN RETURN jsonb_build_object('status',v_status); END IF;
   SELECT id INTO v_email_id FROM public.email_code WHERE email_lower=p_contact AND code_hash=p_hash
     ORDER BY created_at DESC LIMIT 1;
   UPDATE public.email_code SET purpose='password_reset',expires_at=now()+INTERVAL '5 minutes' WHERE id=v_email_id;
 END IF;
 INSERT INTO public.auth_identity_code(channel,contact,purpose,user_id,session_id,code_hash,code_salt,email_code_id)
 VALUES(p_channel,p_contact,p_purpose,v_user,p_session,p_hash,p_salt,v_email_id) RETURNING id INTO v_id;
 IF p_purpose='bind' AND public.auth_account_requires_mfa(p_user) THEN
   UPDATE public.support_mfa SET last_used_counter=p_mfa_counter WHERE user_id=p_user;
 END IF;
 RETURN jsonb_build_object('status','created','deliver',v_user IS NOT NULL,'id',v_id);
END $$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp
""",
    ),
    (
        "find_identity_code(TEXT,TEXT,TEXT,UUID)",
        """
CREATE FUNCTION public.find_identity_code(p_channel TEXT,p_contact TEXT,p_purpose TEXT,p_session UUID)
RETURNS JSONB AS $$
 SELECT jsonb_build_object('id',id,'salt',code_salt) FROM public.auth_identity_code
 WHERE channel=p_channel AND contact=p_contact AND purpose=p_purpose
 AND session_id IS NOT DISTINCT FROM p_session AND used_at IS NULL AND expires_at>now() AND attempts<5
 ORDER BY created_at DESC LIMIT 1
$$ LANGUAGE SQL STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp
""",
    ),
    (
        "check_identity_code(UUID,TEXT,UUID)",
        """
CREATE FUNCTION public.check_identity_code(p_id UUID,p_hash TEXT,p_session UUID) RETURNS JSONB AS $$
DECLARE c public.auth_identity_code%ROWTYPE;
BEGIN
 SELECT * INTO c FROM public.auth_identity_code WHERE id=p_id FOR UPDATE;
 IF NOT FOUND OR c.used_at IS NOT NULL OR c.expires_at<=now() OR c.attempts>=5
 OR c.session_id IS DISTINCT FROM p_session THEN RETURN NULL; END IF;
 UPDATE public.auth_identity_code SET attempts=attempts+1 WHERE id=p_id;
 IF c.code_hash<>p_hash OR c.user_id IS NULL THEN RETURN NULL; END IF;
 IF c.session_id IS NOT NULL AND NOT public.auth_settings_session_matches(c.user_id,c.session_id) THEN RETURN NULL; END IF;
 RETURN jsonb_build_object('user_id',c.user_id,'purpose',c.purpose);
END $$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp
""",
    ),
    (
        "consume_identity_reauth(UUID,TEXT,UUID,UUID)",
        """
CREATE FUNCTION public.consume_identity_reauth(p_id UUID,p_hash TEXT,p_user UUID,p_session UUID) RETURNS BOOLEAN AS $$
BEGIN
 IF NOT public.auth_settings_session_matches(p_user,p_session) OR EXISTS(
 SELECT 1 FROM public.app_user WHERE id=p_user AND password_hash IS NOT NULL) THEN RETURN false; END IF;
 UPDATE public.auth_identity_code SET used_at=now() WHERE id=p_id AND code_hash=p_hash
 AND user_id=p_user AND session_id=p_session AND purpose='reauth' AND attempts<=5 AND used_at IS NULL AND expires_at>now();
 RETURN FOUND;
END $$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp
""",
    ),
    (
        "create_identity_login_proof(TEXT,TEXT,UUID,TEXT,TEXT,TEXT,TEXT)",
        """
CREATE FUNCTION public.create_identity_login_proof(p_email TEXT,p_verified_password TEXT,p_code UUID,p_candidate TEXT,
 p_hash TEXT,p_salt TEXT,p_ip TEXT) RETURNS JSONB AS $$
DECLARE u public.app_user%ROWTYPE; c public.auth_identity_code%ROWTYPE; v_id UUID;
BEGIN
 IF p_hash !~ '^[0-9a-f]{64}$' OR p_salt !~ '^[0-9a-f]{32}$' THEN RETURN NULL; END IF;
 IF p_code IS NULL THEN
   SELECT * INTO u FROM public.app_user WHERE email_lower=lower(btrim(p_email))
   AND password_hash=p_verified_password AND password_hash IS NOT NULL FOR UPDATE;
 ELSE
   SELECT * INTO c FROM public.auth_identity_code WHERE id=p_code AND purpose='login'
   AND channel='phone' AND code_hash=p_candidate AND attempts<=5 AND used_at IS NULL AND expires_at>now() FOR UPDATE;
   IF NOT FOUND THEN RETURN NULL; END IF;
   SELECT * INTO u FROM public.app_user WHERE id=c.user_id AND verified_phone=c.contact FOR UPDATE;
 END IF;
 IF u.id IS NULL OR u.status NOT IN ('invited','active') OR EXISTS(
 SELECT 1 FROM public.platform_staff_account p WHERE p.user_id=u.id AND p.status='invited') THEN RETURN NULL; END IF;
 IF u.home_tenant_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM public.tenant_membership
 WHERE user_id=u.id AND tenant_id=u.home_tenant_id AND status IN ('pending','active')) THEN RETURN NULL; END IF;
 IF p_code IS NOT NULL THEN UPDATE public.auth_identity_code SET used_at=now() WHERE id=p_code; END IF;
 INSERT INTO public.email_code(email_lower,code_hash,code_salt,purpose,ip_address,expires_at,identity_method)
 VALUES(u.email_lower,p_hash,p_salt,'login',p_ip::INET,now()+INTERVAL '30 seconds',
 CASE WHEN p_code IS NULL THEN 'password' ELSE 'phone' END) RETURNING id INTO v_id;
 RETURN jsonb_build_object('id',v_id,'email',u.email);
END $$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp
""",
    ),
    (
        "lookup_identity_factor(UUID,TEXT,UUID,JSONB)",
        """
CREATE FUNCTION public.lookup_identity_factor(p_id UUID,p_hash TEXT,p_session UUID,p_keyring JSONB) RETURNS JSONB AS $$
 SELECT jsonb_build_object('required',public.auth_account_requires_mfa(c.user_id),'status',m.status,
 'secret',CASE WHEN m.status='active' THEN public.decrypt_support_mfa_secret(m.active_secret_ciphertext,m.active_key_version,p_keyring) ELSE NULL END,
 'counter',m.last_used_counter,'user_id',c.user_id)
 FROM public.auth_identity_code c LEFT JOIN public.support_mfa m ON m.user_id=c.user_id
 WHERE c.id=p_id AND c.code_hash=p_hash AND c.attempts<=5 AND c.used_at IS NULL AND c.expires_at>now()
 AND c.session_id IS NOT DISTINCT FROM p_session AND c.purpose IN ('reset','setup')
$$ LANGUAGE SQL STABLE SECURITY DEFINER SET search_path=pg_catalog,pg_temp
""",
    ),
    (
        "complete_identity_password(UUID,TEXT,UUID,TEXT,BIGINT,TEXT,JSONB,TEXT)",
        """
CREATE FUNCTION public.complete_identity_password(p_id UUID,p_hash TEXT,p_session UUID,p_password TEXT,
 p_counter BIGINT,p_secret TEXT,p_keyring JSONB,p_recovery TEXT) RETURNS BOOLEAN AS $$
DECLARE c public.auth_identity_code%ROWTYPE; u public.app_user%ROWTYPE; m public.support_mfa%ROWTYPE;
BEGIN
 SELECT * INTO c FROM public.auth_identity_code WHERE id=p_id AND code_hash=p_hash
 AND attempts<=5 AND used_at IS NULL AND expires_at>now() AND purpose IN ('reset','setup') FOR UPDATE;
 IF NOT FOUND OR c.session_id IS DISTINCT FROM p_session THEN RETURN false; END IF;
 SELECT * INTO u FROM public.app_user WHERE id=c.user_id AND status IN ('invited','active') FOR UPDATE;
 IF NOT FOUND OR p_password IS NULL OR char_length(p_password)<20 THEN RETURN false; END IF;
 IF (c.channel='phone' AND u.verified_phone IS DISTINCT FROM c.contact)
 OR (c.channel='email' AND (u.email_lower<>c.contact OR (c.purpose='reset' AND u.email_verified_at IS NULL))) THEN RETURN false; END IF;
 IF c.purpose='setup' AND (u.password_hash IS NOT NULL OR NOT public.auth_settings_session_matches(u.id,p_session)) THEN RETURN false; END IF;
 IF public.auth_account_requires_mfa(u.id) THEN
   IF c.purpose='setup' AND EXISTS(SELECT 1 FROM public.session WHERE id=p_session AND user_id=u.id
      AND revoked_at IS NULL AND mfa_verified_at IS NOT NULL) THEN NULL;
   ELSE
     SELECT * INTO m FROM public.support_mfa WHERE user_id=u.id FOR UPDATE;
     IF p_recovery IS NOT NULL THEN
       UPDATE public.support_mfa_recovery_code SET used_at=now() WHERE user_id=u.id AND code_hash=p_recovery
       AND generation=m.active_generation AND activated_at IS NOT NULL AND used_at IS NULL;
       IF NOT FOUND THEN RETURN false; END IF;
     ELSE
       IF m.status<>'active' OR p_counter IS NULL OR p_counter<=COALESCE(m.last_used_counter,-1)
       OR p_secret IS DISTINCT FROM public.decrypt_support_mfa_secret(m.active_secret_ciphertext,m.active_key_version,p_keyring)
       THEN RETURN false; END IF;
       UPDATE public.support_mfa SET last_used_counter=p_counter WHERE user_id=u.id;
     END IF;
   END IF;
 END IF;
 UPDATE public.app_user SET password_hash=p_password,email_verified_at=CASE WHEN c.channel='email' THEN now() ELSE email_verified_at END,updated_at=now() WHERE id=u.id;
 UPDATE public.auth_identity_code SET used_at=now() WHERE user_id=u.id AND used_at IS NULL;
 UPDATE public.email_code SET used_at=now() WHERE email_lower=u.email_lower AND used_at IS NULL;
 UPDATE public.auth_mfa_challenge SET consumed_at=now() WHERE user_id=u.id AND consumed_at IS NULL;
 IF c.purpose='reset' THEN UPDATE public.session SET revoked_at=now() WHERE user_id=u.id AND revoked_at IS NULL; END IF;
 INSERT INTO public.audit_log(user_id,action,table_name,record_id,metadata) VALUES(u.id,'UPDATE','app_user',u.id,jsonb_build_object('event','account_password_changed','method',c.purpose));
 RETURN true;
END $$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp
""",
    ),
    (
        "complete_identity_phone(UUID,TEXT,UUID,UUID)",
        """
CREATE FUNCTION public.complete_identity_phone(p_id UUID,p_hash TEXT,p_user UUID,p_session UUID) RETURNS BOOLEAN AS $$
DECLARE c public.auth_identity_code%ROWTYPE;
BEGIN
 IF NOT public.auth_settings_session_matches(p_user,p_session) THEN RETURN false; END IF;
 SELECT * INTO c FROM public.auth_identity_code WHERE id=p_id AND code_hash=p_hash AND user_id=p_user
 AND session_id=p_session AND purpose='bind' AND channel='phone' AND attempts<=5 AND used_at IS NULL AND expires_at>now() FOR UPDATE;
 IF NOT FOUND THEN RETURN false; END IF;
 -- Issuance bound the new phone to a recent proof and originating live session.
 PERFORM pg_advisory_xact_lock(hashtextextended(c.contact,147));
 IF EXISTS(SELECT 1 FROM public.app_user WHERE verified_phone=c.contact AND id<>p_user) THEN RETURN false; END IF;
 UPDATE public.app_user SET verified_phone=c.contact,phone_verified_at=now(),updated_at=now() WHERE id=p_user;
 UPDATE public.auth_identity_code SET used_at=now() WHERE user_id=p_user AND used_at IS NULL;
 UPDATE public.auth_mfa_challenge SET consumed_at=now() WHERE user_id=p_user AND consumed_at IS NULL;
 UPDATE public.session SET revoked_at=now() WHERE user_id=p_user AND id<>p_session AND revoked_at IS NULL;
 INSERT INTO public.audit_log(user_id,action,table_name,record_id,metadata) VALUES(p_user,'UPDATE','app_user',p_user,jsonb_build_object('event','verified_phone_changed'));
 RETURN true;
END $$ LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path=pg_catalog,pg_temp
""",
    ),
)


def upgrade() -> None:
    schema_sql = """
ALTER TABLE public.app_user ADD COLUMN verified_phone TEXT UNIQUE,
 ADD COLUMN phone_verified_at TIMESTAMPTZ, ADD COLUMN email_verified_at TIMESTAMPTZ,
 ADD COLUMN password_prompt_dismissed_at TIMESTAMPTZ,
 ADD CONSTRAINT ck_verified_phone CHECK ((verified_phone IS NULL AND phone_verified_at IS NULL)
 OR (verified_phone ~ '^\\+992[0-9]{9}$' AND phone_verified_at IS NOT NULL));
UPDATE public.app_user SET email_verified_at=last_login_at WHERE last_login_at IS NOT NULL;
ALTER TABLE public.email_code ADD COLUMN identity_method TEXT NOT NULL DEFAULT 'bootstrap'
 CHECK(identity_method IN ('bootstrap','password','phone'));
CREATE TABLE public.auth_identity_code (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(), channel TEXT NOT NULL CHECK(channel IN ('email','phone')),
 contact TEXT NOT NULL, purpose TEXT NOT NULL CHECK(purpose IN ('login','reset','setup','reauth','bind')),
 user_id UUID REFERENCES public.app_user(id) ON DELETE CASCADE,
 session_id UUID REFERENCES public.session(id) ON DELETE CASCADE,
 code_hash TEXT NOT NULL, code_salt TEXT NOT NULL, attempts SMALLINT NOT NULL DEFAULT 0,
 email_code_id UUID REFERENCES public.email_code(id) ON DELETE SET NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(), expires_at TIMESTAMPTZ NOT NULL DEFAULT now()+INTERVAL '5 minutes', used_at TIMESTAMPTZ);
ALTER TABLE public.auth_identity_code OWNER TO aurum_schema_owner;
REVOKE ALL ON public.auth_identity_code FROM PUBLIC,aurum_app,aurum_support;
CREATE INDEX ix_identity_code_lookup ON public.auth_identity_code(channel,contact,purpose,created_at DESC);
CREATE INDEX ix_identity_code_expiry ON public.auth_identity_code(expires_at,id);
"""
    for statement in schema_sql.split(";"):
        if statement.strip():
            op.execute(statement)
    for signature, sql in FUNCTIONS:
        op.execute(sql)
        op.execute(f"ALTER FUNCTION public.{signature} OWNER TO aurum_schema_owner")
        op.execute(f"REVOKE ALL ON FUNCTION public.{signature} FROM PUBLIC,aurum_app,aurum_support")
        grantees = (
            "aurum_support,aurum_app"
            if signature.startswith("confirm_bootstrap_email(")
            else "aurum_support"
        )
        op.execute(f"GRANT EXECUTE ON FUNCTION public.{signature} TO {grantees}")
    # Codes for recovery/setup are purpose-separated but use the same encrypted mail outbox.
    mail = _source("0111_add_auth_email_outbox.py")
    op.execute(
        mail.ISSUE_SQL.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1).replace(
            "INTERVAL '10 minutes'", "INTERVAL '5 minutes'"
        )
    )
    op.execute(
        mail.CLAIM_SQL.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1).replace(
            "code.purpose = 'login'", "code.purpose IN ('login','password_reset')"
        )
    )
    _login_boundary(restore=False)
    _cleanup_boundary(restore=False)


def _cleanup_boundary(*, restore: bool) -> None:
    worker = _source("0137_isolate_system_worker.py")
    sql = worker.PURGE_EMAIL_CODES_SQL.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)
    if not restore:
        sql = sql.replace(
            "  v_removed INTEGER;", "  v_removed INTEGER;\n  v_identity_removed INTEGER;"
        )
        sql = sql.replace(
            "  RETURN v_removed;",
            """  WITH candidates AS (
    SELECT id FROM public.auth_identity_code
    WHERE expires_at < statement_timestamp()-INTERVAL '24 hours'
    ORDER BY expires_at,id LIMIT GREATEST(p_limit-v_removed,0)
    FOR UPDATE SKIP LOCKED
  ) DELETE FROM public.auth_identity_code c USING candidates WHERE c.id=candidates.id;
  GET DIAGNOSTICS v_identity_removed = ROW_COUNT;
  RETURN v_removed+v_identity_removed;""",
        )
    op.execute(sql)


def _login_boundary(*, restore: bool) -> None:
    invitations = _source("0128_add_tenant_invitation_lifecycle.py")
    source = _source("0056_add_support_mfa.py")
    owner = _source("0115_require_owner_mfa.py")
    # The latest matcher also rejects codes tied to expired/revoked invitations.
    # Rebuilding from 0037 would silently remove that boundary on upgrade/downgrade.
    matches = invitations.MATCH_EMAIL_CODE_SQL
    standard = (
        source.CREATE_STANDARD_SESSION_SQL.replace(
            "CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1
        )
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
    mfa = owner._account_mfa_challenge(source.CREATE_MFA_CHALLENGE_SQL)
    if not restore:
        matches = matches.replace(
            "      AND code.purpose = 'login'",
            """      AND code.purpose = 'login'
      AND (code.identity_method <> 'bootstrap' OR NOT EXISTS (
        SELECT 1 FROM public.app_user u WHERE u.email_lower=code.email_lower AND u.password_hash IS NOT NULL))""",
        )
        standard = standard.replace(
            "  v_ip :=",
            """  IF NOT public.auth_email_code_matches(p_code_id,p_email,p_candidate_hash) THEN RETURN NULL; END IF;
  v_ip :=""",
            1,
        )
        mfa = mfa.replace(
            "  v_ip :=",
            """  IF NOT public.auth_email_code_matches(p_code_id,p_email,p_candidate_hash) THEN RETURN; END IF;
  v_ip :=""",
            1,
        )
    for sql in (matches, standard, mfa):
        op.execute(sql)


def downgrade() -> None:
    _cleanup_boundary(restore=True)
    _login_boundary(restore=True)
    mail = _source("0111_add_auth_email_outbox.py")
    op.execute(mail.ISSUE_SQL.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    op.execute(mail.CLAIM_SQL.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1))
    for signature, _ in reversed(FUNCTIONS):
        op.execute(f"DROP FUNCTION public.{signature}")
    op.execute("DROP TABLE public.auth_identity_code")
    op.execute("ALTER TABLE public.email_code DROP COLUMN identity_method")
    op.execute(
        "ALTER TABLE public.app_user DROP COLUMN verified_phone, DROP COLUMN phone_verified_at, DROP COLUMN email_verified_at, DROP COLUMN password_prompt_dismissed_at"
    )
