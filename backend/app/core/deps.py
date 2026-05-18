"""FastAPI dependencies.

`get_db` picks the app or support pool based on the auth context populated by
`AuthContextMiddleware`, opens a transaction, and seeds the RLS GUCs:
    app.tenant_id      — UUID of the active tenant
    app.user_id        — UUID of the acting user
    app.support_session — 'true' when a support/dev session is bypassing RLS

The session is committed if the request handler returns normally; rolled back
on exception. Domain services should not commit themselves.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AppSessionLocal, SupportSessionLocal


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    tenant_id = getattr(request.state, "tenant_id", None)
    user_id = getattr(request.state, "user_id", None)
    is_support = bool(getattr(request.state, "is_support_session", False))
    use_support_pool = bool(getattr(request.state, "use_support_pool", False))

    sessionmaker = SupportSessionLocal if use_support_pool else AppSessionLocal

    async with sessionmaker() as session:
        async with session.begin():
            if tenant_id is not None:
                await session.execute(
                    text("SELECT set_config('app.tenant_id', :v, true)"),
                    {"v": str(tenant_id)},
                )
            if user_id is not None:
                await session.execute(
                    text("SELECT set_config('app.user_id', :v, true)"),
                    {"v": str(user_id)},
                )
            if is_support:
                await session.execute(
                    text("SELECT set_config('app.support_session', 'true', true)"),
                )
            yield session
