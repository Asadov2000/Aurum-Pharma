"""Account MFA policy lookups preserve the authenticated session boundary."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


async def test_account_mfa_requirement_rejects_foreign_and_revoked_sessions(
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    user_id = uuid4()
    session_id = uuid4()
    app_engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    lookup = text("SELECT public.lookup_auth_account_mfa_requirement(:user_id, :session_id)")
    try:
        async with db_engine.begin() as connection:
            await connection.execute(text("SELECT set_config('app.support_session', 'true', true)"))
            await connection.execute(
                text(
                    "INSERT INTO public.app_user (id, email, full_name, status) "
                    "VALUES (:user_id, :email, 'MFA policy test', 'active')"
                ),
                {"user_id": user_id, "email": f"mfa-policy-{user_id}@aurum.test"},
            )
            await connection.execute(
                text(
                    "INSERT INTO public.session (id, user_id, refresh_token_hash, expires_at) "
                    "VALUES (:session_id, :user_id, :token_hash, now() + INTERVAL '1 hour')"
                ),
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "token_hash": uuid4().hex + uuid4().hex,
                },
            )

        async with app_engine.connect() as connection:
            assert (
                await connection.scalar(lookup, {"user_id": user_id, "session_id": session_id})
            ) is False

        async with app_engine.connect() as connection:
            assert (
                await connection.scalar(lookup, {"user_id": uuid4(), "session_id": session_id})
                is None
            )

        async with db_engine.begin() as connection:
            await connection.execute(text("SELECT set_config('app.support_session', 'true', true)"))
            await connection.execute(
                text("UPDATE public.session SET revoked_at = now() WHERE id = :session_id"),
                {"session_id": session_id},
            )

        async with app_engine.connect() as connection:
            assert (
                await connection.scalar(lookup, {"user_id": user_id, "session_id": session_id})
                is None
            )
    finally:
        await app_engine.dispose()
        async with maintenance_engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM public.session WHERE id = :session_id"),
                {"session_id": session_id},
            )
            await connection.execute(
                text("DELETE FROM public.app_user WHERE id = :user_id"),
                {"user_id": user_id},
            )
