"""Construct pre-0199 closed-shift history only in disposable test databases."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.pos.test_unit_return import runtime


async def close_historical_unit_shift(
    session: AsyncSession, maintenance_engine: AsyncEngine, shift_id: UUID
) -> None:
    """Retain defensive reads/preflight tests for history predating the close guard."""
    context = runtime(session)
    await session.commit()
    async with maintenance_engine.begin() as connection:
        assert str(await connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert await connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        assert await connection.scalar(text("SELECT current_user")) == "aurum_schema_owner"
        # DDL and historical update share a transaction: failure restores the guard.
        await connection.execute(
            text("ALTER TABLE public.shift DISABLE TRIGGER shift_unfinished_unit_cart_guard")
        )
        result = await connection.execute(
            text(
                "UPDATE public.shift SET status='closed',closed_at=now() "
                "WHERE tenant_id=:tenant AND id=:shift AND status='open'"
            ),
            {"tenant": context.stock.tenant_id, "shift": shift_id},
        )
        assert result.rowcount == 1
        await connection.execute(
            text("ALTER TABLE public.shift ENABLE TRIGGER shift_unfinished_unit_cart_guard")
        )
    await set_receiving_context(session, context.stock, context.actor_id)
