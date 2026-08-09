"""RLS isolates POS payment attempts by tenant."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


@pytest_asyncio.fixture
async def support_engine_payment_attempts() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_SUPPORT, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def app_engine_payment_attempts() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_pos_payment_attempt_rls_enforces_tenant_isolation(
    support_engine_payment_attempts: AsyncEngine,
    app_engine_payment_attempts: AsyncEngine,
) -> None:
    suffix = uuid4().hex[:8]
    tenant_ids: list[str] = []
    user_ids: list[str] = []
    try:
        async with support_engine_payment_attempts.begin() as conn:
            tenants = await conn.execute(
                text(
                    "INSERT INTO tenant (name, contact_email, status) VALUES "
                    "(:name_a, :email_a, 'active'), (:name_b, :email_b, 'active') "
                    "RETURNING id"
                ),
                {
                    "name_a": f"Attempt RLS A {suffix}",
                    "email_a": f"attempt-rls-a-{suffix}@aurum.tj",
                    "name_b": f"Attempt RLS B {suffix}",
                    "email_b": f"attempt-rls-b-{suffix}@aurum.tj",
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
                    "email_a": f"attempt-user-a-{suffix}@aurum.tj",
                    "email_b": f"attempt-user-b-{suffix}@aurum.tj",
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
            attempts = await conn.execute(
                text(
                    "INSERT INTO pos_payment_attempt "
                    "(tenant_id, sale_id, cashier_user_id, operation_id, operation_hash, "
                    "payment_method, amount) VALUES "
                    "(:tenant_a, :sale_a, :user_a, gen_random_uuid(), repeat('a', 64), "
                    "'card', 10), "
                    "(:tenant_b, :sale_b, :user_b, gen_random_uuid(), repeat('b', 64), "
                    "'qr', 20) RETURNING id"
                ),
                {
                    "tenant_a": tenant_ids[0],
                    "tenant_b": tenant_ids[1],
                    "sale_a": sale_ids[0],
                    "sale_b": sale_ids[1],
                    "user_a": user_ids[0],
                    "user_b": user_ids[1],
                },
            )
            attempt_ids = [str(row[0]) for row in attempts]

        async with app_engine_payment_attempts.connect() as conn:
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :value, false)"),
                {"value": tenant_ids[0]},
            )
            await conn.execute(
                text("SELECT set_config('app.user_id', :value, false)"),
                {"value": user_ids[0]},
            )
            rows = (
                (await conn.execute(text("SELECT id FROM pos_payment_attempt ORDER BY id")))
                .scalars()
                .all()
            )
            assert [str(attempt_id) for attempt_id in rows] == [attempt_ids[0]]

            await conn.execute(
                text("SELECT set_config('app.tenant_id', :value, false)"),
                {"value": tenant_ids[1]},
            )
            await conn.execute(
                text("SELECT set_config('app.user_id', :value, false)"),
                {"value": user_ids[1]},
            )
            rows = (
                (await conn.execute(text("SELECT id FROM pos_payment_attempt ORDER BY id")))
                .scalars()
                .all()
            )
            assert [str(attempt_id) for attempt_id in rows] == [attempt_ids[1]]
    finally:
        if tenant_ids:
            async with support_engine_payment_attempts.begin() as conn:
                await conn.execute(
                    text("DELETE FROM tenant WHERE id = ANY(:tenant_ids)"),
                    {"tenant_ids": tenant_ids},
                )
                if user_ids:
                    await conn.execute(
                        text("DELETE FROM app_user WHERE id = ANY(:user_ids)"),
                        {"user_ids": user_ids},
                    )
