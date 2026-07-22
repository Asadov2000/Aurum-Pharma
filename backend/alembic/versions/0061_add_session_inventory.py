"""Add identity-bound self-service session management.

Revision ID: 0061
Revises: 0060
Create Date: 2026-07-19

The runtime role keeps no direct access to the authentication session table.
These SECURITY DEFINER functions expose only safe metadata and always bind
reads and mutations to ``app.user_id`` from the authenticated request.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0061"
down_revision: str | Sequence[str] | None = "0060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


LOOKUP_AUTH_SESSIONS_SQL = """
CREATE FUNCTION public.lookup_auth_sessions(
  p_user_id UUID,
  p_current_session_id UUID
) RETURNS TABLE(
  id UUID,
  user_agent TEXT,
  ip_address TEXT,
  created_at TIMESTAMPTZ,
  last_used_at TIMESTAMPTZ,
  expires_at TIMESTAMPTZ,
  is_current BOOLEAN
) AS $$
BEGIN
  IF p_user_id IS NULL
    OR p_user_id IS DISTINCT FROM public.current_app_user_id()
  THEN
    RAISE EXCEPTION 'Authentication sessions are unavailable'
      USING ERRCODE = '42501';
  END IF;

  RETURN QUERY
  SELECT
    auth_session.id,
    pg_catalog.left(auth_session.user_agent, 512),
    CASE
      WHEN auth_session.ip_address IS NULL THEN NULL
      ELSE pg_catalog.host(auth_session.ip_address)
    END,
    auth_session.created_at,
    auth_session.last_used_at,
    auth_session.expires_at,
    auth_session.id = p_current_session_id
  FROM public.session AS auth_session
  WHERE auth_session.user_id = p_user_id
    AND auth_session.revoked_at IS NULL
    AND auth_session.expires_at > pg_catalog.now()
  ORDER BY
    (auth_session.id = p_current_session_id) DESC,
    auth_session.last_used_at DESC,
    auth_session.created_at DESC
  LIMIT 50;
END;
$$ LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


REVOKE_AUTH_SESSION_BY_ID_SQL = """
CREATE FUNCTION public.revoke_auth_session_by_id(
  p_user_id UUID,
  p_session_id UUID,
  p_current_session_id UUID
) RETURNS TEXT AS $$
BEGIN
  IF p_user_id IS NULL
    OR p_user_id IS DISTINCT FROM public.current_app_user_id()
  THEN
    RAISE EXCEPTION 'Authentication session is unavailable'
      USING ERRCODE = '42501';
  END IF;

  IF p_current_session_id IS NULL OR NOT EXISTS (
    SELECT 1
    FROM public.session AS current_session
    WHERE current_session.id = p_current_session_id
      AND current_session.user_id = p_user_id
      AND current_session.revoked_at IS NULL
      AND current_session.expires_at > pg_catalog.now()
  ) THEN
    RAISE EXCEPTION 'Current authentication session is unavailable'
      USING ERRCODE = '42501';
  END IF;

  IF p_session_id = p_current_session_id THEN
    RETURN 'current';
  END IF;

  UPDATE public.session AS auth_session
  SET
    revoked_at = pg_catalog.now(),
    revoked_reason = 'user_revoked',
    last_used_at = pg_catalog.now()
  WHERE auth_session.id = p_session_id
    AND auth_session.user_id = p_user_id
    AND auth_session.revoked_at IS NULL
    AND auth_session.expires_at > pg_catalog.now();

  IF FOUND THEN
    RETURN 'revoked';
  END IF;
  RETURN 'not_found';
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


REVOKE_OTHER_AUTH_SESSIONS_SQL = """
CREATE FUNCTION public.revoke_other_auth_sessions(
  p_user_id UUID,
  p_current_session_id UUID
) RETURNS INTEGER AS $$
DECLARE
  v_revoked_count INTEGER;
BEGIN
  IF p_user_id IS NULL
    OR p_user_id IS DISTINCT FROM public.current_app_user_id()
  THEN
    RAISE EXCEPTION 'Authentication sessions are unavailable'
      USING ERRCODE = '42501';
  END IF;

  IF p_current_session_id IS NULL OR NOT EXISTS (
    SELECT 1
    FROM public.session AS current_session
    WHERE current_session.id = p_current_session_id
      AND current_session.user_id = p_user_id
      AND current_session.revoked_at IS NULL
      AND current_session.expires_at > pg_catalog.now()
  ) THEN
    RAISE EXCEPTION 'Current authentication session is unavailable'
      USING ERRCODE = '42501';
  END IF;

  UPDATE public.session AS auth_session
  SET
    revoked_at = pg_catalog.now(),
    revoked_reason = 'user_revoked_others',
    last_used_at = pg_catalog.now()
  WHERE auth_session.user_id = p_user_id
    AND auth_session.id <> p_current_session_id
    AND auth_session.revoked_at IS NULL
    AND auth_session.expires_at > pg_catalog.now();

  GET DIAGNOSTICS v_revoked_count = ROW_COUNT;
  RETURN v_revoked_count;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


FUNCTION_SIGNATURES = (
    "public.lookup_auth_sessions(UUID, UUID)",
    "public.revoke_auth_session_by_id(UUID, UUID, UUID)",
    "public.revoke_other_auth_sessions(UUID, UUID)",
)


def _secure_function(signature: str) -> None:
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_support")
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} FROM PUBLIC, aurum_app")
    op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO aurum_support, aurum_app")


def upgrade() -> None:
    op.execute(
        "CREATE INDEX ix_session_user_last_used_active "
        "ON public.session (user_id, last_used_at DESC) "
        "WHERE revoked_at IS NULL"
    )
    op.execute(LOOKUP_AUTH_SESSIONS_SQL)
    op.execute(REVOKE_AUTH_SESSION_BY_ID_SQL)
    op.execute(REVOKE_OTHER_AUTH_SESSIONS_SQL)
    for signature in FUNCTION_SIGNATURES:
        _secure_function(signature)


def downgrade() -> None:
    for signature in reversed(FUNCTION_SIGNATURES):
        op.execute(f"DROP FUNCTION {signature}")
    op.execute("DROP INDEX public.ix_session_user_last_used_active")
