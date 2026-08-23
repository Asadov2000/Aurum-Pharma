"""RLS keeps account preferences visible only to their owning user."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


@pytest_asyncio.fixture
async def support_engine_preferences() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_SUPPORT, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest_asyncio.fixture
async def app_engine_preferences() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_user_preferences_rls_is_self_only(
    support_engine_preferences: AsyncEngine,
    app_engine_preferences: AsyncEngine,
) -> None:
    suffix = uuid4().hex[:8]
    user_ids: list[str] = []
    try:
        async with support_engine_preferences.begin() as connection:
            users = await connection.execute(
                text(
                    "INSERT INTO app_user (email, full_name, status) VALUES "
                    "(:email_a, 'Preferences A', 'active'), "
                    "(:email_b, 'Preferences B', 'active'), "
                    "(:email_c, 'Preferences C', 'active') RETURNING id"
                ),
                {
                    "email_a": f"preferences-rls-a-{suffix}@aurum.tj",
                    "email_b": f"preferences-rls-b-{suffix}@aurum.tj",
                    "email_c": f"preferences-rls-c-{suffix}@aurum.tj",
                },
            )
            user_ids = [str(row[0]) for row in users]
            await connection.execute(
                text(
                    "INSERT INTO user_preferences (user_id, theme) VALUES "
                    "(:user_a, 'dark'), (:user_b, 'light')"
                ),
                {"user_a": user_ids[0], "user_b": user_ids[1]},
            )

        async with app_engine_preferences.connect() as connection:
            await connection.execute(
                text("SELECT set_config('app.user_id', :value, false)"),
                {"value": user_ids[0]},
            )
            rows = (
                (await connection.execute(text("SELECT user_id, theme FROM user_preferences")))
                .mappings()
                .all()
            )
            assert [(str(row["user_id"]), row["theme"]) for row in rows] == [(user_ids[0], "dark")]

        async with app_engine_preferences.begin() as connection:
            await connection.execute(
                text("SELECT set_config('app.user_id', :value, false)"),
                {"value": user_ids[0]},
            )
            result = await connection.execute(
                text("UPDATE user_preferences SET theme = 'dark' " "WHERE user_id = :other_user"),
                {"other_user": user_ids[1]},
            )
            assert result.rowcount == 0

        with pytest.raises(DBAPIError):
            async with app_engine_preferences.begin() as connection:
                await connection.execute(
                    text("SELECT set_config('app.user_id', :value, false)"),
                    {"value": user_ids[0]},
                )
                await connection.execute(
                    text("INSERT INTO user_preferences (user_id) VALUES (:other_user)"),
                    {"other_user": user_ids[2]},
                )
    finally:
        if user_ids:
            async with support_engine_preferences.begin() as connection:
                await connection.execute(
                    text("DELETE FROM app_user WHERE id = ANY(:user_ids)"),
                    {"user_ids": user_ids},
                )
