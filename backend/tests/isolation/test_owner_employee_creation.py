"""Database contract for owner-scoped employee account creation."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.time import utc_now


async def _set_actor(connection: AsyncConnection, *, user_id: UUID, tenant_id: UUID) -> None:
    await connection.execute(
        text("SELECT set_config('app.user_id', :value, true)"),
        {"value": str(user_id)},
    )
    await connection.execute(
        text("SELECT set_config('app.tenant_id', :value, true)"),
        {"value": str(tenant_id)},
    )


async def test_only_owner_can_create_employee_inside_current_tenant(  # noqa: PLR0915
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    tenant_id = uuid4()
    owner_id = uuid4()
    owner_membership_id = uuid4()
    manager_id = uuid4()
    manager_membership_id = uuid4()
    existing_id = uuid4()
    owner_role_id = uuid4()
    manager_role_id = uuid4()
    operation_id = uuid4()
    employee_email = f"employee-{operation_id}@aurum.test"
    existing_email = f"existing-{operation_id}@aurum.test"
    employee_id: UUID | None = None
    now = utc_now()

    async with db_engine.begin() as connection:
        await connection.execute(text("SELECT set_config('app.support_session', 'true', true)"))
        await connection.execute(
            text("""
                INSERT INTO public.tenant (id, name, contact_email, status)
                VALUES (:tenant_id, :name, :contact_email, 'active')
                """),
            {
                "tenant_id": tenant_id,
                "name": f"Owner employee test {operation_id}",
                "contact_email": f"tenant-{operation_id}@aurum.test",
            },
        )
        await connection.execute(
            text("""
                INSERT INTO public.app_user (
                  id, email, full_name, home_tenant_id, status, activated_at
                ) VALUES
                  (:owner_id, :owner_email, 'Owner', :tenant_id, 'active', :now),
                  (:manager_id, :manager_email, 'Manager', :tenant_id, 'active', :now),
                  (:existing_id, :existing_email, 'Existing', :tenant_id, 'invited', NULL)
                """),
            {
                "tenant_id": tenant_id,
                "owner_id": owner_id,
                "owner_email": f"owner-{operation_id}@aurum.test",
                "manager_id": manager_id,
                "manager_email": f"manager-{operation_id}@aurum.test",
                "existing_id": existing_id,
                "existing_email": existing_email,
                "now": now,
            },
        )
        await connection.execute(
            text("""
                INSERT INTO public.tenant_membership (
                  id, tenant_id, user_id, full_name, status, activated_at
                ) VALUES
                  (:owner_membership_id, :tenant_id, :owner_id, 'Owner', 'active', :now),
                  (:manager_membership_id, :tenant_id, :manager_id, 'Manager', 'active', :now)
                """),
            {
                "tenant_id": tenant_id,
                "owner_membership_id": owner_membership_id,
                "owner_id": owner_id,
                "manager_membership_id": manager_membership_id,
                "manager_id": manager_id,
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
                  (:manager_role_id, :tenant_id, 'Employee manager', 4, false, true,
                   false, NULL)
                """),
            {
                "owner_role_id": owner_role_id,
                "manager_role_id": manager_role_id,
                "tenant_id": tenant_id,
            },
        )
        await connection.execute(
            text("""
                INSERT INTO public.role_permission (role_id, permission_code)
                VALUES
                  (:owner_role_id, 'users.invite'), (:owner_role_id, 'roles.assign'),
                  (:manager_role_id, 'users.invite'), (:manager_role_id, 'roles.assign')
                """),
            {"owner_role_id": owner_role_id, "manager_role_id": manager_role_id},
        )

    async with maintenance_engine.begin() as connection:
        await connection.execute(
            text("""
                INSERT INTO public.access_role_version (
                  id, role_id, tenant_id, version, name, status,
                  creation_xid, published_at
                )
                SELECT gen_random_uuid(), role.id, role.tenant_id, 1, role.name,
                       'published', txid_current(), statement_timestamp()
                FROM public.role AS role
                WHERE role.id IN (:owner_role_id, :manager_role_id)
                """),
            {"owner_role_id": owner_role_id, "manager_role_id": manager_role_id},
        )

    async with db_engine.begin() as connection:
        await connection.execute(text("SELECT set_config('app.support_session', 'true', true)"))
        await connection.execute(
            text("""
                INSERT INTO public.user_assignment (
                  user_id, tenant_id, membership_id, role_id, branch_id, is_active
                ) VALUES
                  (:owner_id, :tenant_id, :owner_membership_id, :owner_role_id, NULL, true),
                  (:manager_id, :tenant_id, :manager_membership_id, :manager_role_id, NULL, true)
                """),
            {
                "tenant_id": tenant_id,
                "owner_id": owner_id,
                "owner_membership_id": owner_membership_id,
                "manager_id": manager_id,
                "manager_membership_id": manager_membership_id,
                "owner_role_id": owner_role_id,
                "manager_role_id": manager_role_id,
            },
        )
        await connection.execute(
            text("""
                INSERT INTO public.tenant_ownership (
                  tenant_id, membership_id, is_active, granted_at
                ) VALUES (:tenant_id, :membership_id, true, :now)
                """),
            {"tenant_id": tenant_id, "membership_id": owner_membership_id, "now": now},
        )

    app_engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    try:
        async with app_engine.begin() as connection:
            await _set_actor(connection, user_id=owner_id, tenant_id=tenant_id)
            first = (
                (
                    await connection.execute(
                        text("""
                        SELECT * FROM public.create_tenant_employee_invitation(
                          :tenant_id, :email, :full_name, :phone, :operation_id, :issued_at
                        )
                        """),
                        {
                            "tenant_id": tenant_id,
                            "email": employee_email,
                            "full_name": "New employee",
                            "phone": "+992900000001",
                            "operation_id": operation_id,
                            "issued_at": now,
                        },
                    )
                )
                .mappings()
                .one()
            )
            repeated = (
                (
                    await connection.execute(
                        text("""
                        SELECT * FROM public.create_tenant_employee_invitation(
                          :tenant_id, :email, :full_name, :phone, :operation_id, :issued_at
                        )
                        """),
                        {
                            "tenant_id": tenant_id,
                            "email": employee_email,
                            "full_name": "New employee",
                            "phone": "+992900000001",
                            "operation_id": operation_id,
                            "issued_at": now,
                        },
                    )
                )
                .mappings()
                .one()
            )
            employee_id = first["employee_user_id"]
            assert first["employee_created"] is True
            assert repeated["employee_created"] is False
            assert repeated["employee_user_id"] == employee_id
            assert repeated["employee_invitation_id"] == first["employee_invitation_id"]

        with pytest.raises(DBAPIError) as non_owner_error:
            async with app_engine.begin() as connection:
                await _set_actor(connection, user_id=manager_id, tenant_id=tenant_id)
                await connection.execute(
                    text("""
                        SELECT * FROM public.create_tenant_employee_invitation(
                          :tenant_id, :email, 'Forbidden', NULL, :operation_id, :issued_at
                        )
                        """),
                    {
                        "tenant_id": tenant_id,
                        "email": f"forbidden-{operation_id}@aurum.test",
                        "operation_id": uuid4(),
                        "issued_at": now,
                    },
                )
        assert getattr(non_owner_error.value.orig, "sqlstate", None) == "42501"

        with pytest.raises(DBAPIError) as existing_email_error:
            async with app_engine.begin() as connection:
                await _set_actor(connection, user_id=owner_id, tenant_id=tenant_id)
                await connection.execute(
                    text("""
                        SELECT * FROM public.create_tenant_employee_invitation(
                          :tenant_id, :email, 'Existing', NULL, :operation_id, :issued_at
                        )
                        """),
                    {
                        "tenant_id": tenant_id,
                        "email": existing_email,
                        "operation_id": uuid4(),
                        "issued_at": now,
                    },
                )
        assert getattr(existing_email_error.value.orig, "sqlstate", None) == "23505"

        async with db_engine.connect() as connection:
            result = (
                (
                    await connection.execute(
                        text("""
                        SELECT account.home_tenant_id, account.is_developer,
                               account.is_administrator, membership.status,
                               invitation.operation_id
                        FROM public.app_user AS account
                        JOIN public.tenant_membership AS membership
                          ON membership.user_id = account.id
                        JOIN public.tenant_invitation AS invitation
                          ON invitation.membership_id = membership.id
                        WHERE account.id = :employee_id
                        """),
                        {"employee_id": employee_id},
                    )
                )
                .mappings()
                .one()
            )
        assert result == {
            "home_tenant_id": tenant_id,
            "is_developer": False,
            "is_administrator": False,
            "status": "pending",
            "operation_id": operation_id,
        }
    finally:
        await app_engine.dispose()
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
                text(
                    "DELETE FROM public.access_role_version "
                    "WHERE role_id IN (:owner_role_id, :manager_role_id)"
                ),
                {"owner_role_id": owner_role_id, "manager_role_id": manager_role_id},
            )
            await connection.execute(
                text(
                    "UPDATE public.app_user SET home_tenant_id = NULL "
                    "WHERE home_tenant_id = :tenant_id"
                ),
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
            if employee_id is not None:
                await connection.execute(
                    text("DELETE FROM public.app_user WHERE id = :employee_id"),
                    {"employee_id": employee_id},
                )
            await connection.execute(
                text(
                    "DELETE FROM public.app_user WHERE id IN (:owner_id, :manager_id, :existing_id)"
                ),
                {"owner_id": owner_id, "manager_id": manager_id, "existing_id": existing_id},
            )
