"""security: bind scoped support access to one auth-session family

Revision ID: 0063
Revises: 0062
Create Date: 2026-07-22

Support access follows refresh-token rotation on the device that opened it, but
cannot be reused by another login of the same account. Authorization branches
for platform support and tenant members are also made mutually exclusive.
"""

from __future__ import annotations

from collections.abc import Sequence
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0063"
down_revision: str | Sequence[str] | None = "0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


IS_TENANT_SUPPORT_SESSION_SQL = """
CREATE OR REPLACE FUNCTION public.is_tenant_support_session()
RETURNS BOOLEAN AS $$
  SELECT session_user IN ('aurum_app', 'aurum_support')
    AND COALESCE(pg_catalog.current_setting('app.support_session', true), '') = 'true'
    AND NULLIF(
      pg_catalog.current_setting('app.support_access_session_id', true),
      ''
    ) IS NOT NULL
    AND NULLIF(
      pg_catalog.current_setting('app.auth_session_id', true),
      ''
    ) IS NOT NULL
    AND EXISTS (
      SELECT 1
      FROM public.support_access_session AS access_session
      JOIN public.app_user AS actor
        ON actor.id = access_session.actor_user_id
      JOIN public.tenant AS tenant
        ON tenant.id = access_session.tenant_id
      WHERE access_session.id = NULLIF(
          pg_catalog.current_setting('app.support_access_session_id', true),
          ''
        )::UUID
        AND access_session.actor_user_id = NULLIF(
          pg_catalog.current_setting('app.user_id', true),
          ''
        )::UUID
        AND access_session.tenant_id = NULLIF(
          pg_catalog.current_setting('app.tenant_id', true),
          ''
        )::UUID
        AND access_session.revoked_at IS NULL
        AND access_session.expires_at > pg_catalog.statement_timestamp()
        AND actor.status = 'active'
        AND (actor.is_developer OR actor.is_administrator)
        AND access_session.actor_session_id IN (
          WITH RECURSIVE auth_lineage AS (
            SELECT auth_session.id, auth_session.rotated_from_session_id
            FROM public.session AS auth_session
            WHERE auth_session.id = NULLIF(
                pg_catalog.current_setting('app.auth_session_id', true),
                ''
              )::UUID
              AND auth_session.user_id = access_session.actor_user_id
              AND auth_session.revoked_at IS NULL
              AND auth_session.expires_at > pg_catalog.statement_timestamp()

            UNION ALL

            SELECT parent.id, parent.rotated_from_session_id
            FROM public.session AS parent
            JOIN auth_lineage AS child
              ON parent.id = child.rotated_from_session_id
            WHERE parent.user_id = access_session.actor_user_id
          )
          SELECT auth_lineage.id FROM auth_lineage
        )
    )
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


SUPPORT_HAS_CAPABILITY_SQL = """
CREATE OR REPLACE FUNCTION public.support_access_has_capability(
  p_permission_code TEXT
) RETURNS BOOLEAN AS $$
  SELECT public.is_support_session()
    OR (
      public.is_tenant_support_session()
      AND EXISTS (
        SELECT 1
        FROM public.support_access_capability AS capability
        WHERE capability.support_access_session_id = NULLIF(
            pg_catalog.current_setting('app.support_access_session_id', true),
            ''
          )::UUID
          AND capability.tenant_id = public.current_tenant_id()
          AND capability.permission_code = p_permission_code
      )
      AND (
        p_permission_code NOT IN ('roles.assign', 'roles.create', 'roles.update')
        OR EXISTS (
          SELECT 1
          FROM public.tenant AS tenant
          WHERE tenant.id = public.current_tenant_id()
            AND tenant.status <> 'archived'
        )
      )
    )
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


ACTIVE_PERMISSION_GATE_SQL = """
CREATE OR REPLACE FUNCTION public.tenant_actor_has_permission(
  p_tenant_id UUID,
  p_permission_code TEXT
) RETURNS BOOLEAN AS $$
  SELECT public.is_support_session()
    OR (
      public.is_tenant_support_session()
      AND p_tenant_id IS NOT DISTINCT FROM public.current_tenant_id()
      AND public.support_access_has_capability(p_permission_code)
    )
    OR (
      NOT public.is_support_session()
      AND NOT public.is_tenant_support_session()
      AND p_tenant_id IS NOT NULL
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


SCOPED_PERMISSION_SQL = """
CREATE OR REPLACE FUNCTION public.tenant_actor_has_scoped_permission(
  p_tenant_id UUID,
  p_permission_code TEXT,
  p_branch_id UUID
) RETURNS BOOLEAN AS $$
  SELECT public.is_support_session()
    OR (
      public.is_tenant_support_session()
      AND p_tenant_id IS NOT DISTINCT FROM public.current_tenant_id()
      AND public.support_access_has_capability(p_permission_code)
    )
    OR (
      NOT public.is_support_session()
      AND NOT public.is_tenant_support_session()
      AND p_tenant_id IS NOT NULL
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


ROLE_DELEGATION_GATE_SQL = """
CREATE OR REPLACE FUNCTION public.tenant_actor_can_delegate_role(
  p_tenant_id UUID,
  p_role_id UUID,
  p_branch_id UUID
) RETURNS BOOLEAN AS $$
  SELECT public.is_support_session()
    OR (
      public.is_tenant_support_session()
      AND p_tenant_id IS NOT DISTINCT FROM public.current_tenant_id()
      AND public.support_access_has_capability('roles.assign')
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
        WHERE role_permission.role_id = p_role_id
          AND NOT public.support_actor_can_delegate_permission(
            role_permission.permission_code
          )
      )
    )
    OR (
      NOT public.is_support_session()
      AND NOT public.is_tenant_support_session()
      AND public.tenant_actor_is_owner(p_tenant_id)
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


REVOKE_ON_ARCHIVE_SQL = """
CREATE OR REPLACE FUNCTION public.trg_revoke_support_access_on_tenant_archive()
RETURNS TRIGGER AS $$
BEGIN
  IF OLD.status IS DISTINCT FROM NEW.status AND NEW.status = 'archived' THEN
    UPDATE public.support_access_session AS access_session
    SET
      revoked_at = pg_catalog.statement_timestamp(),
      revoked_by_user_id = COALESCE(
        public.current_app_user_id(),
        access_session.actor_user_id
      ),
      updated_at = pg_catalog.statement_timestamp(),
      updated_by = COALESCE(
        public.current_app_user_id(),
        access_session.actor_user_id
      )
    WHERE access_session.tenant_id = NEW.id
      AND access_session.revoked_at IS NULL;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


FUNCTIONS = (
    (IS_TENANT_SUPPORT_SESSION_SQL, "public.is_tenant_support_session()", True),
    (
        SUPPORT_HAS_CAPABILITY_SQL,
        "public.support_access_has_capability(TEXT)",
        False,
    ),
    (
        ACTIVE_PERMISSION_GATE_SQL,
        "public.tenant_actor_has_permission(UUID, TEXT)",
        False,
    ),
    (
        SCOPED_PERMISSION_SQL,
        "public.tenant_actor_has_scoped_permission(UUID, TEXT, UUID)",
        True,
    ),
    (
        ROLE_DELEGATION_GATE_SQL,
        "public.tenant_actor_can_delegate_role(UUID, UUID, UUID)",
        False,
    ),
    (
        REVOKE_ON_ARCHIVE_SQL,
        "public.trg_revoke_support_access_on_tenant_archive()",
        False,
    ),
)


def _load_revision_0062() -> ModuleType:
    path = Path(__file__).with_name("0062_add_scoped_support_access_sessions.py")
    spec = spec_from_file_location("aurum_migration_0062_hardening", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load authorization migration 0062")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _secure_function(signature: str, *, app_access: bool = False) -> None:
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_support")
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} FROM PUBLIC, aurum_app")
    grantees = "aurum_support, aurum_app" if app_access else "aurum_support"
    op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {grantees}")


def upgrade() -> None:
    op.execute("ALTER TABLE public.support_access_session " "ADD COLUMN actor_session_id UUID")
    op.execute("""
        UPDATE public.support_access_session
        SET
          revoked_at = COALESCE(revoked_at, statement_timestamp()),
          revoked_by_user_id = COALESCE(revoked_by_user_id, actor_user_id),
          updated_at = statement_timestamp(),
          updated_by = COALESCE(updated_by, actor_user_id)
        WHERE actor_session_id IS NULL
          AND revoked_at IS NULL
        """)
    op.execute(
        "ALTER TABLE public.support_access_session "
        "ADD CONSTRAINT fk_support_access_actor_session "
        "FOREIGN KEY (actor_session_id) REFERENCES public.session(id) ON DELETE RESTRICT"
    )
    op.execute(
        "ALTER TABLE public.support_access_session "
        "ADD CONSTRAINT ck_support_access_actor_session "
        "CHECK (actor_session_id IS NOT NULL OR revoked_at IS NOT NULL)"
    )
    op.execute("""
        CREATE INDEX ix_support_access_session_actor_family_active
        ON public.support_access_session(actor_user_id, actor_session_id, expires_at)
        WHERE revoked_at IS NULL
        """)

    for statement, signature, app_access in FUNCTIONS:
        op.execute(statement)
        _secure_function(signature, app_access=app_access)

    op.execute(
        "DROP TRIGGER IF EXISTS trg_revoke_support_access_on_tenant_archive " "ON public.tenant"
    )
    op.execute("""
        CREATE TRIGGER trg_revoke_support_access_on_tenant_archive
        AFTER UPDATE OF status ON public.tenant
        FOR EACH ROW
        EXECUTE FUNCTION public.trg_revoke_support_access_on_tenant_archive()
        """)


def downgrade() -> None:
    source_0062 = _load_revision_0062()

    op.execute("""
        UPDATE public.support_access_session
        SET
          revoked_at = COALESCE(revoked_at, statement_timestamp()),
          revoked_by_user_id = COALESCE(revoked_by_user_id, actor_user_id),
          updated_at = statement_timestamp(),
          updated_by = COALESCE(updated_by, actor_user_id)
        WHERE revoked_at IS NULL
        """)
    op.execute(
        "DROP TRIGGER IF EXISTS trg_revoke_support_access_on_tenant_archive " "ON public.tenant"
    )
    op.execute("DROP FUNCTION public.trg_revoke_support_access_on_tenant_archive()")

    legacy_definitions = (
        (
            source_0062.IS_TENANT_SUPPORT_SESSION_SQL,
            "public.is_tenant_support_session()",
            True,
        ),
        (
            source_0062.SUPPORT_HAS_CAPABILITY_SQL,
            "public.support_access_has_capability(TEXT)",
            False,
        ),
        (
            source_0062.ACTIVE_PERMISSION_GATE_SQL,
            "public.tenant_actor_has_permission(UUID, TEXT)",
            False,
        ),
        (
            source_0062.SCOPED_PERMISSION_SQL,
            "public.tenant_actor_has_scoped_permission(UUID, TEXT, UUID)",
            True,
        ),
        (
            source_0062.ROLE_DELEGATION_GATE_SQL,
            "public.tenant_actor_can_delegate_role(UUID, UUID, UUID)",
            False,
        ),
    )
    for statement, signature, app_access in legacy_definitions:
        op.execute(statement)
        _secure_function(signature, app_access=app_access)

    op.execute("DROP INDEX public.ix_support_access_session_actor_family_active")
    op.execute(
        "ALTER TABLE public.support_access_session "
        "DROP CONSTRAINT ck_support_access_actor_session"
    )
    op.execute(
        "ALTER TABLE public.support_access_session "
        "DROP CONSTRAINT fk_support_access_actor_session"
    )
    op.execute("ALTER TABLE public.support_access_session DROP COLUMN actor_session_id")
