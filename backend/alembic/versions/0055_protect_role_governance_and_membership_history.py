"""security: protect role governance and membership history

Revision ID: 0055
Revises: 0054
Create Date: 2026-07-18

Ordinary tenant roles may contain every business capability exposed by the
constructor, but they may never receive protected account/role-governance
capabilities. Direct deletion of membership history is also rejected; tenant
deletion may still cascade through the history tables.
"""

from __future__ import annotations

from collections.abc import Sequence
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0055"
down_revision: str | Sequence[str] | None = "0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


PROTECTED_GOVERNANCE_CODES_SQL = """
  'roles.assign',
  'roles.create',
  'roles.update',
  'users.block',
  'users.delete',
  'users.invite',
  'users.update'
"""


FULL_CATALOG_GUARD_SQL = """
  IF TG_OP = 'INSERT' AND NOT EXISTS (
    SELECT 1
    FROM public.permission AS actor_permission
    WHERE actor_permission.is_active
      AND actor_permission.target_role_type = 'tenant'
      AND actor_permission.owner_delegable
      AND actor_permission.code <> NEW.permission_code
      AND public.tenant_actor_has_permission(
        v_role_tenant_id,
        actor_permission.code
      )
      AND NOT EXISTS (
        SELECT 1
        FROM public.role_permission AS existing_permission
        WHERE existing_permission.role_id = v_role_id
          AND existing_permission.permission_code = actor_permission.code
      )
  ) THEN
    RAISE EXCEPTION 'Role cannot reproduce the owner delegation scope'
      USING ERRCODE = '42501';
  END IF;

"""


MEMBERSHIP_DELETE_GUARD_SQL = """  IF TG_OP = 'DELETE' THEN
    IF pg_catalog.pg_trigger_depth() <= 1 THEN
      RAISE EXCEPTION 'Membership history cannot be deleted directly'
        USING ERRCODE = '42501';
    END IF;
    RETURN OLD;
  END IF;

"""


def _load_revision_0053() -> ModuleType:
    path = Path(__file__).with_name("0053_add_scoped_delegated_authorization.py")
    spec = spec_from_file_location("aurum_migration_0053_history", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load authorization migration 0053")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sql(module: ModuleType, name: str) -> str:
    value = getattr(module, name, None)
    if not isinstance(value, str):
        raise RuntimeError(f"Migration 0053 does not expose SQL constant {name}")
    return value


def _as_create_or_replace(statement: str) -> str:
    if statement.lstrip().startswith("CREATE OR REPLACE FUNCTION"):
        return statement
    return statement.replace("CREATE FUNCTION", "CREATE OR REPLACE FUNCTION", 1)


def _secure_function(signature: str) -> None:
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_support")
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} FROM PUBLIC, aurum_app")
    op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO aurum_support")


def _without_full_catalog_guard(statement: str) -> str:
    if statement.count(FULL_CATALOG_GUARD_SQL) != 1:
        raise RuntimeError("Cannot locate the historical full-catalog role guard")
    return statement.replace(FULL_CATALOG_GUARD_SQL, "", 1)


def _with_membership_delete_guard(statement: str) -> str:
    marker = "BEGIN\n  IF TG_OP = 'UPDATE' THEN"
    if statement.count(marker) != 1:
        raise RuntimeError("Cannot locate the tenant membership guard body")
    return statement.replace(
        marker,
        f"BEGIN\n{MEMBERSHIP_DELETE_GUARD_SQL}  IF TG_OP = 'UPDATE' THEN",
        1,
    )


def _replace_membership_trigger(*, include_delete: bool) -> None:
    op.execute("GRANT TRIGGER ON TABLE public.tenant_membership TO aurum_support")
    op.execute("DROP TRIGGER IF EXISTS trg_guard_tenant_membership " "ON public.tenant_membership")
    events = "DELETE OR UPDATE" if include_delete else "UPDATE"
    op.execute(f"""
        CREATE TRIGGER trg_guard_tenant_membership
        BEFORE {events} ON public.tenant_membership
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_tenant_membership()
        """)
    op.execute("REVOKE TRIGGER ON TABLE public.tenant_membership FROM aurum_support")


def _protect_governance_catalog() -> None:
    op.execute(f"""
        UPDATE public.permission
        SET
          developer_grantable = false,
          administrator_grantable = false,
          owner_grantable = false,
          developer_delegable = false,
          administrator_delegable = false,
          owner_delegable = false
        WHERE code IN ({PROTECTED_GOVERNANCE_CODES_SQL})
        """)
    op.execute("""
        UPDATE public.permission
        SET
          administrator_grantable = false,
          administrator_delegable = false
        WHERE code = 'tenant.export.full'
        """)


def _restore_legacy_catalog() -> None:
    op.execute(f"""
        UPDATE public.permission
        SET
          developer_grantable = true,
          administrator_grantable = true,
          owner_grantable = true,
          developer_delegable = true,
          administrator_delegable = true,
          owner_delegable = true
        WHERE code IN ({PROTECTED_GOVERNANCE_CODES_SQL})
        """)
    op.execute("""
        UPDATE public.permission
        SET
          administrator_grantable = true,
          administrator_delegable = true
        WHERE code = 'tenant.export.full'
        """)


def upgrade() -> None:
    source = _load_revision_0053()
    _protect_governance_catalog()

    role_permission_guard = _without_full_catalog_guard(
        _sql(source, "ROLE_PERMISSION_MUTATION_GUARD_SQL")
    )
    op.execute(_as_create_or_replace(role_permission_guard))
    _secure_function("public.trg_guard_role_permission_mutation()")

    membership_guard = _with_membership_delete_guard(_sql(source, "MEMBERSHIP_GUARD_TRIGGER_SQL"))
    op.execute(_as_create_or_replace(membership_guard))
    _secure_function("public.trg_guard_tenant_membership()")
    _replace_membership_trigger(include_delete=True)


def downgrade() -> None:
    source = _load_revision_0053()
    op.execute(_as_create_or_replace(_sql(source, "ROLE_PERMISSION_MUTATION_GUARD_SQL")))
    _secure_function("public.trg_guard_role_permission_mutation()")

    op.execute(_as_create_or_replace(_sql(source, "MEMBERSHIP_GUARD_TRIGGER_SQL")))
    _secure_function("public.trg_guard_tenant_membership()")
    _replace_membership_trigger(include_delete=False)
    _restore_legacy_catalog()
