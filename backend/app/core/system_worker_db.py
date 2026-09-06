"""Loop-safe database pool for cross-tenant maintenance tasks."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.system_worker_config import get_system_worker_settings

settings = get_system_worker_settings()
system_worker_engine = create_async_engine(
    settings.DATABASE_URL_WORKER,
    echo=False,
    hide_parameters=True,
    poolclass=NullPool,
)
SystemWorkerSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    system_worker_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)
