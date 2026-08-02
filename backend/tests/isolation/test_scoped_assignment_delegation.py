"""Database enforcement for protected owner assignment scopes."""

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


async def test_aurum_app_cannot_assign_generic_role_to_active_owner(
    db_engine: AsyncEngine,
    app_engine_scoped_assignment: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    tenant_id = uuid4()
    actor_id = uuid4()
    target_id = uuid4()
    actor_membership_id = uuid4()
    target_membership_id = uuid4()
    owner_role_id = uuid4()
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
                "name": f"Protected owner assignment {tenant_id}",
                "email": f"protected-owner-{tenant_id}@aurum.test",
            },
        )
        await support.execute(
            text("""
                INSERT INTO public.app_user (
                  id, email, full_name, home_tenant_id, status, activated_at
                )
                VALUES
                  (:actor_id, :actor_email, 'Owner actor', :tenant_id, 'active', now()),
                  (:target_id, :target_email, 'Owner target', :tenant_id, 'active', now())
                """),
            {
                "tenant_id": tenant_id,
                "actor_id": actor_id,
                "target_id": target_id,
                "actor_email": f"owner-actor-{tenant_id}@aurum.test",
                "target_email": f"owner-target-{tenant_id}@aurum.test",
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
                    'Owner actor',
                    'active',
                    now()
                  ),
                  (
                    :target_membership_id,
                    :tenant_id,
                    :target_id,
                    'Owner target',
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
                INSERT INTO public.role (
                  id,
                  tenant_id,
                  name,
                  level,
                  is_system,
                  is_active,
                  is_protected,
                  protected_kind
                )
                VALUES
                  (
                    :owner_role_id,
                    :tenant_id,
                    'Protected owner',
                    3,
                    false,
                    true,
                    true,
                    'tenant_owner'
                  ),
                  (
                    :delegated_role_id,
                    :tenant_id,
                    'Delegated cashier',
                    4,
                    false,
                    true,
                    false,
                    NULL
                  )
                """),
            {
                "tenant_id": tenant_id,
                "owner_role_id": owner_role_id,
                "delegated_role_id": delegated_role_id,
            },
        )
        await support.execute(
            text("""
                INSERT INTO public.role_permission (role_id, permission_code)
                SELECT :owner_role_id, template_permission.permission_code
                FROM public.role_template AS template
                JOIN public.role_template_permission AS template_permission
                  ON template_permission.template_id = template.id
                WHERE template.slug = 'owner'
                  AND template.is_active
                """),
            {"owner_role_id": owner_role_id},
        )
        await support.execute(
            text("""
                INSERT INTO public.role_permission (role_id, permission_code)
                VALUES (:delegated_role_id, 'pos.sell')
                """),
            {"delegated_role_id": delegated_role_id},
        )
        await support.execute(
            text("""
                INSERT INTO public.tenant_ownership (
                  tenant_id, membership_id, is_active
                )
                VALUES
                  (:tenant_id, :actor_membership_id, true),
                  (:tenant_id, :target_membership_id, true)
                """),
            {
                "tenant_id": tenant_id,
                "actor_membership_id": actor_membership_id,
                "target_membership_id": target_membership_id,
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
                    :owner_role_id,
                    true
                  ),
                  (
                    :target_id,
                    :tenant_id,
                    :target_membership_id,
                    NULL,
                    :owner_role_id,
                    true
                  )
                """),
            {
                "tenant_id": tenant_id,
                "actor_id": actor_id,
                "target_id": target_id,
                "actor_membership_id": actor_membership_id,
                "target_membership_id": target_membership_id,
                "owner_role_id": owner_role_id,
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
            with pytest.raises(DBAPIError, match="protected ownership workflow"):
                await app_connection.execute(
                    text("""
                        SELECT public.create_tenant_user_assignment(
                          :tenant_id,
                          :target_id,
                          NULL,
                          :delegated_role_id,
                          false
                        )
                        """),
                    {
                        "tenant_id": tenant_id,
                        "target_id": target_id,
                        "delegated_role_id": delegated_role_id,
                    },
                )
        finally:
            await transaction.rollback()

    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session', 'true', true)"))
        target_roles = list(
            (
                await support.execute(
                    text("""
                        SELECT role_id
                        FROM public.user_assignment
                        WHERE tenant_id = :tenant_id
                          AND user_id = :target_id
                          AND is_active
                        """),
                    {"tenant_id": tenant_id, "target_id": target_id},
                )
            ).scalars()
        )
        assert target_roles == [owner_role_id]
        await support.execute(
            text("DELETE FROM public.user_assignment WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        await support.execute(
            text("""
                DELETE FROM public.role_permission
                WHERE role_id IN (:owner_role_id, :delegated_role_id)
                """),
            {
                "owner_role_id": owner_role_id,
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
            text("DELETE FROM public.tenant WHERE id = :tenant_id"),
            {"tenant_id": tenant_id},
        )

    async with maintenance_engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM public.audit_log WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )

    async with db_engine.begin() as support:
        await support.execute(
            text("DELETE FROM public.app_user WHERE id IN (:actor_id, :target_id)"),
            {"actor_id": actor_id, "target_id": target_id},
        )
