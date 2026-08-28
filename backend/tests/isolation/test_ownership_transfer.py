"""Database contract for the protected ownership transfer workflow."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.time import utc_now


async def _set_actor_context(
    connection: AsyncConnection,
    *,
    user_id: UUID,
    tenant_id: UUID,
    mfa_verified_at: int | None = None,
) -> None:
    await connection.execute(
        text("SELECT set_config('app.user_id', :value, true)"),
        {"value": str(user_id)},
    )
    await connection.execute(
        text("SELECT set_config('app.tenant_id', :value, true)"),
        {"value": str(tenant_id)},
    )
    await connection.execute(
        text("SELECT set_config('app.mfa_verified_at', :value, true)"),
        {
            "value": str(
                mfa_verified_at if mfa_verified_at is not None else int(utc_now().timestamp())
            )
        },
    )


async def test_ownership_transfer_is_atomic_and_revokes_sessions(  # noqa: PLR0915
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    """Keep the transaction-sensitive workflow in one end-to-end DB scenario."""
    tenant_id = uuid4()
    owner_user_id = uuid4()
    owner_membership_id = uuid4()
    owner_ownership_id = uuid4()
    target_user_id = uuid4()
    target_membership_id = uuid4()
    outsider_user_id = uuid4()
    outsider_membership_id = uuid4()
    owner_role_id = uuid4()
    employee_role_id = uuid4()
    owner_session_id = uuid4()
    target_session_id = uuid4()
    request_id = uuid4()
    now = utc_now()

    async with db_engine.begin() as connection:
        await connection.execute(text("SELECT set_config('app.support_session', 'true', true)"))
        await connection.execute(
            text("""
                INSERT INTO public.tenant (id, name, contact_email, status)
                VALUES (:tenant_id, :name, :email, 'active')
                """),
            {
                "tenant_id": tenant_id,
                "name": f"Ownership transfer {tenant_id}",
                "email": f"transfer-{tenant_id}@aurum.test",
            },
        )
        await connection.execute(
            text("""
                INSERT INTO public.app_user (
                  id, email, full_name, home_tenant_id, status, activated_at
                ) VALUES
                  (:owner_user_id, :owner_email, 'Current owner', :tenant_id, 'active', :now),
                  (:target_user_id, :target_email, 'Future owner', :tenant_id, 'invited', NULL),
                  (:outsider_user_id, :outsider_email, 'Other employee', :tenant_id, 'active', :now)
                """),
            {
                "tenant_id": tenant_id,
                "owner_user_id": owner_user_id,
                "owner_email": f"owner-{tenant_id}@aurum.test",
                "target_user_id": target_user_id,
                "target_email": f"target-{tenant_id}@aurum.test",
                "outsider_user_id": outsider_user_id,
                "outsider_email": f"outsider-{tenant_id}@aurum.test",
                "now": now,
            },
        )
        await connection.execute(
            text("""
                INSERT INTO public.tenant_membership (
                  id, tenant_id, user_id, full_name, status, activated_at
                ) VALUES
                  (:owner_membership_id, :tenant_id, :owner_user_id,
                   'Current owner', 'active', :now),
                  (:target_membership_id, :tenant_id, :target_user_id,
                   'Future owner', 'active', :now),
                  (:outsider_membership_id, :tenant_id, :outsider_user_id,
                   'Other employee', 'active', :now)
                """),
            {
                "tenant_id": tenant_id,
                "owner_membership_id": owner_membership_id,
                "owner_user_id": owner_user_id,
                "target_membership_id": target_membership_id,
                "target_user_id": target_user_id,
                "outsider_membership_id": outsider_membership_id,
                "outsider_user_id": outsider_user_id,
                "now": now,
            },
        )
        await connection.execute(
            text("""
                INSERT INTO public.role (
                  id, tenant_id, name, level, is_system, is_active,
                  is_protected, protected_kind
                ) VALUES
                  (:owner_role_id, :tenant_id, 'Protected owner', 3, false, true,
                   true, 'tenant_owner'),
                  (:employee_role_id, :tenant_id, 'Employee', 4, false, true,
                   false, NULL)
                """),
            {
                "tenant_id": tenant_id,
                "owner_role_id": owner_role_id,
                "employee_role_id": employee_role_id,
            },
        )
        await connection.execute(
            text("""
                INSERT INTO public.tenant_ownership (
                  id, tenant_id, membership_id, is_active, granted_at
                ) VALUES (
                  :ownership_id, :tenant_id, :membership_id, true, :now
                )
                """),
            {
                "ownership_id": owner_ownership_id,
                "tenant_id": tenant_id,
                "membership_id": owner_membership_id,
                "now": now,
            },
        )
    async with maintenance_engine.begin() as connection:
        await connection.execute(
            text("""
                INSERT INTO public.access_role_version (
                  id, role_id, tenant_id, version, name, description, status,
                  creation_xid, published_at, created_by
                )
                SELECT
                  gen_random_uuid(), role.id, role.tenant_id, role.version,
                  role.name, role.description, 'published', txid_current(),
                  statement_timestamp(), NULL
                FROM public.role AS role
                WHERE role.id IN (:owner_role_id, :employee_role_id)
                """),
            {
                "owner_role_id": owner_role_id,
                "employee_role_id": employee_role_id,
            },
        )

    async with db_engine.begin() as connection:
        await connection.execute(text("SELECT set_config('app.support_session', 'true', true)"))
        await connection.execute(
            text("""
                INSERT INTO public.user_assignment (
                  user_id, tenant_id, membership_id, branch_id, role_id, is_active
                ) VALUES
                  (:owner_user_id, :tenant_id, :owner_membership_id, NULL,
                   :owner_role_id, true),
                  (:target_user_id, :tenant_id, :target_membership_id, NULL,
                   :employee_role_id, true)
                """),
            {
                "tenant_id": tenant_id,
                "owner_user_id": owner_user_id,
                "owner_membership_id": owner_membership_id,
                "owner_role_id": owner_role_id,
                "target_user_id": target_user_id,
                "target_membership_id": target_membership_id,
                "employee_role_id": employee_role_id,
            },
        )
        await connection.execute(
            text("""
                INSERT INTO public.support_mfa (
                  user_id, active_secret_ciphertext, active_key_version,
                  status, active_generation, confirmed_at
                ) VALUES (
                  :target_user_id, :ciphertext, 1, 'active', 1, :now
                )
                """),
            {"target_user_id": target_user_id, "ciphertext": b"test", "now": now},
        )
        await connection.execute(
            text("""
                INSERT INTO public.session (
                  id, user_id, refresh_token_hash, expires_at, mfa_verified_at
                ) VALUES
                  (:owner_session_id, :owner_user_id, :owner_hash, :expires_at, :now),
                  (:target_session_id, :target_user_id, :target_hash, :expires_at, :now)
                """),
            {
                "owner_session_id": owner_session_id,
                "owner_user_id": owner_user_id,
                "owner_hash": uuid4().hex + uuid4().hex,
                "target_session_id": target_session_id,
                "target_user_id": target_user_id,
                "target_hash": uuid4().hex + uuid4().hex,
                "expires_at": now + timedelta(days=1),
                "now": now,
            },
        )

    app_engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    try:
        async with app_engine.begin() as connection:
            await _set_actor_context(
                connection,
                user_id=owner_user_id,
                tenant_id=tenant_id,
            )
            first = await connection.scalar(
                text("""
                    SELECT public.create_tenant_ownership_transfer(
                      :request_id, :target_membership_id, :expires_at
                    )
                    """),
                {
                    "request_id": request_id,
                    "target_membership_id": target_membership_id,
                    "expires_at": now + timedelta(days=3),
                },
            )
            repeated = await connection.scalar(
                text("""
                    SELECT public.create_tenant_ownership_transfer(
                      :request_id, :target_membership_id, :expires_at
                    )
                    """),
                {
                    "request_id": request_id,
                    "target_membership_id": target_membership_id,
                    "expires_at": now + timedelta(days=3),
                },
            )
            assert first == request_id
            assert repeated == request_id

        with pytest.raises(DBAPIError, match="pending"):
            async with app_engine.begin() as connection:
                await _set_actor_context(
                    connection,
                    user_id=owner_user_id,
                    tenant_id=tenant_id,
                )
                await connection.scalar(
                    text("""
                        SELECT public.create_tenant_ownership_transfer(
                          :request_id, :target_membership_id, :expires_at
                        )
                        """),
                    {
                        "request_id": uuid4(),
                        "target_membership_id": target_membership_id,
                        "expires_at": now + timedelta(days=3),
                    },
                )

        async with maintenance_engine.connect() as connection:
            assert await connection.scalar(
                text("SELECT public.auth_account_requires_mfa(:user_id)"),
                {"user_id": target_user_id},
            )

        async with app_engine.begin() as connection:
            await _set_actor_context(
                connection,
                user_id=outsider_user_id,
                tenant_id=tenant_id,
            )
            assert (
                await connection.scalar(
                    text("SELECT count(*) FROM public.tenant_ownership_transfer")
                )
                == 0
            )

        with pytest.raises(DBAPIError, match="target is unavailable"):
            async with app_engine.begin() as connection:
                await _set_actor_context(
                    connection,
                    user_id=outsider_user_id,
                    tenant_id=tenant_id,
                )
                await connection.scalar(
                    text("SELECT public.accept_tenant_ownership_transfer(:request_id)"),
                    {"request_id": request_id},
                )

        with pytest.raises(DBAPIError, match="acceptance is invalid"):
            async with app_engine.begin() as connection:
                await _set_actor_context(
                    connection,
                    user_id=target_user_id,
                    tenant_id=tenant_id,
                    mfa_verified_at=int((utc_now() - timedelta(hours=1)).timestamp()),
                )
                await connection.scalar(
                    text("SELECT public.accept_tenant_ownership_transfer(:request_id)"),
                    {"request_id": request_id},
                )

        first_connection = await app_engine.connect()
        second_connection = await app_engine.connect()
        first_transaction = await first_connection.begin()
        second_transaction = await second_connection.begin()
        second_accept: asyncio.Task[object] | None = None
        try:
            await _set_actor_context(
                first_connection,
                user_id=target_user_id,
                tenant_id=tenant_id,
            )
            await _set_actor_context(
                second_connection,
                user_id=target_user_id,
                tenant_id=tenant_id,
            )
            assert (
                await first_connection.scalar(
                    text("SELECT count(*) FROM public.tenant_ownership_transfer")
                )
                == 1
            )
            completed = await first_connection.scalar(
                text("SELECT public.accept_tenant_ownership_transfer(:request_id)"),
                {"request_id": request_id},
            )
            assert completed == request_id
            second_accept = asyncio.create_task(
                second_connection.scalar(
                    text("SELECT public.accept_tenant_ownership_transfer(:request_id)"),
                    {"request_id": request_id},
                )
            )
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(second_accept), timeout=0.2)
            await first_transaction.commit()
            with pytest.raises(DBAPIError, match="request is unavailable"):
                await second_accept
            await second_transaction.rollback()
        finally:
            if first_transaction.is_active:
                await first_transaction.rollback()
            if second_transaction.is_active:
                await second_transaction.rollback()
            if second_accept is not None and not second_accept.done():
                second_accept.cancel()
                await asyncio.gather(second_accept, return_exceptions=True)
            await first_connection.close()
            await second_connection.close()
    finally:
        await app_engine.dispose()

    async with db_engine.connect() as connection:
        active_owner = (
            await connection.execute(
                text("""
                    SELECT membership.user_id
                    FROM public.tenant_ownership AS ownership
                    JOIN public.tenant_membership AS membership
                      ON membership.id = ownership.membership_id
                    WHERE ownership.tenant_id = :tenant_id
                      AND ownership.is_active
                    """),
                {"tenant_id": tenant_id},
            )
        ).scalar_one()
        owner_assignments = (
            (
                await connection.execute(
                    text("""
                    SELECT assignment.user_id
                    FROM public.user_assignment AS assignment
                    JOIN public.role AS role ON role.id = assignment.role_id
                    WHERE assignment.tenant_id = :tenant_id
                      AND assignment.is_active
                      AND role.protected_kind = 'tenant_owner'
                    """),
                    {"tenant_id": tenant_id},
                )
            )
            .scalars()
            .all()
        )
        transfer_status = await connection.scalar(
            text("SELECT status FROM public.tenant_ownership_transfer WHERE id = :request_id"),
            {"request_id": request_id},
        )
        revoked_sessions = await connection.scalar(
            text("""
                SELECT count(*)
                FROM public.session
                WHERE id IN (:owner_session_id, :target_session_id)
                  AND revoked_at IS NOT NULL
                  AND revoked_reason = 'ownership_transferred'
                """),
            {
                "owner_session_id": owner_session_id,
                "target_session_id": target_session_id,
            },
        )
        assert active_owner == target_user_id
        assert owner_assignments == [target_user_id]
        assert transfer_status == "completed"
        assert revoked_sessions == 2

    async with maintenance_engine.begin() as connection:
        await connection.execute(
            text("DELETE FROM public.tenant_ownership_transfer WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        await connection.execute(
            text("DELETE FROM public.user_assignment WHERE tenant_id = :tenant_id"),
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
            text("DELETE FROM public.tenant WHERE id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        await connection.execute(
            text("DELETE FROM public.audit_log WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        await connection.execute(
            text(
                "DELETE FROM public.app_user " "WHERE id IN (:owner_id, :target_id, :outsider_id)"
            ),
            {
                "owner_id": owner_user_id,
                "target_id": target_user_id,
                "outsider_id": outsider_user_id,
            },
        )
