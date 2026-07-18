"""Database enforcement for capability-specific assignment scopes."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings


@pytest_asyncio.fixture
async def app_engine_scoped_assignment() -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        get_settings().DATABASE_URL_APP,
        poolclass=NullPool,
    )
    try:
        yield engine
    finally:
        await engine.dispose()


async def _set_actor_context(
    connection: AsyncConnection,
    *,
    tenant_id: UUID,
    user_id: UUID,
) -> None:
    await connection.execute(
        text("SELECT set_config('app.tenant_id', :value, true)"),
        {"value": str(tenant_id)},
    )
    await connection.execute(
        text("SELECT set_config('app.user_id', :value, true)"),
        {"value": str(user_id)},
    )


async def test_aurum_app_cannot_mix_role_capability_and_assignment_branch(
    db_engine: AsyncEngine,
    app_engine_scoped_assignment: AsyncEngine,
) -> None:
    tenant_id = uuid4()
    actor_id = uuid4()
    target_id = uuid4()
    actor_membership_id = uuid4()
    target_membership_id = uuid4()
    branch_a_id = uuid4()
    branch_b_id = uuid4()
    assign_role_id = uuid4()
    sell_role_id = uuid4()
    delegated_role_id = uuid4()

    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session', 'true', true)"))
        await support.execute(
            text("""
                INSERT INTO public.tenant (id, name, contact_email, status)
                VALUES (:tenant_id, :name, :email, 'active')
                """),
            {
                "tenant_id": tenant_id,
                "name": f"Scoped assignment {tenant_id}",
                "email": f"scoped-{tenant_id}@aurum.test",
            },
        )
        await support.execute(
            text("""
                INSERT INTO public.app_user (
                  id, email, full_name, home_tenant_id, status, activated_at
                )
                VALUES
                  (:actor_id, :actor_email, 'Scoped owner', :tenant_id, 'active', now()),
                  (:target_id, :target_email, 'Scoped target', :tenant_id, 'active', now())
                """),
            {
                "tenant_id": tenant_id,
                "actor_id": actor_id,
                "target_id": target_id,
                "actor_email": f"actor-{tenant_id}@aurum.test",
                "target_email": f"target-{tenant_id}@aurum.test",
            },
        )
        await support.execute(
            text("""
                INSERT INTO public.tenant_membership (
                  id, tenant_id, user_id, full_name, status, activated_at
                )
                VALUES
                  (
                    :actor_membership_id,
                    :tenant_id,
                    :actor_id,
                    'Scoped owner',
                    'active',
                    now()
                  ),
                  (
                    :target_membership_id,
                    :tenant_id,
                    :target_id,
                    'Scoped target',
                    'active',
                    now()
                  )
                """),
            {
                "tenant_id": tenant_id,
                "actor_id": actor_id,
                "target_id": target_id,
                "actor_membership_id": actor_membership_id,
                "target_membership_id": target_membership_id,
            },
        )
        await support.execute(
            text("""
                INSERT INTO public.tenant_ownership (
                  tenant_id, membership_id, is_active
                )
                VALUES (:tenant_id, :membership_id, true)
                """),
            {
                "tenant_id": tenant_id,
                "membership_id": actor_membership_id,
            },
        )
        await support.execute(
            text("""
                INSERT INTO public.branch (id, tenant_id, name)
                VALUES
                  (:branch_a_id, :tenant_id, 'Scope A'),
                  (:branch_b_id, :tenant_id, 'Scope B')
                """),
            {
                "tenant_id": tenant_id,
                "branch_a_id": branch_a_id,
                "branch_b_id": branch_b_id,
            },
        )
        await support.execute(
            text("""
                INSERT INTO public.role (
                  id, tenant_id, name, level, is_system, is_active
                )
                VALUES
                  (
                    :assign_role_id,
                    :tenant_id,
                    'Scoped assign',
                    3,
                    false,
                    true
                  ),
                  (
                    :sell_role_id,
                    :tenant_id,
                    'Scoped sell',
                    4,
                    false,
                    true
                  ),
                  (
                    :delegated_role_id,
                    :tenant_id,
                    'Delegated sell',
                    4,
                    false,
                    true
                  )
                """),
            {
                "tenant_id": tenant_id,
                "assign_role_id": assign_role_id,
                "sell_role_id": sell_role_id,
                "delegated_role_id": delegated_role_id,
            },
        )
        await support.execute(
            text("""
                INSERT INTO public.role_permission (role_id, permission_code)
                VALUES
                  (:assign_role_id, 'roles.assign'),
                  (:sell_role_id, 'pos.sell'),
                  (:delegated_role_id, 'pos.sell')
                """),
            {
                "assign_role_id": assign_role_id,
                "sell_role_id": sell_role_id,
                "delegated_role_id": delegated_role_id,
            },
        )
        await support.execute(
            text("""
                INSERT INTO public.user_assignment (
                  user_id,
                  tenant_id,
                  membership_id,
                  branch_id,
                  role_id,
                  is_active
                )
                VALUES
                  (
                    :actor_id,
                    :tenant_id,
                    :actor_membership_id,
                    NULL,
                    :assign_role_id,
                    true
                  ),
                  (
                    :actor_id,
                    :tenant_id,
                    :actor_membership_id,
                    :branch_a_id,
                    :sell_role_id,
                    true
                  )
                """),
            {
                "actor_id": actor_id,
                "tenant_id": tenant_id,
                "actor_membership_id": actor_membership_id,
                "branch_a_id": branch_a_id,
                "branch_b_id": branch_b_id,
                "assign_role_id": assign_role_id,
                "sell_role_id": sell_role_id,
            },
        )

    async with app_engine_scoped_assignment.connect() as app_connection:
        transaction = await app_connection.begin()
        try:
            await _set_actor_context(
                app_connection,
                tenant_id=tenant_id,
                user_id=actor_id,
            )
            can_assign_b = await app_connection.scalar(
                text("""
                    SELECT public.tenant_actor_has_scoped_permission(
                      :tenant_id,
                      'roles.assign',
                      :branch_id
                    )
                    """),
                {"tenant_id": tenant_id, "branch_id": branch_b_id},
            )
            can_sell_a = await app_connection.scalar(
                text("""
                    SELECT public.tenant_actor_has_scoped_permission(
                      :tenant_id,
                      'pos.sell',
                      :branch_id
                    )
                    """),
                {"tenant_id": tenant_id, "branch_id": branch_a_id},
            )
            can_sell_b = await app_connection.scalar(
                text("""
                    SELECT public.tenant_actor_has_scoped_permission(
                      :tenant_id,
                      'pos.sell',
                      :branch_id
                    )
                    """),
                {"tenant_id": tenant_id, "branch_id": branch_b_id},
            )
            assert can_assign_b is True
            assert can_sell_a is True
            assert can_sell_b is False

            with pytest.raises(
                DBAPIError,
                match="Assignment creation is outside actor delegation scope",
            ):
                await app_connection.execute(
                    text("""
                        SELECT public.create_tenant_user_assignment(
                          :tenant_id,
                          :target_id,
                          :branch_b_id,
                          :delegated_role_id,
                          false
                        )
                        """),
                    {
                        "target_id": target_id,
                        "tenant_id": tenant_id,
                        "branch_b_id": branch_b_id,
                        "delegated_role_id": delegated_role_id,
                    },
                )
        finally:
            await transaction.rollback()

    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session', 'true', true)"))
        count = await support.scalar(
            text("""
                SELECT pg_catalog.count(*)
                FROM public.user_assignment
                WHERE tenant_id = :tenant_id
                  AND user_id = :target_id
                """),
            {"tenant_id": tenant_id, "target_id": target_id},
        )
        assert count == 0
        await support.execute(
            text("DELETE FROM public.user_assignment WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        await support.execute(
            text("""
                DELETE FROM public.role_permission
                WHERE role_id IN (:assign_role_id, :sell_role_id, :delegated_role_id)
                """),
            {
                "assign_role_id": assign_role_id,
                "sell_role_id": sell_role_id,
                "delegated_role_id": delegated_role_id,
            },
        )
        await support.execute(
            text("DELETE FROM public.role WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        await support.execute(
            text("DELETE FROM public.sync_writer_epoch WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        await support.execute(
            text("DELETE FROM public.branch WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        await support.execute(
            text("DELETE FROM public.audit_log WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        await support.execute(
            text("DELETE FROM public.tenant WHERE id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        await support.execute(
            text("DELETE FROM public.app_user WHERE id IN (:actor_id, :target_id)"),
            {"actor_id": actor_id, "target_id": target_id},
        )
