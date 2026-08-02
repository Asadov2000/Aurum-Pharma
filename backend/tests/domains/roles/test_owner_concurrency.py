"""Concurrent last-owner damage is serialized on the tenant row."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker


@pytest.mark.parametrize("second_operation", ["revoke", "suspend"])
async def test_concurrent_last_owner_damage_is_serialized(
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    second_operation: str,
) -> None:
    tenant_id = uuid4()
    user_a_id = uuid4()
    user_b_id = uuid4()
    membership_a_id = uuid4()
    membership_b_id = uuid4()
    ownership_a_id = uuid4()
    ownership_b_id = uuid4()

    async with db_engine.begin() as connection:
        await connection.execute(
            text("""
                INSERT INTO public.tenant (id, name, contact_email, status)
                VALUES (:tenant_id, :name, :email, 'active')
                """),
            {
                "tenant_id": tenant_id,
                "name": f"Concurrent owners {tenant_id}",
                "email": f"owners-{tenant_id}@aurum.test",
            },
        )
        await connection.execute(
            text("""
                INSERT INTO public.app_user (
                  id, email, full_name, home_tenant_id, status, activated_at
                )
                VALUES
                  (:user_a_id, :email_a, 'Owner A', :tenant_id, 'active', now()),
                  (:user_b_id, :email_b, 'Owner B', :tenant_id, 'active', now())
                """),
            {
                "tenant_id": tenant_id,
                "user_a_id": user_a_id,
                "user_b_id": user_b_id,
                "email_a": f"owner-a-{tenant_id}@aurum.test",
                "email_b": f"owner-b-{tenant_id}@aurum.test",
            },
        )
        await connection.execute(
            text("""
                INSERT INTO public.tenant_membership (
                  id, tenant_id, user_id, full_name, status, activated_at
                )
                VALUES
                  (:membership_a_id, :tenant_id, :user_a_id, 'Owner A', 'active', now()),
                  (:membership_b_id, :tenant_id, :user_b_id, 'Owner B', 'active', now())
                """),
            {
                "tenant_id": tenant_id,
                "membership_a_id": membership_a_id,
                "membership_b_id": membership_b_id,
                "user_a_id": user_a_id,
                "user_b_id": user_b_id,
            },
        )
        await connection.execute(
            text("""
                INSERT INTO public.tenant_ownership (
                  id, tenant_id, membership_id, is_active
                )
                VALUES
                  (:ownership_a_id, :tenant_id, :membership_a_id, true),
                  (:ownership_b_id, :tenant_id, :membership_b_id, true)
                """),
            {
                "tenant_id": tenant_id,
                "membership_a_id": membership_a_id,
                "membership_b_id": membership_b_id,
                "ownership_a_id": ownership_a_id,
                "ownership_b_id": ownership_b_id,
            },
        )

    sessions = async_sessionmaker(db_engine, expire_on_commit=False)
    first = sessions()
    second = sessions()
    first_tx = await first.begin()
    second_tx = await second.begin()
    second_task: asyncio.Task[object] | None = None
    try:
        await first.execute(
            text("""
                UPDATE public.tenant_ownership
                SET is_active = false, revoked_at = now()
                WHERE id = :ownership_id
                """),
            {"ownership_id": ownership_a_id},
        )
        if second_operation == "revoke":
            statement = text("""
                UPDATE public.tenant_ownership
                SET is_active = false, revoked_at = now()
                WHERE id = :target_id
                """)
            target_id = ownership_b_id
        else:
            statement = text("""
                UPDATE public.tenant_membership
                SET status = 'suspended', suspended_at = now()
                WHERE id = :target_id
                """)
            target_id = membership_b_id

        second_task = asyncio.create_task(second.execute(statement, {"target_id": target_id}))
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(second_task), timeout=0.2)

        await first_tx.commit()
        with pytest.raises(IntegrityError, match="last active owner"):
            await second_task
        await second_tx.rollback()
    finally:
        if first_tx.is_active:
            await first_tx.rollback()
        if second_tx.is_active:
            await second_tx.rollback()
        if second_task is not None and not second_task.done():
            second_task.cancel()
            await asyncio.gather(second_task, return_exceptions=True)
        await first.close()
        await second.close()

        async with db_engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM public.tenant WHERE id = :tenant_id"),
                {"tenant_id": tenant_id},
            )

        async with maintenance_engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM public.audit_log WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )

        async with db_engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM public.app_user " "WHERE id IN (:user_a_id, :user_b_id)"),
                {"user_a_id": user_a_id, "user_b_id": user_b_id},
            )


async def test_assignment_and_ownership_activation_are_serialized(
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    tenant_id = uuid4()
    user_id = uuid4()
    membership_id = uuid4()
    role_id = uuid4()

    async with db_engine.begin() as connection:
        await connection.execute(text("SELECT set_config('app.support_session', 'true', true)"))
        await connection.execute(
            text("""
                INSERT INTO public.tenant (id, name, contact_email, status)
                VALUES (:tenant_id, :name, :email, 'active')
                """),
            {
                "tenant_id": tenant_id,
                "name": f"Concurrent ownership {tenant_id}",
                "email": f"ownership-{tenant_id}@aurum.test",
            },
        )
        await connection.execute(
            text("""
                INSERT INTO public.app_user (
                  id, email, full_name, home_tenant_id, status, activated_at
                ) VALUES (
                  :user_id, :email, 'Future owner', :tenant_id, 'active', now()
                )
                """),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "email": f"future-owner-{tenant_id}@aurum.test",
            },
        )
        await connection.execute(
            text("""
                INSERT INTO public.tenant_membership (
                  id, tenant_id, user_id, full_name, status, activated_at
                ) VALUES (
                  :membership_id, :tenant_id, :user_id, 'Future owner', 'active', now()
                )
                """),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "membership_id": membership_id,
            },
        )
        await connection.execute(
            text("""
                INSERT INTO public.role (
                  id, tenant_id, name, level, is_system, is_active
                ) VALUES (
                  :role_id, :tenant_id, 'Regular role', 4, false, true
                )
                """),
            {"tenant_id": tenant_id, "role_id": role_id},
        )

    sessions = async_sessionmaker(db_engine, expire_on_commit=False)
    assignment_session = sessions()
    ownership_session = sessions()
    assignment_tx = await assignment_session.begin()
    ownership_tx = await ownership_session.begin()
    ownership_task: asyncio.Task[object] | None = None
    try:
        await assignment_session.execute(
            text("SELECT set_config('app.support_session', 'true', true)")
        )
        await ownership_session.execute(
            text("SELECT set_config('app.support_session', 'true', true)")
        )
        await assignment_session.execute(
            text("""
                INSERT INTO public.user_assignment (
                  user_id, tenant_id, membership_id, branch_id, role_id, is_active
                ) VALUES (
                  :user_id, :tenant_id, :membership_id, NULL, :role_id, true
                )
                """),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "membership_id": membership_id,
                "role_id": role_id,
            },
        )
        ownership_task = asyncio.create_task(
            ownership_session.execute(
                text("""
                    INSERT INTO public.tenant_ownership (
                      tenant_id, membership_id, is_active
                    ) VALUES (:tenant_id, :membership_id, true)
                    """),
                {"tenant_id": tenant_id, "membership_id": membership_id},
            )
        )
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(ownership_task), timeout=0.2)

        await assignment_tx.commit()
        with pytest.raises(DBAPIError, match="protected owner assignments only"):
            await ownership_task
        await ownership_tx.rollback()
    finally:
        if assignment_tx.is_active:
            await assignment_tx.rollback()
        if ownership_tx.is_active:
            await ownership_tx.rollback()
        if ownership_task is not None and not ownership_task.done():
            ownership_task.cancel()
            await asyncio.gather(ownership_task, return_exceptions=True)
        await assignment_session.close()
        await ownership_session.close()

        async with db_engine.begin() as connection:
            await connection.execute(text("SELECT set_config('app.support_session', 'true', true)"))
            await connection.execute(
                text("DELETE FROM public.tenant WHERE id = :tenant_id"),
                {"tenant_id": tenant_id},
            )

        async with maintenance_engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM public.audit_log WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )

        async with db_engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM public.app_user WHERE id = :user_id"),
                {"user_id": user_id},
            )
