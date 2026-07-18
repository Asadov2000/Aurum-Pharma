"""The application role must not read support MFA material directly."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


@pytest_asyncio.fixture
async def app_engine_mfa() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        get_settings().DATABASE_URL_APP,
        poolclass=NullPool,
    )
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "table_name",
    [
        "support_mfa",
        "support_mfa_recovery_code",
        "auth_mfa_challenge",
    ],
)
async def test_app_role_cannot_select_sensitive_mfa_tables(
    app_engine_mfa: AsyncEngine,
    table_name: str,
) -> None:
    with pytest.raises(DBAPIError) as error:
        async with app_engine_mfa.connect() as connection:
            await connection.execute(text(f"SELECT count(*) FROM public.{table_name}"))

    assert getattr(error.value.orig, "sqlstate", None) == "42501"
