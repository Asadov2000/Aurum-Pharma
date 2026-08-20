"""Single-purpose database pool for isolated subscription transitions."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.billing_worker_config import get_billing_worker_settings

settings = get_billing_worker_settings()
billing_worker_engine = create_async_engine(
    settings.DATABASE_URL_BILLING_WORKER,
    echo=False,
    hide_parameters=True,
    poolclass=NullPool,
)
BillingWorkerSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    billing_worker_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)
