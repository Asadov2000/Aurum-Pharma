"""Transactional downgrade/upgrade rehearsal, restricted to disposable test DBs."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory


@pytest.mark.parametrize(
    "missing_references", [(), ("app_user",), ("app_user", "session", "email_code")]
)
async def test_identity_migration_roundtrip_on_disposable_database(
    maintenance_engine: AsyncEngine, missing_references: tuple[str, ...]
) -> None:
    source = (
        Path(__file__).resolve().parents[3]
        / "alembic"
        / "versions"
        / "0146_verified_login_identities.py"
    )
    spec = importlib.util.spec_from_file_location("identity_migration_rehearsal", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def rehearse(connection: Connection) -> None:
        database = str(connection.scalar(text("SELECT current_database()")))
        assert database.endswith("_test"), "Migration rehearsal requires a disposable test database"
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        connection.execute(text("SET LOCAL ROLE aurum_schema_owner"))
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
            for table_name in missing_references:
                connection.execute(
                    text(f"REVOKE REFERENCES ON TABLE public.{table_name} FROM aurum_schema_owner")
                )
                assert not connection.scalar(
                    text("SELECT has_table_privilege('aurum_schema_owner',:table,'REFERENCES')"),
                    {"table": f"public.{table_name}"},
                )
            reference_acl = connection.execute(
                text("""SELECT grantee,table_name,privilege_type,is_grantable
                FROM information_schema.table_privileges WHERE table_schema='public'
                AND table_name IN ('app_user','session','email_code')
                ORDER BY grantee,table_name,privilege_type,is_grantable""")
            ).all()
            # 0145 still uses the invitation-aware matcher introduced by 0128.
            # Compare its exact body, not merely the existence of the function.
            canonical = module._source("0128_add_tenant_invitation_lifecycle.py")
            expected_body = canonical.MATCH_EMAIL_CODE_SQL.split("AS $$", 1)[1].split("$$", 1)[0]
            assert (
                connection.scalar(text("""SELECT prosrc FROM pg_proc
                WHERE oid='public.auth_email_code_matches(uuid,text,text)'::regprocedure"""))
                == expected_body
            )
            assert (
                connection.scalar(text("SELECT to_regclass('public.auth_identity_code')")) is None
            )
            assert not connection.scalar(
                text("""SELECT EXISTS(SELECT 1 FROM information_schema.columns
                WHERE table_schema='public' AND table_name='app_user'
                AND column_name='verified_phone')""")
            )
            module.upgrade()
            assert connection.execute(text("""SELECT grantee,table_name,privilege_type,is_grantable
                FROM information_schema.table_privileges WHERE table_schema='public'
                AND table_name IN ('app_user','session','email_code')
                ORDER BY grantee,table_name,privilege_type,is_grantable""")).all() == reference_acl
            assert connection.scalar(text("""SELECT strpos(prosrc,'code.tenant_invitation_id')>0
                AND strpos(prosrc,'code.identity_method')>0 FROM pg_proc
                WHERE oid='public.auth_email_code_matches(uuid,text,text)'::regprocedure"""))
            assert (
                connection.scalar(text("SELECT to_regclass('public.auth_identity_code')"))
                is not None
            )
            assert not connection.scalar(
                text(
                    "SELECT has_function_privilege('aurum_app',"
                    "'public.lookup_identity_password(text)','EXECUTE')"
                )
            )
            assert connection.scalar(
                text(
                    "SELECT has_function_privilege('aurum_support',"
                    "'public.lookup_identity_password(text)','EXECUTE')"
                )
            )

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            # Even a successful rehearsal leaves the shared disposable fixture as found.
            await transaction.rollback()


async def test_cluster_safe_upgrade_from_0091_without_reference_privileges(
    maintenance_engine: AsyncEngine,
) -> None:
    """Reproduce CI's hardened 0091 -> head path with real migrator credentials."""
    backend = Path(__file__).resolve().parents[3]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    revisions = [
        revision
        for revision in ScriptDirectory.from_config(config).walk_revisions(base="0091", head="head")
        if revision.revision != "0091"
    ]

    def rehearse(connection: Connection) -> None:
        database = str(connection.scalar(text("SELECT current_database()")))
        assert database.endswith("_test"), "Cluster rehearsal requires a disposable test database"
        assert connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        connection.execute(text("SET LOCAL ROLE aurum_schema_owner"))
        with Operations.context(MigrationContext.configure(connection)):
            for revision in revisions:
                revision.module.downgrade()
            connection.execute(
                text(
                    "REVOKE REFERENCES ON TABLE public.app_user,public.tenant "
                    "FROM aurum_schema_owner"
                )
            )
            for table_name in ("app_user", "tenant"):
                assert not connection.scalar(
                    text("SELECT has_table_privilege('aurum_schema_owner',:table,'REFERENCES')"),
                    {"table": f"public.{table_name}"},
                )
            for revision in reversed(revisions):
                revision.module.upgrade()
            for table_name in ("app_user", "tenant"):
                assert not connection.scalar(
                    text("SELECT has_table_privilege('aurum_schema_owner',:table,'REFERENCES')"),
                    {"table": f"public.{table_name}"},
                )
            assert connection.scalar(text("SELECT to_regclass('public.auth_identity_code')"))

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.run_sync(rehearse)
        finally:
            await transaction.rollback()
