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
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

settings = get_settings()

app_engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL_APP,
    echo=False,
    hide_parameters=True,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

support_engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL_SUPPORT,
    echo=False,
    hide_parameters=True,
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

# Worker engine (Celery tasks). Each task runs in its own short-lived event
# loop (asyncio.run), so a pooled connection would outlive its loop and asyncpg
# would raise "got Future attached to a different loop". NullPool opens a fresh
# connection per checkout and closes it on return, so nothing crosses loops.
# Uses the support role (BYPASSRLS) like the tasks need; the pooled
# support_engine stays dedicated to the request path (see deps.get_db).
worker_engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL_SUPPORT,
    echo=False,
    hide_parameters=True,
    poolclass=NullPool,
)

WorkerSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    worker_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)
