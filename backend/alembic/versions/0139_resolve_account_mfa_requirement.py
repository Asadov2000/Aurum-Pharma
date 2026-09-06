"""Resolve account MFA requirements through the authenticated identity boundary.

Revision ID: 0139
Revises: 0138
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0139"
down_revision: str | Sequence[str] | None = "0138"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

FUNCTION = "public.lookup_auth_account_mfa_requirement(UUID, UUID)"


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION public.lookup_auth_account_mfa_requirement(
          p_user_id UUID,
          p_session_id UUID
        ) RETURNS BOOLEAN AS $$
          SELECT public.auth_account_requires_mfa(identity.id)
          FROM public.lookup_auth_user_by_id(p_user_id, p_session_id) AS identity
        $$ LANGUAGE SQL
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        """)
    op.execute(f"ALTER FUNCTION {FUNCTION} OWNER TO aurum_schema_owner")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {FUNCTION} FROM PUBLIC, aurum_app, aurum_support"
    )
    op.execute(f"GRANT EXECUTE ON FUNCTION {FUNCTION} TO aurum_app, aurum_support")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION {FUNCTION}")
