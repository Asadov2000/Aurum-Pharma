"""Tenant-scoped, loop-safe database pool for catalog import workers."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

settings = get_settings()
catalog_worker_engine = create_async_engine(
    settings.DATABASE_URL_APP,
    echo=False,
    hide_parameters=True,
    poolclass=NullPool,
)
CatalogWorkerSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    catalog_worker_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)
