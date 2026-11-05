"""Database boundaries for prepared stock, independent of service validation."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.core.config import get_settings
from app.domains.inventory.unit_ledger_repository import UnitLedgerRepository
from app.domains.inventory.unit_ledger_service import PreparedUnitLedgerService
from tests.domains.inventory.test_unit_ledger import (
    PACKAGING,
    TODAY,
    LedgerContext,
    prepare,
    seed_ledger_context,
)

TABLES = ("unit_stock_operation", "unit_stock_lot", "unit_stock_opened_pack", "unit_stock_entry")


async def test_runtime_history_and_projection_mutations_are_denied(
    db_session: AsyncSession,
) -> None:
    context = await seed_ledger_context(db_session)
    lot_id = await prepare(db_session, context)
    for statement in (
        "UPDATE unit_stock_lot SET closed_packs = 100 WHERE id = :lot",
        "UPDATE unit_stock_lot SET units_per_pack = 60 WHERE id = :lot",
        "DELETE FROM unit_stock_lot WHERE id = :lot",
        "UPDATE unit_stock_operation SET quantity = 2 WHERE tenant_id = :tenant",
        "DELETE FROM unit_stock_operation WHERE tenant_id = :tenant",
        "UPDATE unit_stock_entry SET units_delta = 1 WHERE lot_id = :lot",
        "DELETE FROM unit_stock_entry WHERE lot_id = :lot",
    ):
        with pytest.raises(DBAPIError):
            async with db_session.begin_nested():
                await db_session.execute(
                    text(statement), {"lot": lot_id, "tenant": context.tenant_id}
                )
    assert (
        await db_session.scalar(
            text("SELECT closed_packs FROM unit_stock_lot WHERE id = :lot"), {"lot": lot_id}
        )
        == 1
    )
    audited = set(
        (
            await db_session.execute(
                text(
                    "SELECT DISTINCT table_name FROM audit_log WHERE tenant_id = :tenant "
                    "AND table_name = ANY(:tables)"
                ),
                {"tenant": context.tenant_id, "tables": list(TABLES)},
            )
        ).scalars()
    )
    assert {"unit_stock_lot", "unit_stock_operation", "unit_stock_entry"} <= audited


async def test_header_without_entries_cannot_be_committed(db_session: AsyncSession) -> None:
    context = await seed_ledger_context(db_session)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO unit_stock_operation "
                    "(id,tenant_id,branch_id,catalog_id,unit,units_per_pack,dispense_step,"
                    "kind,sale_format,quantity,base_units,request_hash) VALUES "
                    "(:id,:tenant,:branch,:catalog,'tablet',30,1,'dispense','unit',1,1,:hash)"
                ),
                {
                    "id": uuid4(),
                    "tenant": context.tenant_id,
                    "branch": context.branch_id,
                    "catalog": context.catalog_id,
                    "hash": "a" * 64,
                },
            )
            await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


@pytest.mark.parametrize("restriction", ["readonly", "inventory"])
async def test_tenant_access_guard_blocks_prepared_writes(
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    restriction: str,
) -> None:
    async with AsyncSession(db_engine, expire_on_commit=False) as support:
        async with support.begin():
            await support.execute(text("SELECT set_config('app.support_session', 'true', true)"))
            context = await seed_ledger_context(support)
    # Prepare the restricted policy using the disposable-database owner; actual
    # stock writes below use the runtime role and every production stock guard.
    async with maintenance_engine.begin() as owner:
        assert str(await owner.scalar(text("SELECT current_database()"))).endswith("_test")
        await owner.execute(
            text("ALTER TABLE tenant_access_policy DISABLE TRIGGER a00_guard_tenant_access_policy")
        )
        await owner.execute(
            text(
                "UPDATE tenant_access_policy SET version=version+1, "
                "mode=:mode, blocked_modules=:modules WHERE tenant_id=:tenant"
            ),
            {
                "tenant": context.tenant_id,
                "mode": "readonly" if restriction == "readonly" else "full",
                "modules": ["inventory"] if restriction == "inventory" else [],
            },
        )
        await owner.execute(
            text("ALTER TABLE tenant_access_policy ENABLE TRIGGER a00_guard_tenant_access_policy")
        )
    async with AsyncSession(db_engine, expire_on_commit=False) as support:
        with pytest.raises(DBAPIError, match="tenant_access_restricted"):
            async with support.begin():
                await prepare(support, context)


async def test_malformed_entry_arithmetic_cannot_change_stock(db_session: AsyncSession) -> None:
    context = await seed_ledger_context(db_session)
    lot_id = await prepare(db_session, context)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            operation_id = uuid4()
            await db_session.execute(
                text(
                    "INSERT INTO unit_stock_operation "
                    "(id,tenant_id,branch_id,catalog_id,unit,units_per_pack,dispense_step,"
                    "kind,sale_format,quantity,base_units,request_hash) VALUES "
                    "(:id,:tenant,:branch,:catalog,'tablet',30,1,'dispense','pack',1,30,:hash)"
                ),
                {
                    "id": operation_id,
                    "tenant": context.tenant_id,
                    "branch": context.branch_id,
                    "catalog": context.catalog_id,
                    "hash": "b" * 64,
                },
            )
            await db_session.execute(
                text(
                    "INSERT INTO unit_stock_entry "
                    "(id,tenant_id,branch_id,catalog_id,operation_id,lot_id,kind,entry_order,"
                    "closed_delta,opened_delta,units_delta,pack_remaining_after) VALUES "
                    "(:id,:tenant,:branch,:catalog,:operation,:lot,'pack_take',0,-1,0,-1,0)"
                ),
                {
                    "id": uuid4(),
                    "tenant": context.tenant_id,
                    "branch": context.branch_id,
                    "catalog": context.catalog_id,
                    "operation": operation_id,
                    "lot": lot_id,
                },
            )
    assert (
        await db_session.scalar(
            text("SELECT closed_packs FROM unit_stock_lot WHERE id = :lot"), {"lot": lot_id}
        )
        == 1
    )


async def test_foreign_tenant_branch_reference_is_rejected(db_session: AsyncSession) -> None:
    first = await seed_ledger_context(db_session)
    second = await seed_ledger_context(db_session)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO unit_stock_lot "
                    "(id,tenant_id,branch_id,catalog_id,unit,units_per_pack,"
                    "dispense_step,expires_at) "
                    "VALUES (:id,:tenant,:branch,:catalog,'tablet',30,1,CURRENT_DATE+30)"
                ),
                {
                    "id": uuid4(),
                    "tenant": first.tenant_id,
                    "branch": second.branch_id,
                    "catalog": first.catalog_id,
                },
            )


async def test_app_cannot_spoof_support_access(db_engine: AsyncEngine) -> None:
    async with AsyncSession(db_engine, expire_on_commit=False) as support:
        async with support.begin():
            await support.execute(text("SELECT set_config('app.support_session', 'true', true)"))
            context = await seed_ledger_context(support)
            await prepare(support, context)
    engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "SELECT set_config('app.tenant_id', :tenant, true), "
                    "set_config('app.support_session', 'true', true)"
                ),
                {"tenant": str(context.tenant_id)},
            )
            for table in TABLES:
                assert (
                    await connection.scalar(
                        text(f"SELECT count(*) FROM {table} WHERE tenant_id = :tenant"),
                        {"tenant": context.tenant_id},
                    )
                    == 0
                )
    finally:
        await engine.dispose()


async def test_app_branch_assignment_hides_other_branch_and_tenant(
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    user_id, membership_id, role_id = uuid4(), uuid4(), uuid4()
    async with AsyncSession(db_engine, expire_on_commit=False) as support:
        async with support.begin():
            await support.execute(text("SELECT set_config('app.support_session', 'true', true)"))
            context = await seed_ledger_context(support)
            other_tenant = await seed_ledger_context(support)
            other_branch = await support.scalar(
                text("INSERT INTO branch (tenant_id,name) VALUES (:tenant,'Other') RETURNING id"),
                {"tenant": context.tenant_id},
            )
            await prepare(support, context)
            await prepare(support, other_tenant)
            await prepare(
                support, LedgerContext(context.tenant_id, other_branch, context.catalog_id)
            )
            await support.execute(
                text(
                    "INSERT INTO app_user (id,email,full_name,home_tenant_id,status,activated_at) "
                    "VALUES (:user,:email,'Ledger cashier',:tenant,'active',now())"
                ),
                {
                    "user": user_id,
                    "email": f"ledger-cashier-{user_id}@aurum.test",
                    "tenant": context.tenant_id,
                },
            )
            await support.execute(
                text(
                    "INSERT INTO tenant_membership "
                    "(id,tenant_id,user_id,full_name,status,activated_at) "
                    "VALUES (:membership,:tenant,:user,'Ledger cashier','active',now())"
                ),
                {"membership": membership_id, "tenant": context.tenant_id, "user": user_id},
            )
            await support.execute(
                text(
                    "INSERT INTO role (id,tenant_id,name,level,is_system,is_active,is_protected) "
                    "VALUES (:role,:tenant,'Ledger cashier',4,false,true,false)"
                ),
                {"role": role_id, "tenant": context.tenant_id},
            )
            await support.execute(
                text(
                    "INSERT INTO role_permission (role_id,permission_code) "
                    "VALUES (:role,'pos.sell')"
                ),
                {"role": role_id},
            )
    async with maintenance_engine.begin() as owner:
        await owner.execute(
            text(
                "INSERT INTO access_role_version "
                "(id,role_id,tenant_id,version,name,description,status,"
                "creation_xid,published_at,created_by) "
                "SELECT gen_random_uuid(),id,tenant_id,version,name,description,'published',"
                "txid_current(),statement_timestamp(),created_by FROM role WHERE id=:role"
            ),
            {"role": role_id},
        )
        await owner.execute(
            text(
                "INSERT INTO access_role_version_permission (role_version_id,permission_code) "
                "SELECT id,'pos.sell' FROM access_role_version WHERE role_id=:role"
            ),
            {"role": role_id},
        )
    async with db_engine.begin() as support_connection:
        await support_connection.execute(
            text("SELECT set_config('app.support_session', 'true', true)")
        )
        await support_connection.execute(
            text(
                "INSERT INTO user_assignment "
                "(user_id,tenant_id,membership_id,branch_id,role_id,is_active) "
                "VALUES (:user,:tenant,:membership,:branch,:role,true)"
            ),
            {
                "user": user_id,
                "tenant": context.tenant_id,
                "membership": membership_id,
                "branch": context.branch_id,
                "role": role_id,
            },
        )
    engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    try:
        async with engine.begin() as app:
            await app.execute(
                text(
                    "SELECT set_config('app.tenant_id',:tenant,true), "
                    "set_config('app.user_id',:user,true)"
                ),
                {"tenant": str(context.tenant_id), "user": str(user_id)},
            )
            async with AsyncSession(
                bind=app, expire_on_commit=False, join_transaction_mode="create_savepoint"
            ) as app_session:
                result = await PreparedUnitLedgerService(
                    UnitLedgerRepository(app_session)
                ).dispense(
                    tenant_id=context.tenant_id,
                    branch_id=context.branch_id,
                    catalog_id=context.catalog_id,
                    operation_id=uuid4(),
                    packaging=PACKAGING,
                    sale_format="unit",
                    quantity=1,
                    today=TODAY,
                    allowed_branch_ids={context.branch_id},
                )
                assert result.base_units == 1
                await app_session.commit()
            for table, expected in (
                ("unit_stock_lot", 1),
                ("unit_stock_operation", 2),
                ("unit_stock_entry", 2),
            ):
                assert await app.scalar(text(f"SELECT count(*) FROM {table}")) == expected
            with pytest.raises(DBAPIError):
                async with app.begin_nested():
                    await app.execute(
                        text(
                            "INSERT INTO unit_stock_operation "
                            "(id,tenant_id,branch_id,catalog_id,unit,units_per_pack,dispense_step,"
                            "kind,sale_format,quantity,base_units,request_hash) VALUES "
                            "(:id,:tenant,:branch,:catalog,'tablet',30,1,"
                            "'dispense','unit',1,1,:hash)"
                        ),
                        {
                            "id": uuid4(),
                            "tenant": context.tenant_id,
                            "branch": other_branch,
                            "catalog": context.catalog_id,
                            "hash": "c" * 64,
                        },
                    )
            await app.execute(
                text("SELECT set_config('app.tenant_id',:tenant,true)"),
                {"tenant": str(other_tenant.tenant_id)},
            )
            assert await app.scalar(text("SELECT count(*) FROM unit_stock_lot")) == 0
    finally:
        await engine.dispose()


async def test_prepared_tables_have_forced_rls_and_no_runtime_delete(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        for table in TABLES:
            assert await connection.scalar(
                text(
                    "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                    "WHERE oid = to_regclass(:table)"
                ),
                {"table": f"public.{table}"},
            )
            for role in ("aurum_app", "aurum_support"):
                assert not await connection.scalar(
                    text("SELECT has_table_privilege(:role, :table, 'DELETE')"),
                    {"role": role, "table": f"public.{table}"},
                )


async def test_nonempty_downgrade_refuses_without_losing_history(
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    async with AsyncSession(db_engine, expire_on_commit=False) as support:
        async with support.begin():
            await support.execute(text("SELECT set_config('app.support_session', 'true', true)"))
            context = await seed_ledger_context(support)
            lot_id = await prepare(support, context)
    source = (
        Path(__file__).resolve().parents[2] / "alembic/versions/0162_prepared_unit_stock_ledger.py"
    )
    spec = importlib.util.spec_from_file_location("prepared_ledger_downgrade", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def rehearse(connection: Connection) -> None:
        assert str(connection.scalar(text("SELECT current_database()"))).endswith("_test")
        with Operations.context(MigrationContext.configure(connection)):
            module.downgrade()

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            with pytest.raises(DBAPIError, match="unit_stock_nonempty_downgrade_forbidden"):
                async with connection.begin_nested():
                    await connection.run_sync(rehearse)
            assert (
                await connection.scalar(
                    text("SELECT closed_packs FROM unit_stock_lot WHERE id = :lot"), {"lot": lot_id}
                )
                == 1
            )
        finally:
            await transaction.rollback()
