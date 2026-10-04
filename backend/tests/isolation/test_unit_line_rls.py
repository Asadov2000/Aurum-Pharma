"""Prepared POS lines preserve PostgreSQL branch and tenant authorization."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from tests.domains.inventory.test_unit_cost_ledger import prepare_priced
from tests.domains.inventory.test_unit_ledger import LedgerContext, seed_ledger_context
from tests.domains.pos.test_unit_line import post


async def test_prepared_lines_hide_foreign_branch_and_tenant(
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    user_id, membership_id, role_id = uuid4(), uuid4(), uuid4()
    async with AsyncSession(db_engine, expire_on_commit=False) as support:
        async with support.begin():
            assert str(await support.scalar(text("SELECT current_database()"))).endswith("_test")
            await support.execute(text("SELECT set_config('app.support_session', 'true', true)"))
            context = await seed_ledger_context(support)
            other_tenant = await seed_ledger_context(support)
            other_branch = await support.scalar(
                text("INSERT INTO branch (tenant_id,name) VALUES (:tenant,'Other') RETURNING id"),
                {"tenant": context.tenant_id},
            )
            await prepare_priced(support, context)
            await prepare_priced(support, other_tenant, cost=Decimal("20.00"))
            await prepare_priced(
                support,
                LedgerContext(context.tenant_id, other_branch, context.catalog_id),
                cost=Decimal("30.00"),
            )
            for scoped in (
                context,
                other_tenant,
                LedgerContext(context.tenant_id, other_branch, context.catalog_id),
            ):
                await post(support, scoped)
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
            assert await app.scalar(text("SELECT current_user")) == "aurum_app"
            lines = (
                await app.execute(
                    text(
                        "SELECT tenant_id,branch_id,catalog_id,original_cost_amount "
                        "FROM pos_unit_sale_line"
                    )
                )
            ).all()
            assert [
                (row.tenant_id, row.branch_id, row.catalog_id, row.original_cost_amount)
                for row in lines
            ] == [(context.tenant_id, context.branch_id, context.catalog_id, Decimal("0.33"))]
            assert await app.scalar(text("SELECT count(*) FROM pos_unit_sale_command")) == 1
            async with AsyncSession(
                bind=app, expire_on_commit=False, join_transaction_mode="create_savepoint"
            ) as app_session:
                result = await post(app_session, context)
                assert result.snapshot.original_cost_amount == Decimal("0.33")
                await app_session.commit()
            assert await app.scalar(text("SELECT count(*) FROM pos_unit_sale_line")) == 2
            for table in ("pos_unit_sale_command", "pos_unit_sale_line"):
                assert (
                    await app.scalar(
                        text(f"SELECT count(*) FROM {table} WHERE branch_id=:branch"),
                        {"branch": other_branch},
                    )
                    == 0
                )
            with pytest.raises(DBAPIError):
                async with app.begin_nested():
                    await app.execute(
                        text(
                            "INSERT INTO pos_unit_sale_command "
                            "(id,tenant_id,branch_id,catalog_id,request_hash,"
                            "sale_format,quantity,discount_amount) "
                            "VALUES (:id,:tenant,:branch,:catalog,:hash,'unit',1,0.00)"
                        ),
                        {
                            "id": uuid4(),
                            "tenant": context.tenant_id,
                            "branch": other_branch,
                            "catalog": context.catalog_id,
                            "hash": "d" * 64,
                        },
                    )
            await app.execute(
                text("SELECT set_config('app.tenant_id',:tenant,true)"),
                {"tenant": str(other_tenant.tenant_id)},
            )
            for table in ("pos_unit_sale_command", "pos_unit_sale_line"):
                assert await app.scalar(text(f"SELECT count(*) FROM {table}")) == 0
    finally:
        await engine.dispose()


async def test_command_without_line_cannot_be_committed(db_session: AsyncSession) -> None:
    context = await seed_ledger_context(db_session)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO pos_unit_sale_command "
                    "(id,tenant_id,branch_id,catalog_id,request_hash,"
                    "sale_format,quantity,discount_amount) "
                    "VALUES (:id,:tenant,:branch,:catalog,:hash,'unit',1,0.00)"
                ),
                {
                    "id": uuid4(),
                    "tenant": context.tenant_id,
                    "branch": context.branch_id,
                    "catalog": context.catalog_id,
                    "hash": "e" * 64,
                },
            )
            await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


async def test_prepared_pos_tables_force_rls_and_reject_runtime_delete(
    maintenance_engine: AsyncEngine,
) -> None:
    async with maintenance_engine.connect() as connection:
        for table in ("pos_unit_sale_command", "pos_unit_sale_line"):
            assert await connection.scalar(
                text(
                    "SELECT relrowsecurity AND relforcerowsecurity "
                    "FROM pg_class WHERE oid=to_regclass(:table)"
                ),
                {"table": f"public.{table}"},
            )
            for role in ("aurum_app", "aurum_support"):
                assert not await connection.scalar(
                    text("SELECT has_table_privilege(:role,:table,'DELETE')"),
                    {"role": role, "table": f"public.{table}"},
                )
        for role in ("aurum_app", "aurum_support", "aurum_schema_owner"):
            can_digest = await connection.scalar(
                text("SELECT has_function_privilege(:role,'public.digest(bytea,text)','EXECUTE')"),
                {"role": role},
            )
            assert can_digest is (role == "aurum_schema_owner")
