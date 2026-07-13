"""security: enforce tenant RLS through runtime database views

Revision ID: 0029
Revises: 0028
Create Date: 2026-07-13
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0029"
down_revision: str | Sequence[str] | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


RUNTIME_VIEWS = (
    "v_active_subscription",
    "v_batch_with_expiry_status",
)

RUNTIME_VIEW_GUARD_SQL = """
DO $$
DECLARE
  v_view TEXT;
  v_owner TEXT;
  v_options TEXT[];
  v_privileges TEXT[];
  v_alembic_privileges TEXT[];
BEGIN
  FOREACH v_view IN ARRAY ARRAY[
    'v_active_subscription',
    'v_batch_with_expiry_status'
  ]
  LOOP
    SELECT
      pg_catalog.pg_get_userbyid(relations.relowner),
      relations.reloptions
    INTO v_owner, v_options
    FROM pg_catalog.pg_class AS relations
    JOIN pg_catalog.pg_namespace AS schemas
      ON schemas.oid = relations.relnamespace
    WHERE schemas.nspname = 'public'
      AND relations.relname = v_view
      AND relations.relkind = 'v';

    IF v_owner IS DISTINCT FROM 'aurum_support' THEN
      RAISE EXCEPTION 'public.% must be owned by aurum_support', v_view;
    END IF;
    IF NOT COALESCE(v_options, ARRAY[]::TEXT[]) @> ARRAY['security_invoker=true'] THEN
      RAISE EXCEPTION 'public.% must use security_invoker', v_view;
    END IF;

    SELECT pg_catalog.array_agg(grants.privilege_type ORDER BY grants.privilege_type)
    INTO v_privileges
    FROM information_schema.role_table_grants AS grants
    WHERE grants.table_schema = 'public'
      AND grants.table_name = v_view
      AND grants.grantee = 'aurum_app';

    IF v_privileges IS DISTINCT FROM ARRAY['SELECT']::TEXT[] THEN
      RAISE EXCEPTION
        'aurum_app privileges on public.% must be SELECT only: %',
        v_view,
        v_privileges;
    END IF;
  END LOOP;

  SELECT pg_catalog.array_agg(grants.privilege_type ORDER BY grants.privilege_type)
  INTO v_alembic_privileges
  FROM information_schema.role_table_grants AS grants
  WHERE grants.table_schema = 'public'
    AND grants.table_name = 'alembic_version'
    AND grants.grantee = 'aurum_app';

  IF v_alembic_privileges IS NOT NULL THEN
    RAISE EXCEPTION
      'aurum_app must not access public.alembic_version: %',
      v_alembic_privileges;
  END IF;
END
$$
"""


def upgrade() -> None:
    for view in RUNTIME_VIEWS:
        op.execute(f"ALTER VIEW public.{view} SET (security_invoker = true)")
        op.execute(f"REVOKE ALL PRIVILEGES ON TABLE public.{view} FROM aurum_app")
        op.execute(f"GRANT SELECT ON TABLE public.{view} TO aurum_app")

    op.execute("REVOKE ALL PRIVILEGES ON TABLE public.alembic_version FROM aurum_app")
    op.execute(RUNTIME_VIEW_GUARD_SQL)


def downgrade() -> None:
    for view in RUNTIME_VIEWS:
        op.execute(f"ALTER VIEW public.{view} RESET (security_invoker)")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.{view} TO aurum_app")

    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE " "ON TABLE public.alembic_version TO aurum_app"
    )
