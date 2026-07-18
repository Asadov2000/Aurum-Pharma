"""Authorization revision tables must remain tenant-isolated for aurum_app."""

from __future__ import annotations

import asyncio
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
async def support_engine_revision() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        get_settings().DATABASE_URL_SUPPORT,
        poolclass=NullPool,
        connect_args={"server_settings": {"app.support_session": "true"}},
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def app_engine_revision() -> AsyncIterator[AsyncEngine]:
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


async def test_revision_tables_hide_other_tenants(
    support_engine_revision: AsyncEngine,
    app_engine_revision: AsyncEngine,
) -> None:
    suffix = uuid4().hex
    tenant_ids: list[str] = []
    user_id = ""

    try:
        async with support_engine_revision.begin() as connection:
            tenant_rows = await connection.execute(
                text(
                    "INSERT INTO public.tenant (name, contact_email) VALUES "
                    "(:first_name, :first_email), (:second_name, :second_email) "
                    "RETURNING id"
                ),
                {
                    "first_name": f"Revision first {suffix}",
                    "first_email": f"revision-first-{suffix}@example.invalid",
                    "second_name": f"Revision second {suffix}",
                    "second_email": f"revision-second-{suffix}@example.invalid",
                },
            )
            tenant_ids = [str(row[0]) for row in tenant_rows.fetchall()]
            user_id = str(
                (
                    await connection.execute(
                        text(
                            "INSERT INTO public.app_user "
                            "(email, full_name, home_tenant_id, status) "
                            "VALUES (:email, 'Revision actor', :tenant_id, 'active') "
                            "RETURNING id"
                        ),
                        {
                            "email": f"revision-actor-{suffix}@example.invalid",
                            "tenant_id": tenant_ids[0],
                        },
                    )
                ).scalar_one()
            )
            await connection.execute(
                text(
                    "INSERT INTO public.authorization_subject_revision "
                    "(tenant_id, user_id, revision) VALUES (:tenant_id, :user_id, 7)"
                ),
                {"tenant_id": tenant_ids[0], "user_id": user_id},
            )

        async with app_engine_revision.begin() as connection:
            await _set_actor_context(
                connection,
                tenant_id=tenant_ids[0],
                user_id=user_id,
            )
            policy_rows = await connection.execute(
                text(
                    "SELECT tenant_id FROM public.authorization_policy_revision "
                    "WHERE tenant_id IN (:first_tenant, :second_tenant)"
                ),
                {"first_tenant": tenant_ids[0], "second_tenant": tenant_ids[1]},
            )
            subject_rows = await connection.execute(
                text(
                    "SELECT tenant_id, user_id FROM public.authorization_subject_revision "
                    "WHERE tenant_id IN (:first_tenant, :second_tenant)"
                ),
                {"first_tenant": tenant_ids[0], "second_tenant": tenant_ids[1]},
            )

            assert [str(row[0]) for row in policy_rows.fetchall()] == [tenant_ids[0]]
            assert [(str(row[0]), str(row[1])) for row in subject_rows.fetchall()] == [
                (tenant_ids[0], user_id)
            ]

            with pytest.raises(DBAPIError) as mutation_error:
                await connection.execute(
                    text("SELECT public.bump_authorization_policy_revision(:tenant_id)"),
                    {"tenant_id": tenant_ids[0]},
                )
            assert getattr(mutation_error.value.orig, "sqlstate", None) == "42501"
    finally:
        async with support_engine_revision.begin() as connection:
            for tenant_id in tenant_ids:
                await connection.execute(
                    text(
                        "DELETE FROM public.authorization_subject_revision "
                        "WHERE tenant_id = :tenant_id"
                    ),
                    {"tenant_id": tenant_id},
                )
                await connection.execute(
                    text(
                        "DELETE FROM public.authorization_policy_revision "
                        "WHERE tenant_id = :tenant_id"
                    ),
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
            if user_id:
                await connection.execute(
                    text("DELETE FROM public.app_user WHERE id = :user_id"),
                    {"user_id": user_id},
                )


async def test_policy_revision_is_atomic_under_concurrent_mutations(
    support_engine_revision: AsyncEngine,
) -> None:
    suffix = uuid4().hex
    tenant_id = ""
    role_id = ""

    try:
        async with support_engine_revision.begin() as connection:
            tenant_id = str(
                (
                    await connection.execute(
                        text(
                            "INSERT INTO public.tenant (name, contact_email) "
                            "VALUES (:name, :email) RETURNING id"
                        ),
                        {
                            "name": f"Concurrent revision {suffix}",
                            "email": f"concurrent-{suffix}@example.invalid",
                        },
                    )
                ).scalar_one()
            )
            role_id = str(
                (
                    await connection.execute(
                        text(
                            "INSERT INTO public.role "
                            "(tenant_id, name, level, is_system) "
                            "VALUES (:tenant_id, :name, 4, false) RETURNING id"
                        ),
                        {
                            "tenant_id": tenant_id,
                            "name": f"Concurrent role {suffix}",
                        },
                    )
                ).scalar_one()
            )
            before = int(
                (
                    await connection.execute(
                        text(
                            "SELECT revision FROM public.authorization_policy_revision "
                            "WHERE tenant_id = :tenant_id"
                        ),
                        {"tenant_id": tenant_id},
                    )
                ).scalar_one()
            )

        async def mutate_role() -> None:
            async with support_engine_revision.begin() as connection:
                await connection.execute(
                    text("UPDATE public.role SET level = 4 " "WHERE id = :role_id"),
                    {"role_id": role_id},
                )

        await asyncio.gather(mutate_role(), mutate_role())

        async with support_engine_revision.begin() as connection:
            after = int(
                (
                    await connection.execute(
                        text(
                            "SELECT revision FROM public.authorization_policy_revision "
                            "WHERE tenant_id = :tenant_id"
                        ),
                        {"tenant_id": tenant_id},
                    )
                ).scalar_one()
            )
        assert after == before + 2
    finally:
        async with support_engine_revision.begin() as connection:
            if role_id:
                await connection.execute(
                    text("DELETE FROM public.role WHERE id = :role_id"),
                    {"role_id": role_id},
                )
            if tenant_id:
                await connection.execute(
                    text("DELETE FROM public.tenant WHERE id = :tenant_id"),
                    {"tenant_id": tenant_id},
                )
