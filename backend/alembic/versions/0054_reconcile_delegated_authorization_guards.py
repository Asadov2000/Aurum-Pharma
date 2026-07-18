"""security: reconcile delegated authorization database guards

Revision ID: 0054
Revises: 0053
Create Date: 2026-07-18

Revision 0053 was already applied to persistent development databases while
its security review was still in progress. This revision intentionally
reapplies the final 0053 function definitions and triggers. On a clean
database it is idempotent; on an existing database it closes the gap without
requiring a destructive downgrade.
"""

from __future__ import annotations

from collections.abc import Sequence
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0054"
down_revision: str | Sequence[str] | None = "0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OWNER_ROLE_PREFLIGHT_SQL = """
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM public.role AS owner_role
    WHERE owner_role.is_protected
      AND owner_role.protected_kind = 'tenant_owner'
      AND (
        owner_role.tenant_id IS NULL
        OR owner_role.is_system
        OR owner_role.level <> 3
        OR NOT EXISTS (
          SELECT 1
          FROM public.role_template AS template
          WHERE template.slug = 'owner'
            AND template.is_active
        )
        OR EXISTS (
          SELECT role_permission.permission_code
          FROM public.role_permission AS role_permission
          WHERE role_permission.role_id = owner_role.id
          EXCEPT
          SELECT template_permission.permission_code
          FROM public.role_template AS template
          JOIN public.role_template_permission AS template_permission
            ON template_permission.template_id = template.id
          WHERE template.slug = 'owner'
            AND template.is_active
        )
        OR EXISTS (
          SELECT template_permission.permission_code
          FROM public.role_template AS template
          JOIN public.role_template_permission AS template_permission
            ON template_permission.template_id = template.id
          WHERE template.slug = 'owner'
            AND template.is_active
          EXCEPT
          SELECT role_permission.permission_code
          FROM public.role_permission AS role_permission
          WHERE role_permission.role_id = owner_role.id
        )
      )
  ) THEN
    RAISE EXCEPTION
      'Protected tenant owner roles must exactly match the active owner template';
  END IF;

  IF EXISTS (
    SELECT 1
    FROM public.role AS owner_role
    WHERE owner_role.tenant_id IS NOT NULL
      AND NOT owner_role.is_system
      AND NOT owner_role.is_protected
      AND owner_role.name = 'Владелец'
      AND EXISTS (
        SELECT 1
        FROM public.user_assignment AS assignment
        WHERE assignment.role_id = owner_role.id
          AND assignment.is_active
      )
  ) THEN
    RAISE EXCEPTION
      'An assigned owner role remains unprotected; review it before upgrading';
  END IF;
END
$$
"""


def _load_canonical_revision() -> ModuleType:
    path = Path(__file__).with_name("0053_add_scoped_delegated_authorization.py")
    spec = spec_from_file_location("aurum_migration_0053_canonical", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load canonical authorization migration 0053")
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
    return statement.replace(
        "CREATE FUNCTION",
        "CREATE OR REPLACE FUNCTION",
        1,
    )


def _secure_function(signature: str, *, app_access: bool = False) -> None:
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_support")
    op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} FROM PUBLIC, aurum_app")
    grantees = "aurum_support, aurum_app" if app_access else "aurum_support"
    op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {grantees}")


def _replace_auth_lookup_functions(source: ModuleType) -> None:
    login_signature = "public.lookup_login_user_by_email(TEXT, UUID, TEXT)"
    user_signature = "public.lookup_auth_user_by_id(UUID, UUID)"
    op.execute(f"DROP FUNCTION {login_signature}")
    op.execute(f"DROP FUNCTION {user_signature}")
    op.execute(_sql(source, "ACTIVE_LOOKUP_LOGIN_USER_BY_EMAIL_SQL"))
    op.execute(_sql(source, "ACTIVE_LOOKUP_AUTH_USER_BY_ID_SQL"))
    _secure_function(login_signature, app_access=True)
    _secure_function(user_signature, app_access=True)


def _replace_guard_functions(source: ModuleType) -> None:
    definitions = (
        (
            "ACTIVE_PERMISSION_GATE_SQL",
            "public.tenant_actor_has_permission(UUID, TEXT)",
            False,
        ),
        (
            "SCOPED_PERMISSION_SQL",
            "public.tenant_actor_has_scoped_permission(UUID, TEXT, UUID)",
            True,
        ),
        (
            "TENANT_ACTOR_IS_OWNER_SQL",
            "public.tenant_actor_is_owner(UUID)",
            True,
        ),
        (
            "ROLE_DELEGATION_GATE_SQL",
            "public.tenant_actor_can_delegate_role(UUID, UUID, UUID)",
            False,
        ),
        (
            "ASSIGNMENT_SCOPE_TRIGGER_SQL",
            "public.trg_guard_user_assignment_scope()",
            False,
        ),
        (
            "ROLE_MUTATION_GUARD_SQL",
            "public.trg_guard_tenant_role_mutation()",
            False,
        ),
        (
            "ROLE_PERMISSION_MUTATION_GUARD_SQL",
            "public.trg_guard_role_permission_mutation()",
            False,
        ),
        (
            "OWNERSHIP_GUARD_TRIGGER_SQL",
            "public.trg_guard_tenant_ownership()",
            False,
        ),
        (
            "SET_MEMBERSHIP_STATUS_SQL",
            "public.set_tenant_membership_status(" "UUID, UUID, TEXT, TIMESTAMP WITH TIME ZONE)",
            True,
        ),
        (
            "ACCEPT_TENANT_INVITATION_SQL",
            "public.accept_tenant_invitation(" "UUID, UUID, TIMESTAMP WITH TIME ZONE)",
            True,
        ),
        (
            "ROLE_PERMISSION_AUDIT_SQL",
            "public.record_role_permission_change(UUID, TEXT[], TEXT[])",
            True,
        ),
    )
    for constant, signature, app_access in definitions:
        op.execute(_as_create_or_replace(_sql(source, constant)))
        _secure_function(signature, app_access=app_access)


def _replace_guard_triggers() -> None:
    # 0053 revokes ordinary TRIGGER rights from its runtime owner after setup.
    # The owner retains the grant option, so temporarily restore only what this
    # migration needs and revoke it again with the final table grants below.
    op.execute("GRANT TRIGGER ON TABLE public.tenant_ownership TO aurum_support")

    for trigger, table in (
        ("trg_guard_tenant_role_mutation", "role"),
        ("trg_guard_role_permission_mutation", "role_permission"),
        ("trg_guard_tenant_ownership", "tenant_ownership"),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger} ON public.{table}")

    op.execute("""
        CREATE TRIGGER trg_guard_tenant_role_mutation
        BEFORE INSERT OR DELETE OR UPDATE ON public.role
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_tenant_role_mutation()
        """)
    op.execute("""
        CREATE TRIGGER trg_guard_role_permission_mutation
        BEFORE INSERT OR DELETE OR UPDATE ON public.role_permission
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_role_permission_mutation()
        """)
    op.execute("""
        CREATE TRIGGER trg_guard_tenant_ownership
        BEFORE INSERT OR DELETE OR UPDATE ON public.tenant_ownership
        FOR EACH ROW EXECUTE FUNCTION public.trg_guard_tenant_ownership()
        """)


def _restore_runtime_grants() -> None:
    for table in ("tenant_membership", "tenant_ownership"):
        op.execute(
            f"REVOKE ALL PRIVILEGES ON TABLE public.{table} "
            "FROM PUBLIC, aurum_app, aurum_support"
        )
        op.execute(f"GRANT SELECT ON TABLE public.{table} TO aurum_app")
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE " f"ON TABLE public.{table} TO aurum_support"
        )

    for legacy_function in (
        "public.find_invitable_user_id(UUID, TEXT)",
        "public.create_invited_app_user(UUID, TEXT, TEXT)",
        "public.update_tenant_user_profile(UUID, UUID, TEXT, TEXT)",
        "public.set_tenant_user_status(UUID, UUID, TEXT, TIMESTAMP WITH TIME ZONE)",
    ):
        op.execute(f"REVOKE ALL PRIVILEGES ON FUNCTION {legacy_function} FROM aurum_app")


def upgrade() -> None:
    source = _load_canonical_revision()
    op.execute(OWNER_ROLE_PREFLIGHT_SQL)
    _replace_auth_lookup_functions(source)
    _replace_guard_functions(source)
    _replace_guard_triggers()
    _restore_runtime_grants()


def downgrade() -> None:
    # The canonical 0053 file already contains these exact definitions. A
    # downgrade to 0053 therefore requires no schema mutation and remains safe
    # on clean databases. Persistent shared databases are upgrade-only.
    pass
