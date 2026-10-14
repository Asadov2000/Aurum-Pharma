"""Construct pre-0199 closed-shift history only in disposable test databases."""

from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.pos.test_unit_return import runtime


async def close_historical_unit_shift(
    session: AsyncSession, maintenance_engine: AsyncEngine, shift_id: UUID
) -> None:
    """Retain defensive reads/preflight tests for history predating the close guard."""
    context = runtime(session)
    function = f"test_close_unit_shift_{uuid4().hex}"
    await session.commit()
    async with maintenance_engine.begin() as connection:
        assert str(await connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert await connection.scalar(text("SELECT session_user")) == "aurum_migrator"
        assert await connection.scalar(text("SELECT current_user")) == "aurum_schema_owner"
        await connection.execute(
            text(
                f"""
                CREATE FUNCTION public.{function}(p_tenant UUID,p_shift UUID,p_actor UUID)
                RETURNS VOID LANGUAGE plpgsql SECURITY DEFINER
                SET search_path=pg_catalog,pg_temp AS $$
                DECLARE changed BIGINT;
                BEGIN
                  IF right(current_database(),5) <> '_test'
                    OR SESSION_USER <> 'aurum_app'
                    OR public.current_tenant_id() IS DISTINCT FROM p_tenant
                    OR public.current_app_user_id() IS DISTINCT FROM p_actor THEN
                    RAISE EXCEPTION 'Historical fixture scope denied' USING ERRCODE='42501';
                  END IF;
                  ALTER TABLE public.shift DISABLE TRIGGER shift_unfinished_unit_cart_guard;
                  UPDATE public.shift SET status='closed',closed_at=now()
                    WHERE tenant_id=p_tenant AND id=p_shift AND status='open'
                      AND opened_by_user_id=p_actor;
                  GET DIAGNOSTICS changed = ROW_COUNT;
                  IF changed <> 1 THEN
                    RAISE EXCEPTION 'Historical fixture shift missing' USING ERRCODE='P0002';
                  END IF;
                  ALTER TABLE public.shift ENABLE TRIGGER shift_unfinished_unit_cart_guard;
                END $$
                """
            )
        )
        await connection.execute(
            text(f"REVOKE ALL ON FUNCTION public.{function}(UUID,UUID,UUID) FROM PUBLIC")
        )
        await connection.execute(
            text(f"GRANT EXECUTE ON FUNCTION public.{function}(UUID,UUID,UUID) TO aurum_app")
        )
    try:
        await set_receiving_context(session, context.stock, context.actor_id)
        # Real app login satisfies the existing writer guard; only the new guard is bypassed.
        await session.execute(
            text(f"SELECT public.{function}(:tenant,:shift,:actor)"),
            {"tenant": context.stock.tenant_id, "shift": shift_id, "actor": context.actor_id},
        )
        await session.commit()
    finally:
        # Release all app DDL locks before owner cleanup, including after a failed invocation.
        await session.rollback()
        async with maintenance_engine.begin() as connection:
            await connection.execute(text(f"DROP FUNCTION public.{function}(UUID,UUID,UUID)"))
    await set_receiving_context(session, context.stock, context.actor_id)
