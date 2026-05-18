"""Database engines and session factories.

Two pools:
- `app_engine` (role aurum_app) — RLS enabled. Default for tenant traffic.
- `support_engine` (role aurum_support, BYPASSRLS) — for developers/administrators.

Engine selection is per-request, driven by the auth context. See `app.core.deps.get_db`.
"""
from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

settings = get_settings()

app_engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL_APP,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

support_engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL_SUPPORT,
    echo=False,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

AppSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    app_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

SupportSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    support_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)
