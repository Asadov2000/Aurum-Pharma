"""Priced stock snapshots retain the original branch and tenant database scope."""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from tests.domains.inventory.test_unit_cost_ledger import prepare_priced, take
from tests.domains.inventory.test_unit_ledger import LedgerContext, seed_ledger_context


async def test_priced_cost_snapshots_hide_foreign_branch_and_tenant(
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
            prepared = await prepare_priced(support, context)
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
                await take(support, scoped, 1)
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
            lots = (
                await app.execute(text("SELECT id,purchase_cost_per_pack FROM unit_stock_lot"))
            ).all()
            assert [(row.id, row.purchase_cost_per_pack) for row in lots] == [
                (prepared.lot_id, Decimal("10.00"))
            ]
            costs = (
                await app.execute(
                    text(
                        "SELECT lot_id,cost_original_pack_cost,cost_amount FROM unit_stock_entry "
                        "WHERE cost_version IS NOT NULL"
                    )
                )
            ).all()
            assert [
                (row.lot_id, row.cost_original_pack_cost, row.cost_amount) for row in costs
            ] == [(prepared.lot_id, Decimal("10.00"), Decimal("0.33"))]
            await app.execute(
                text("SELECT set_config('app.tenant_id',:tenant,true)"),
                {"tenant": str(other_tenant.tenant_id)},
            )
            assert await app.scalar(text("SELECT count(*) FROM unit_stock_lot")) == 0
            assert (
                await app.scalar(
                    text("SELECT count(*) FROM unit_stock_entry WHERE cost_version IS NOT NULL")
                )
                == 0
            )
    finally:
        await engine.dispose()
