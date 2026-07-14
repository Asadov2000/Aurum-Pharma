"""auth: make refresh rotation retry-safe

Revision ID: 0038
Revises: 0037
Create Date: 2026-07-14

A refresh response may disappear after the database commit. Rotated sessions
therefore record the client operation that created them and their predecessor.
The security-definer function can return the same child for the same operation
without accepting a replay under a different operation id.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0038"
down_revision: str | Sequence[str] | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ROTATE_AUTH_SESSION_SQL = """
CREATE FUNCTION public.rotate_auth_session(
  p_old_token_hash TEXT,
  p_new_token_hash TEXT,
  p_operation_id UUID,
  p_user_agent TEXT,
  p_ip_address TEXT,
  p_expires_at TIMESTAMPTZ
) RETURNS TABLE(
  id UUID,
  user_id UUID,
  expires_at TIMESTAMPTZ,
  reuse_presented_token BOOLEAN
) AS $$
DECLARE
  v_presented_session_id UUID;
  v_user_id UUID;
  v_presented_expires_at TIMESTAMPTZ;
  v_revoked_at TIMESTAMPTZ;
  v_revoked_reason TEXT;
  v_rotation_operation_id UUID;
  v_result_session_id UUID;
  v_result_expires_at TIMESTAMPTZ;
  v_ip INET;
BEGIN
  IF p_old_token_hash !~ '^[0-9a-f]{64}$'
    OR p_new_token_hash !~ '^[0-9a-f]{64}$'
    OR p_old_token_hash = p_new_token_hash
    OR p_operation_id IS NULL
    OR p_expires_at IS NULL
    OR p_expires_at <= pg_catalog.now()
    OR p_expires_at > pg_catalog.now() + INTERVAL '31 days'
  THEN
    RAISE EXCEPTION 'Invalid refresh-rotation payload'
      USING ERRCODE = '22023';
  END IF;

  v_ip := NULLIF(pg_catalog.btrim(p_ip_address), '')::INET;

  SELECT
    auth_session.id,
    auth_session.user_id,
    auth_session.expires_at,
    auth_session.revoked_at,
    auth_session.revoked_reason,
    auth_session.rotation_operation_id
  INTO
    v_presented_session_id,
    v_user_id,
    v_presented_expires_at,
    v_revoked_at,
    v_revoked_reason,
    v_rotation_operation_id
  FROM public.session AS auth_session
  WHERE auth_session.refresh_token_hash = p_old_token_hash
    AND auth_session.expires_at > pg_catalog.now()
  FOR UPDATE;

  IF v_presented_session_id IS NULL THEN
    RETURN;
  END IF;

  IF v_revoked_at IS NULL THEN
    IF NOT EXISTS (
      SELECT 1
      FROM public.app_user AS app_user
      WHERE app_user.id = v_user_id
        AND app_user.status IN ('invited', 'active')
    ) THEN
      UPDATE public.session AS auth_session
      SET
        revoked_at = pg_catalog.now(),
        revoked_reason = 'user_inactive',
        last_used_at = pg_catalog.now()
      WHERE auth_session.id = v_presented_session_id;
      RETURN;
    END IF;

    -- The browser may have accepted Set-Cookie before the response body was
    -- interrupted. In that case the presented session is already the result.
    IF v_rotation_operation_id = p_operation_id THEN
      UPDATE public.session AS auth_session
      SET last_used_at = pg_catalog.now()
      WHERE auth_session.id = v_presented_session_id;

      RETURN QUERY
      SELECT
        v_presented_session_id,
        v_user_id,
        v_presented_expires_at,
        true;
      RETURN;
    END IF;

    UPDATE public.session AS auth_session
    SET
      revoked_at = pg_catalog.now(),
      revoked_reason = 'rotated',
      last_used_at = pg_catalog.now()
    WHERE auth_session.id = v_presented_session_id;

    INSERT INTO public.session (
      user_id,
      refresh_token_hash,
      user_agent,
      ip_address,
      expires_at,
      rotation_operation_id,
      rotated_from_session_id
    ) VALUES (
      v_user_id,
      p_new_token_hash,
      pg_catalog.left(p_user_agent, 1024),
      v_ip,
      p_expires_at,
      p_operation_id,
      v_presented_session_id
    )
    RETURNING session.id INTO v_result_session_id;

    RETURN QUERY
    SELECT v_result_session_id, v_user_id, p_expires_at, false;
    RETURN;
  END IF;

  IF v_revoked_reason <> 'rotated' THEN
    RETURN;
  END IF;

  SELECT child.id, child.user_id, child.expires_at
  INTO v_result_session_id, v_user_id, v_result_expires_at
  FROM public.session AS child
  WHERE child.rotated_from_session_id = v_presented_session_id
    AND child.rotation_operation_id = p_operation_id
    AND child.refresh_token_hash = p_new_token_hash
    AND child.revoked_at IS NULL
    AND child.expires_at > pg_catalog.now()
  FOR UPDATE;

  IF v_result_session_id IS NULL THEN
    RETURN;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.app_user AS app_user
    WHERE app_user.id = v_user_id
      AND app_user.status IN ('invited', 'active')
  ) THEN
    UPDATE public.session AS auth_session
    SET
      revoked_at = pg_catalog.now(),
      revoked_reason = 'user_inactive',
      last_used_at = pg_catalog.now()
    WHERE auth_session.id = v_result_session_id;
    RETURN;
  END IF;

  UPDATE public.session AS auth_session
  SET last_used_at = pg_catalog.now()
  WHERE auth_session.id = v_result_session_id;

  RETURN QUERY
  SELECT v_result_session_id, v_user_id, v_result_expires_at, false;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


REVOKE_AUTH_SESSION_BY_HASH_SQL = """
CREATE FUNCTION public.revoke_auth_session_by_hash(
  p_token_hash TEXT,
  p_reason TEXT,
  p_operation_id UUID
) RETURNS UUID AS $$
DECLARE
  v_session_id UUID;
  v_user_id UUID;
  v_revoked_at TIMESTAMPTZ;
  v_revoked_reason TEXT;
  v_child_session_id UUID;
BEGIN
  IF p_token_hash !~ '^[0-9a-f]{64}$'
    OR p_reason NOT IN ('logout', 'user_inactive')
  THEN
    RAISE EXCEPTION 'Invalid session-revocation payload'
      USING ERRCODE = '22023';
  END IF;

  SELECT
    auth_session.id,
    auth_session.user_id,
    auth_session.revoked_at,
    auth_session.revoked_reason
  INTO v_session_id, v_user_id, v_revoked_at, v_revoked_reason
  FROM public.session AS auth_session
  WHERE auth_session.refresh_token_hash = p_token_hash
  FOR UPDATE;

  IF v_session_id IS NULL THEN
    RETURN NULL;
  END IF;

  IF v_revoked_at IS NULL THEN
    UPDATE public.session AS auth_session
    SET
      revoked_at = pg_catalog.now(),
      revoked_reason = p_reason,
      last_used_at = pg_catalog.now()
    WHERE auth_session.id = v_session_id;
    RETURN v_user_id;
  END IF;

  IF p_operation_id IS NULL OR v_revoked_reason <> 'rotated' THEN
    RETURN NULL;
  END IF;

  SELECT child.id, child.user_id
  INTO v_child_session_id, v_user_id
  FROM public.session AS child
  WHERE child.rotated_from_session_id = v_session_id
    AND child.rotation_operation_id = p_operation_id
    AND child.revoked_at IS NULL
  FOR UPDATE;

  IF v_child_session_id IS NULL THEN
    RETURN NULL;
  END IF;

  UPDATE public.session AS auth_session
  SET
    revoked_at = pg_catalog.now(),
    revoked_reason = p_reason,
    last_used_at = pg_catalog.now()
  WHERE auth_session.id = v_child_session_id;

  RETURN v_user_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


RESTORE_LOOKUP_AUTH_SESSION_SQL = """
CREATE FUNCTION public.lookup_auth_session_by_hash(
  p_token_hash TEXT
) RETURNS TABLE(id UUID, user_id UUID, expires_at TIMESTAMPTZ) AS $$
  SELECT auth_session.id, auth_session.user_id, auth_session.expires_at
  FROM public.session AS auth_session
  WHERE auth_session.refresh_token_hash = p_token_hash
    AND auth_session.revoked_at IS NULL
    AND auth_session.expires_at > pg_catalog.now()
  LIMIT 1
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


RESTORE_ROTATE_AUTH_SESSION_SQL = """
CREATE FUNCTION public.rotate_auth_session(
  p_old_token_hash TEXT,
  p_new_token_hash TEXT,
  p_user_agent TEXT,
  p_ip_address TEXT,
  p_expires_at TIMESTAMPTZ
) RETURNS TABLE(id UUID, user_id UUID, expires_at TIMESTAMPTZ) AS $$
DECLARE
  v_old_session_id UUID;
  v_user_id UUID;
  v_new_session_id UUID;
  v_ip INET;
BEGIN
  IF p_old_token_hash !~ '^[0-9a-f]{64}$'
    OR p_new_token_hash !~ '^[0-9a-f]{64}$'
    OR p_old_token_hash = p_new_token_hash
    OR p_expires_at IS NULL
    OR p_expires_at <= pg_catalog.now()
    OR p_expires_at > pg_catalog.now() + INTERVAL '31 days'
  THEN
    RAISE EXCEPTION 'Invalid refresh-rotation payload'
      USING ERRCODE = '22023';
  END IF;

  v_ip := NULLIF(pg_catalog.btrim(p_ip_address), '')::INET;

  SELECT auth_session.id, auth_session.user_id
  INTO v_old_session_id, v_user_id
  FROM public.session AS auth_session
  WHERE auth_session.refresh_token_hash = p_old_token_hash
    AND auth_session.revoked_at IS NULL
    AND auth_session.expires_at > pg_catalog.now()
  FOR UPDATE;

  IF v_old_session_id IS NULL THEN
    RETURN;
  END IF;

  IF NOT EXISTS (
    SELECT 1
    FROM public.app_user AS app_user
    WHERE app_user.id = v_user_id
      AND app_user.status IN ('invited', 'active')
  ) THEN
    UPDATE public.session AS auth_session
    SET
      revoked_at = pg_catalog.now(),
      revoked_reason = 'user_inactive'
    WHERE auth_session.id = v_old_session_id;
    RETURN;
  END IF;

  UPDATE public.session AS auth_session
  SET
    revoked_at = pg_catalog.now(),
    revoked_reason = 'rotated',
    last_used_at = pg_catalog.now()
  WHERE auth_session.id = v_old_session_id;

  INSERT INTO public.session (
    user_id,
    refresh_token_hash,
    user_agent,
    ip_address,
    expires_at
  ) VALUES (
    v_user_id,
    p_new_token_hash,
    pg_catalog.left(p_user_agent, 1024),
    v_ip,
    p_expires_at
  )
  RETURNING session.id INTO v_new_session_id;

  RETURN QUERY
  SELECT v_new_session_id, v_user_id, p_expires_at;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


RESTORE_REVOKE_AUTH_SESSION_SQL = """
CREATE FUNCTION public.revoke_auth_session_by_hash(
  p_token_hash TEXT,
  p_reason TEXT
) RETURNS UUID AS $$
DECLARE
  v_user_id UUID;
BEGIN
  IF p_reason NOT IN ('logout', 'user_inactive') THEN
    RAISE EXCEPTION 'Invalid session-revocation reason'
      USING ERRCODE = '22023';
  END IF;

  UPDATE public.session AS auth_session
  SET
    revoked_at = pg_catalog.now(),
    revoked_reason = p_reason,
    last_used_at = pg_catalog.now()
  WHERE auth_session.refresh_token_hash = p_token_hash
    AND auth_session.revoked_at IS NULL
  RETURNING auth_session.user_id INTO v_user_id;

  RETURN v_user_id;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


NEW_ROTATE_SIGNATURE = (
    "public.rotate_auth_session(TEXT, TEXT, UUID, TEXT, TEXT, TIMESTAMP WITH TIME ZONE)"
)
NEW_REVOKE_SIGNATURE = "public.revoke_auth_session_by_hash(TEXT, TEXT, UUID)"


def _configure_function_acl(function: str) -> None:
    op.execute(f"ALTER FUNCTION {function} OWNER TO aurum_support")
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {function} FROM PUBLIC, aurum_app")
    op.execute(f"GRANT EXECUTE ON FUNCTION {function} TO aurum_support, aurum_app")


def upgrade() -> None:
    op.execute("ALTER TABLE public.session ADD COLUMN rotation_operation_id UUID")
    op.execute("ALTER TABLE public.session ADD COLUMN rotated_from_session_id UUID")
    op.execute(
        "ALTER TABLE public.session ADD CONSTRAINT fk_session_rotated_from "
        "FOREIGN KEY (rotated_from_session_id) REFERENCES public.session(id) "
        "ON DELETE SET NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_session_rotated_from "
        "ON public.session (rotated_from_session_id) "
        "WHERE rotated_from_session_id IS NOT NULL"
    )

    op.execute("DROP FUNCTION public.lookup_auth_session_by_hash(TEXT)")
    op.execute(
        "DROP FUNCTION public.rotate_auth_session("
        "TEXT, TEXT, TEXT, TEXT, TIMESTAMP WITH TIME ZONE)"
    )
    op.execute("DROP FUNCTION public.revoke_auth_session_by_hash(TEXT, TEXT)")

    op.execute(ROTATE_AUTH_SESSION_SQL)
    op.execute(REVOKE_AUTH_SESSION_BY_HASH_SQL)
    _configure_function_acl(NEW_ROTATE_SIGNATURE)
    _configure_function_acl(NEW_REVOKE_SIGNATURE)


def downgrade() -> None:
    op.execute(f"DROP FUNCTION {NEW_REVOKE_SIGNATURE}")
    op.execute(f"DROP FUNCTION {NEW_ROTATE_SIGNATURE}")

    op.execute(RESTORE_LOOKUP_AUTH_SESSION_SQL)
    op.execute(RESTORE_ROTATE_AUTH_SESSION_SQL)
    op.execute(RESTORE_REVOKE_AUTH_SESSION_SQL)
    for function in (
        "public.lookup_auth_session_by_hash(TEXT)",
        "public.rotate_auth_session(TEXT, TEXT, TEXT, TEXT, TIMESTAMP WITH TIME ZONE)",
        "public.revoke_auth_session_by_hash(TEXT, TEXT)",
    ):
        _configure_function_acl(function)

    op.execute("DROP INDEX public.ux_session_rotated_from")
    op.execute("ALTER TABLE public.session DROP CONSTRAINT fk_session_rotated_from")
    op.execute("ALTER TABLE public.session DROP COLUMN rotated_from_session_id")
    op.execute("ALTER TABLE public.session DROP COLUMN rotation_operation_id")
