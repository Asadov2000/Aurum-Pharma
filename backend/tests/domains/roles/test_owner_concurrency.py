"""Concurrent last-owner damage is serialized on the tenant row."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.domains.roles.repository import RolesRepository
from app.domains.roles.service import RolesService
from tests.role_version_helpers import set_test_recent_confirmation


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
    role_version_id = uuid4()

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

    async with maintenance_engine.begin() as connection:
        await connection.execute(
            text("""
                INSERT INTO public.access_role_version (
                  id, role_id, tenant_id, version, name, status,
                  creation_xid, published_at, created_by
                ) VALUES (
                  :version_id, :role_id, :tenant_id, 1, 'Regular role', 'published',
                  txid_current(), statement_timestamp(), :user_id
                )
                """),
            {
                "version_id": role_version_id,
                "role_id": role_id,
                "tenant_id": tenant_id,
                "user_id": user_id,
            },
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

        async with maintenance_engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM public.user_assignment WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
            await connection.execute(
                text("DELETE FROM public.access_role_version WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )
            await connection.execute(
                text("DELETE FROM public.audit_log WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            )

        async with db_engine.begin() as connection:
            await connection.execute(text("SELECT set_config('app.support_session', 'true', true)"))
            await connection.execute(
                text("DELETE FROM public.tenant WHERE id = :tenant_id"),
                {"tenant_id": tenant_id},
            )

        async with db_engine.begin() as connection:
            await connection.execute(
                text("DELETE FROM public.app_user WHERE id = :user_id"),
                {"user_id": user_id},
            )


async def _set_owner_context(
    session: AsyncSession,
    *,
    owner_id: UUID,
    tenant_id: UUID,
) -> None:
    await session.execute(
        text("SELECT set_config('app.auth_session_id', :session_id, true)"),
        {"session_id": str(owner_id)},
    )
    await session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": str(owner_id)},
    )
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )
    await session.execute(text("SELECT set_config('app.support_session', 'false', true)"))
    await session.execute(
        text("SELECT set_config('app.mfa_verified_at', :verified_at, true)"),
        {"verified_at": str(int(datetime.now(UTC).timestamp()))},
    )


async def _setup_publication_race(
    db_engine: AsyncEngine,
    tenant_id: UUID,
) -> tuple[UUID, UUID, UUID, UUID, int]:
    sessions = async_sessionmaker(db_engine, expire_on_commit=False)
    owner_id: UUID
    role_id: UUID
    role_version: int
    owner_permissions: set[str]
    async with sessions.begin() as setup:
        await setup.execute(text("SELECT set_config('app.support_session', 'true', true)"))
        actor_id = await setup.scalar(text("""
                SELECT grant_record.user_id
                FROM public.platform_access_grant AS grant_record
                JOIN public.app_user AS account ON account.id = grant_record.user_id
                WHERE grant_record.access_kind = 'developer'
                  AND grant_record.status = 'active'
                  AND account.status = 'active'
                ORDER BY grant_record.requested_at, grant_record.user_id
                LIMIT 1
                """))
        assert actor_id is not None
        provisioning_session_id = await set_test_recent_confirmation(setup, user_id=actor_id)
        await setup.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"),
            {"user_id": str(actor_id)},
        )
        await setup.execute(
            text("SELECT set_config('app.mfa_verified_at', :verified_at, true)"),
            {"verified_at": str(int(datetime.now(UTC).timestamp()))},
        )
        await setup.execute(
            text("""
                INSERT INTO public.tenant (id, name, contact_email, status)
                VALUES (:tenant_id, :name, :email, 'active')
                """),
            {
                "tenant_id": tenant_id,
                "name": f"Publication race {tenant_id}",
                "email": f"publication-race-{tenant_id}@aurum.test",
            },
        )
        repository = RolesRepository(setup)
        service = RolesService(repository)
        owner, _membership, _ownership, owner_role = await service.provision_owner(
            tenant_id=tenant_id,
            email=f"publication-owner-{tenant_id}@aurum.test",
            full_name="Publication owner",
            actor_id=actor_id,
        )
        await setup.execute(
            text(
                "UPDATE public.session SET revoked_at=now(), revoked_reason='fixture_provisioned' "
                "WHERE id=:session_id"
            ),
            {"session_id": provisioning_session_id},
        )
        await set_test_recent_confirmation(setup, user_id=owner.id, session_id=owner.id)
        await _set_owner_context(
            setup,
            owner_id=owner.id,
            tenant_id=tenant_id,
        )
        owner_permissions = set(await repository.get_role_permissions(owner_role.id))
        role, _codes = await service.create_role(
            actor_id=owner.id,
            actor_permissions=owner_permissions,
            actor_is_developer=False,
            actor_is_administrator=False,
            tenant_id=tenant_id,
            name="Параллельная роль",
            description=None,
            permission_codes=["catalog.view"],
        )
        owner_id = owner.id
        role_id = role.id
        role_version = role.version

    app_engine = create_async_engine(
        get_settings().DATABASE_URL_APP,
        poolclass=NullPool,
    )
    app_sessions = async_sessionmaker(app_engine, expire_on_commit=False)
    try:
        async with app_sessions.begin() as app_session:
            await _set_owner_context(
                app_session,
                owner_id=owner_id,
                tenant_id=tenant_id,
            )
            employee, assignment, created = await RolesService(
                RolesRepository(app_session)
            ).invite_user(
                actor_id=owner_id,
                actor_permissions=owner_permissions,
                actor_permission_scopes={code: None for code in owner_permissions},
                actor_is_developer=False,
                actor_is_administrator=False,
                tenant_id=tenant_id,
                email=f"publication-employee-{tenant_id}@aurum.test",
                full_name="Publication employee",
                phone=None,
                operation_id=uuid4(),
                role_id=role_id,
                branch_id=None,
                password_required=False,
            )
            assert created is True
            return owner_id, employee.id, role_id, assignment.id, role_version
    finally:
        await app_engine.dispose()


async def _cleanup_publication_race(
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    *,
    tenant_id: UUID,
    owner_id: UUID,
    employee_id: UUID,
) -> None:
    async with maintenance_engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM public.user_assignment WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        await connection.execute(
            text("DELETE FROM public.tenant_invitation WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        await connection.execute(
            text("""
                DELETE FROM public.access_role_version_permission
                WHERE role_version_id IN (
                  SELECT id FROM public.access_role_version
                  WHERE tenant_id = :tenant_id
                )
                """),
            {"tenant_id": tenant_id},
        )
        await connection.execute(
            text("DELETE FROM public.access_role_version WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        await connection.execute(
            text("DELETE FROM public.audit_log WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
    async with db_engine.begin() as connection:
        await connection.execute(text("SELECT set_config('app.support_session', 'true', true)"))
        await connection.execute(
            text("DELETE FROM public.tenant WHERE id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        await connection.execute(
            text("DELETE FROM public.app_user WHERE id IN (:owner_id, :employee_id)"),
            {"owner_id": owner_id, "employee_id": employee_id},
        )


async def test_concurrent_assignment_revoke_wins_role_publication(
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    tenant_id = uuid4()
    owner_id, employee_id, role_id, assignment_id, expected_version = await _setup_publication_race(
        db_engine, tenant_id
    )
    sessions = async_sessionmaker(db_engine, expire_on_commit=False)

    publish_session = sessions()
    revoke_session = sessions()
    publish_tx = await publish_session.begin()
    revoke_tx = await revoke_session.begin()
    publish_task: asyncio.Task[object] | None = None
    try:
        for session in (publish_session, revoke_session):
            await _set_owner_context(
                session,
                owner_id=owner_id,
                tenant_id=tenant_id,
            )

        revoke_repository = RolesRepository(revoke_session)
        assert await revoke_repository.lock_tenant_authorization(tenant_id)
        revoked = await revoke_repository.deactivate_assignment(
            assignment_id,
            tenant_id=tenant_id,
        )
        assert revoked == 1

        async def publish() -> object:
            repository = RolesRepository(publish_session)
            assert await repository.lock_tenant_authorization(tenant_id)
            return await repository.publish_role_version(
                role_id=role_id,
                expected_version=expected_version,
                name="Параллельная роль",
                description="Расширенная версия",
                permission_codes=["catalog.view", "pos.sell"],
            )

        publish_task = asyncio.create_task(publish())
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(publish_task), timeout=0.2)

        await revoke_tx.commit()

        await publish_task
        await publish_tx.commit()
    finally:
        if publish_tx.is_active:
            await publish_tx.rollback()
        if revoke_tx.is_active:
            await revoke_tx.rollback()
        if publish_task is not None and not publish_task.done():
            publish_task.cancel()
            await asyncio.gather(publish_task, return_exceptions=True)
        await publish_session.close()
        await revoke_session.close()

    async with maintenance_engine.begin() as connection:
        assignment_state = (
            (
                await connection.execute(
                    text("""
                    SELECT is_active, role_version_id
                    FROM public.user_assignment
                    WHERE id = :assignment_id
                    """),
                    {"assignment_id": assignment_id},
                )
            )
            .mappings()
            .one()
        )
        published_version_id = await connection.scalar(
            text("""
                SELECT id
                FROM public.access_role_version
                WHERE role_id = :role_id AND status = 'published'
                """),
            {"role_id": role_id},
        )
        assert assignment_state["is_active"] is False
        assert assignment_state["role_version_id"] != published_version_id
    await _cleanup_publication_race(
        db_engine,
        maintenance_engine,
        tenant_id=tenant_id,
        owner_id=owner_id,
        employee_id=employee_id,
    )
