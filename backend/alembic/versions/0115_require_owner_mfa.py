"""Require account MFA for active tenant owners.

Revision ID: 0115
Revises: 0114
Create Date: 2026-08-27
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Union

from alembic import op

revision: str = "0115"
down_revision: Union[str, Sequence[str], None] = "0114"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ACCOUNT_REQUIRES_MFA_SQL = """
CREATE FUNCTION public.auth_account_requires_mfa(p_user_id UUID)
RETURNS BOOLEAN AS $$
  SELECT EXISTS (
    SELECT 1
    FROM public.app_user AS app_user
    WHERE app_user.id = p_user_id
      AND app_user.status IN ('invited', 'active')
      AND (
        app_user.is_developer
        OR app_user.is_administrator
        OR EXISTS (
          SELECT 1
          FROM public.tenant_membership AS membership
          JOIN public.tenant_ownership AS ownership
            ON ownership.tenant_id = membership.tenant_id
           AND ownership.membership_id = membership.id
           AND ownership.is_active
          WHERE membership.user_id = app_user.id
            AND membership.tenant_id = app_user.home_tenant_id
            AND membership.status = 'active'
        )
      )
  )
$$ LANGUAGE SQL
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
"""


def _load_revision_module(filename: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(f"aurum_migration_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load migration source: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _secure_function(signature: str, *, app_access: bool = False) -> None:
    op.execute(f"ALTER FUNCTION {signature} OWNER TO aurum_schema_owner")
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {signature} "
        "FROM PUBLIC, aurum_app, aurum_support"
    )
    grantees = "aurum_support, aurum_app" if app_access else "aurum_support"
    op.execute(f"GRANT EXECUTE ON FUNCTION {signature} TO {grantees}")


def _replace_function(signature: str, statement: str, *, app_access: bool = False) -> None:
    op.execute(f"DROP FUNCTION {signature}")
    op.execute(statement)
    _secure_function(signature, app_access=app_access)


def _allow_required_accounts(statement: str) -> str:
    support_only = "AND (app_user.is_developer OR app_user.is_administrator)"
    if support_only not in statement:
        raise RuntimeError("Canonical MFA function no longer contains its eligibility guard")
    return statement.replace(
        support_only,
        "AND public.auth_account_requires_mfa(app_user.id)",
    ).replace("CREATE FUNCTION public.", "CREATE OR REPLACE FUNCTION public.", 1)


def _owner_login_lookup(statement: str) -> str:
    result = statement.replace(
        "membership_status TEXT, mfa_status TEXT",
        "membership_status TEXT, mfa_status TEXT, mfa_required BOOLEAN",
        1,
    ).replace(
        "support_mfa.status AS mfa_status",
        "support_mfa.status AS mfa_status,\n"
        "    public.auth_account_requires_mfa(app_user.id) AS mfa_required",
        1,
    )
    if result == statement:
        raise RuntimeError("Canonical login lookup no longer matches the expected contract")
    return result


def _account_mfa_challenge(statement: str) -> str:
    result = _allow_required_accounts(statement).replace(
        "AND app_user.password_hash IS NOT NULL",
        "AND (\n"
        "      (NOT app_user.is_developer AND NOT app_user.is_administrator)\n"
        "      OR app_user.password_hash IS NOT NULL\n"
        "    )",
        1,
    )
    if result == _allow_required_accounts(statement):
        raise RuntimeError("Canonical MFA challenge no longer contains the support password guard")
    return result


def _mfa_functions(source_0057: ModuleType) -> tuple[tuple[str, str], ...]:
    return (
        (
            source_0057.LOOKUP_MFA_CHALLENGE_SQL,
            "public.lookup_auth_mfa_challenge(TEXT, JSONB, BOOLEAN)",
        ),
        (
            source_0057.STAGE_MFA_ENROLLMENT_SQL,
            "public.stage_auth_mfa_enrollment(TEXT, TEXT, SMALLINT, TEXT, TEXT[])",
        ),
        (
            source_0057.COMPLETE_MFA_ENROLLMENT_SQL,
            "public.complete_auth_mfa_enrollment("
            "TEXT, BIGINT, TEXT, JSONB, TEXT, TEXT, TEXT, TIMESTAMP WITH TIME ZONE)",
        ),
        (
            source_0057.COMPLETE_MFA_VERIFICATION_SQL,
            "public.complete_auth_mfa_verification("
            "TEXT, BIGINT, TEXT, JSONB, TEXT, TEXT, TEXT, TIMESTAMP WITH TIME ZONE)",
        ),
        (
            source_0057.RECOVER_MFA_CHALLENGE_SQL,
            "public.recover_auth_mfa_challenge(TEXT, TEXT, TEXT, TEXT)",
        ),
        (
            source_0057.LOOKUP_STEP_UP_SQL,
            "public.lookup_support_mfa_for_step_up(UUID, UUID, JSONB)",
        ),
        (
            source_0057.COMPLETE_STEP_UP_SQL,
            "public.complete_support_mfa_step_up(UUID, UUID, BIGINT, TEXT, JSONB)",
        ),
    )


def upgrade() -> None:
    source_0056 = _load_revision_module("0056_add_support_mfa.py")
    source_0057 = _load_revision_module("0057_harden_support_mfa.py")
    source_0089 = _load_revision_module("0089_add_platform_staff_lifecycle.py")

    op.execute(ACCOUNT_REQUIRES_MFA_SQL)
    _secure_function("public.auth_account_requires_mfa(UUID)")

    _replace_function(
        "public.lookup_login_user_by_email(TEXT, UUID, TEXT)",
        _owner_login_lookup(source_0089.LOOKUP_LOGIN_USER_SQL),
        app_access=True,
    )
    statement = _account_mfa_challenge(source_0056.CREATE_MFA_CHALLENGE_SQL)
    op.execute(statement)
    _secure_function(
        "public.create_auth_mfa_challenge_from_email_code("
        "TEXT, UUID, TEXT, TEXT, TEXT, TEXT, TIMESTAMP WITH TIME ZONE)",
        app_access=True,
    )

    for canonical, signature in _mfa_functions(source_0057):
        op.execute(_allow_required_accounts(canonical))
        _secure_function(signature)


def downgrade() -> None:
    source_0056 = _load_revision_module("0056_add_support_mfa.py")
    source_0057 = _load_revision_module("0057_harden_support_mfa.py")
    source_0089 = _load_revision_module("0089_add_platform_staff_lifecycle.py")

    _replace_function(
        "public.lookup_login_user_by_email(TEXT, UUID, TEXT)",
        source_0089.LOOKUP_LOGIN_USER_SQL,
        app_access=True,
    )
    op.execute(
        source_0056.CREATE_MFA_CHALLENGE_SQL.replace(
            "CREATE FUNCTION public.",
            "CREATE OR REPLACE FUNCTION public.",
            1,
        )
    )
    _secure_function(
        "public.create_auth_mfa_challenge_from_email_code("
        "TEXT, UUID, TEXT, TEXT, TEXT, TEXT, TIMESTAMP WITH TIME ZONE)",
        app_access=True,
    )
    for canonical, signature in _mfa_functions(source_0057):
        op.execute(
            canonical.replace(
                "CREATE FUNCTION public.",
                "CREATE OR REPLACE FUNCTION public.",
                1,
            )
        )
        _secure_function(signature)

    op.execute("DROP FUNCTION public.auth_account_requires_mfa(UUID)")
