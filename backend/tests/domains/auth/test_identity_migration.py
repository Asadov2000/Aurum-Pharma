"""Transactional downgrade/upgrade rehearsal, restricted to disposable test DBs."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic.migration import MigrationContext
from alembic.operations import Operations


async def test_identity_migration_roundtrip_on_disposable_database(
    maintenance_engine: AsyncEngine,
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
        connection.execute(text("SET LOCAL ROLE aurum_schema_owner"))
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()
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
