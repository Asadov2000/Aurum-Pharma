"""Single-purpose database pool for the isolated invitation mailer."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.mailer_config import get_mailer_settings

settings = get_mailer_settings()
mailer_engine = create_async_engine(
    settings.DATABASE_URL_MAILER,
    echo=False,
    hide_parameters=True,
    poolclass=NullPool,
)
MailerSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    mailer_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)
