"""RLS isolation for the tenant-scoped POS command ledger."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


@pytest_asyncio.fixture
async def support_engine_pos_commands() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_SUPPORT, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def app_engine_pos_commands() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_pos_command_rls_enforces_tenant_isolation(
    support_engine_pos_commands: AsyncEngine,
    app_engine_pos_commands: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    suffix = uuid4().hex[:8]
    tenant_ids: list[str] = []
    user_ids: list[str] = []
    command_ids: list[str] = []
    try:
        async with support_engine_pos_commands.begin() as conn:
            tenants = await conn.execute(
                text(
                    "INSERT INTO tenant (name, contact_email, status) VALUES "
                    "(:name_a, :email_a, 'active'), (:name_b, :email_b, 'active') "
                    "RETURNING id"
                ),
                {
                    "name_a": f"POS command RLS A {suffix}",
                    "email_a": f"pos-command-a-{suffix}@aurum.tj",
                    "name_b": f"POS command RLS B {suffix}",
                    "email_b": f"pos-command-b-{suffix}@aurum.tj",
                },
            )
            tenant_ids = [str(row[0]) for row in tenants]
            users = await conn.execute(
                text(
                    "INSERT INTO app_user (email, full_name, home_tenant_id, status) VALUES "
                    "(:email_a, 'A', :tenant_a, 'active'), "
                    "(:email_b, 'B', :tenant_b, 'active') RETURNING id"
                ),
                {
                    "email_a": f"pos-command-user-a-{suffix}@aurum.tj",
                    "email_b": f"pos-command-user-b-{suffix}@aurum.tj",
                    "tenant_a": tenant_ids[0],
                    "tenant_b": tenant_ids[1],
                },
            )
            user_ids = [str(row[0]) for row in users]
            branches = await conn.execute(
                text(
                    "INSERT INTO branch (tenant_id, name) VALUES "
                    "(:tenant_a, 'A'), (:tenant_b, 'B') RETURNING id"
                ),
                {"tenant_a": tenant_ids[0], "tenant_b": tenant_ids[1]},
            )
            branch_ids = [str(row[0]) for row in branches]
            registers = await conn.execute(
                text(
                    "INSERT INTO register (tenant_id, branch_id, name) VALUES "
                    "(:tenant_a, :branch_a, 'A'), (:tenant_b, :branch_b, 'B') RETURNING id"
                ),
                {
                    "tenant_a": tenant_ids[0],
                    "tenant_b": tenant_ids[1],
                    "branch_a": branch_ids[0],
                    "branch_b": branch_ids[1],
                },
            )
            register_ids = [str(row[0]) for row in registers]
            shifts = await conn.execute(
                text(
                    "INSERT INTO shift "
                    "(tenant_id, branch_id, register_id, opened_by_user_id, status) VALUES "
                    "(:tenant_a, :branch_a, :register_a, :user_a, 'open'), "
                    "(:tenant_b, :branch_b, :register_b, :user_b, 'open') RETURNING id"
                ),
                {
                    "tenant_a": tenant_ids[0],
                    "tenant_b": tenant_ids[1],
                    "branch_a": branch_ids[0],
                    "branch_b": branch_ids[1],
                    "register_a": register_ids[0],
                    "register_b": register_ids[1],
                    "user_a": user_ids[0],
                    "user_b": user_ids[1],
                },
            )
            shift_ids = [str(row[0]) for row in shifts]
            sales = await conn.execute(
                text(
                    "INSERT INTO sale "
                    "(tenant_id, branch_id, register_id, shift_id, cashier_user_id) VALUES "
                    "(:tenant_a, :branch_a, :register_a, :shift_a, :user_a), "
                    "(:tenant_b, :branch_b, :register_b, :shift_b, :user_b) RETURNING id"
                ),
                {
                    "tenant_a": tenant_ids[0],
                    "tenant_b": tenant_ids[1],
                    "branch_a": branch_ids[0],
                    "branch_b": branch_ids[1],
                    "register_a": register_ids[0],
                    "register_b": register_ids[1],
                    "shift_a": shift_ids[0],
                    "shift_b": shift_ids[1],
                    "user_a": user_ids[0],
                    "user_b": user_ids[1],
                },
            )
            sale_ids = [str(row[0]) for row in sales]
            commands = await conn.execute(
                text(
                    "INSERT INTO pos_command "
                    "(tenant_id, operation_id, actor_user_id, sale_id, command_type, "
                    "request_hash, result_payload) VALUES "
                    "(:tenant_a, :operation_a, :user_a, :sale_a, 'sale.create', "
                    "repeat('a', 64), '{\"command_type\":\"sale.create\"}'::jsonb), "
                    "(:tenant_b, :operation_b, :user_b, :sale_b, 'sale.create', "
                    "repeat('b', 64), '{\"command_type\":\"sale.create\"}'::jsonb) RETURNING id"
                ),
                {
                    "tenant_a": tenant_ids[0],
                    "tenant_b": tenant_ids[1],
                    "operation_a": str(uuid4()),
                    "operation_b": str(uuid4()),
                    "user_a": user_ids[0],
                    "user_b": user_ids[1],
                    "sale_a": sale_ids[0],
                    "sale_b": sale_ids[1],
                },
            )
            command_ids = [str(row[0]) for row in commands]

        async with app_engine_pos_commands.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :value, false)"),
                {"value": tenant_ids[0]},
            )
            await conn.execute(
                text("SELECT set_config('app.user_id', :value, false)"),
                {"value": user_ids[0]},
            )
            visible = (await conn.execute(text("SELECT id FROM pos_command"))).scalars().all()
            assert [str(command_id) for command_id in visible] == [command_ids[0]]

            await conn.execute(
                text("SELECT set_config('app.tenant_id', :value, false)"),
                {"value": tenant_ids[1]},
            )
            await conn.execute(
                text("SELECT set_config('app.user_id', :value, false)"),
                {"value": user_ids[1]},
            )
            visible = (await conn.execute(text("SELECT id FROM pos_command"))).scalars().all()
            assert [str(command_id) for command_id in visible] == [command_ids[1]]
    finally:
        if tenant_ids:
            async with maintenance_engine.begin() as conn:
                await conn.execute(
                    text("ALTER TABLE public.pos_command DISABLE TRIGGER trg_pos_command_immutable")
                )
            try:
                async with support_engine_pos_commands.begin() as conn:
                    await conn.execute(
                        text("DELETE FROM tenant WHERE id = ANY(:tenant_ids)"),
                        {"tenant_ids": tenant_ids},
                    )
                    if user_ids:
                        await conn.execute(
                            text("DELETE FROM app_user WHERE id = ANY(:user_ids)"),
                            {"user_ids": user_ids},
                        )
                async with maintenance_engine.begin() as conn:
                    await conn.execute(
                        text("DELETE FROM audit_log WHERE tenant_id = ANY(:tenant_ids)"),
                        {"tenant_ids": tenant_ids},
                    )
            finally:
                async with maintenance_engine.begin() as conn:
                    await conn.execute(
                        text(
                            "ALTER TABLE public.pos_command ENABLE TRIGGER "
                            "trg_pos_command_immutable"
                        )
                    )
