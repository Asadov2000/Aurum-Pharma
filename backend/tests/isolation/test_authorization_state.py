"""Database authorization gates must honor active state."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


@pytest_asyncio.fixture
async def support_engine_authorization() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_SUPPORT, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def app_engine_authorization() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _set_actor_context(
    connection: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
) -> None:
    await connection.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": tenant_id},
    )
    await connection.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": user_id},
    )


async def _create_assignment(
    connection: AsyncConnection,
    *,
    tenant_id: str,
    user_id: str,
    role_id: str,
) -> None:
    await connection.execute(
        text(
            "SELECT public.create_tenant_user_assignment("
            ":tenant_id, :user_id, NULL, :role_id, false)"
        ),
        {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "role_id": role_id,
        },
    )


async def test_assignment_gate_rejects_inactive_permission(
    support_engine_authorization: AsyncEngine,
    app_engine_authorization: AsyncEngine,
) -> None:
    suffix = uuid4().hex
    tenant_id = ""
    actor_id = ""
    role_ids: list[str] = []
    user_ids: list[str] = []

    try:
        async with support_engine_authorization.begin() as connection:
            tenant_id = str(
                (
                    await connection.execute(
                        text(
                            "INSERT INTO public.tenant (name, contact_email) "
                            "VALUES (:name, :email) RETURNING id"
                        ),
                        {
                            "name": f"Authorization state {suffix}",
                            "email": f"authorization-{suffix}@example.invalid",
                        },
                    )
                ).scalar_one()
            )
            rows = await connection.execute(
                text(
                    "INSERT INTO public.app_user "
                    "(email, full_name, home_tenant_id, status) VALUES "
                    "(:actor_email, 'Actor', :tenant_id, 'active'), "
                    "(:first_email, 'First target', :tenant_id, 'active'), "
                    "(:second_email, 'Second target', :tenant_id, 'active') "
                    "RETURNING id"
                ),
                {
                    "actor_email": f"actor-{suffix}@example.invalid",
                    "first_email": f"first-{suffix}@example.invalid",
                    "second_email": f"second-{suffix}@example.invalid",
                    "tenant_id": tenant_id,
                },
            )
            user_ids = [str(row[0]) for row in rows.fetchall()]
            actor_id = user_ids[0]
            roles = await connection.execute(
                text(
                    "INSERT INTO public.role "
                    "(tenant_id, name, level, is_system) VALUES "
                    "(:tenant_id, :actor_role, 3, false), "
                    "(:tenant_id, :target_role, 4, false) RETURNING id"
                ),
                {
                    "tenant_id": tenant_id,
                    "actor_role": f"Actor role {suffix}",
                    "target_role": f"Target role {suffix}",
                },
            )
            role_ids = [str(row[0]) for row in roles.fetchall()]
            await connection.execute(
                text(
                    "INSERT INTO public.role_permission (role_id, permission_code) "
                    "VALUES (:role_id, 'roles.assign')"
                ),
                {"role_id": role_ids[0]},
            )
            await connection.execute(
                text(
                    "INSERT INTO public.user_assignment (user_id, tenant_id, role_id) "
                    "VALUES (:user_id, :tenant_id, :role_id)"
                ),
                {
                    "user_id": actor_id,
                    "tenant_id": tenant_id,
                    "role_id": role_ids[0],
                },
            )

        async with app_engine_authorization.begin() as connection:
            await _set_actor_context(
                connection,
                tenant_id=tenant_id,
                user_id=actor_id,
            )
            await _create_assignment(
                connection,
                tenant_id=tenant_id,
                user_id=user_ids[1],
                role_id=role_ids[1],
            )

        async with support_engine_authorization.begin() as connection:
            await connection.execute(
                text("UPDATE public.permission SET is_active = false WHERE code = 'roles.assign'")
            )

        with pytest.raises(DBAPIError) as error:
            async with app_engine_authorization.begin() as connection:
                await _set_actor_context(
                    connection,
                    tenant_id=tenant_id,
                    user_id=actor_id,
                )
                await _create_assignment(
                    connection,
                    tenant_id=tenant_id,
                    user_id=user_ids[2],
                    role_id=role_ids[1],
                )
        assert getattr(error.value.orig, "sqlstate", None) == "42501"
    finally:
        async with support_engine_authorization.begin() as connection:
            await connection.execute(
                text("UPDATE public.permission SET is_active = true WHERE code = 'roles.assign'")
            )
            if tenant_id:
                await connection.execute(
                    text("DELETE FROM public.audit_log WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant_id},
                )
                await connection.execute(
                    text("DELETE FROM public.user_assignment WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant_id},
                )
                await connection.execute(
                    text("DELETE FROM public.role WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant_id},
                )
                await connection.execute(
                    text("DELETE FROM public.app_user WHERE home_tenant_id = :tenant_id"),
                    {"tenant_id": tenant_id},
                )
                await connection.execute(
                    text("DELETE FROM public.tenant_settings WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant_id},
                )
                await connection.execute(
                    text("DELETE FROM public.tenant WHERE id = :tenant_id"),
                    {"tenant_id": tenant_id},
                )
