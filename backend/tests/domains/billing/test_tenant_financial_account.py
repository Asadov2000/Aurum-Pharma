"""Tenant-facing access to the protected billing financial projection."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.security import create_access_token
from app.domains.auth.models import AppUser
from app.domains.foundation.models import Tenant
from app.domains.roles.models import (
    AccessRoleVersion,
    AccessRoleVersionPermission,
    Role,
    RolePermission,
    TenantMembership,
    UserAssignment,
)
from tests.role_version_helpers import create_published_test_role


@dataclass(frozen=True)
class TenantBillingSubjects:
    tenant_id: UUID
    owner_user_id: UUID
    seller_user_id: UUID


async def _seed_committed_subjects(
    maintenance_engine: AsyncEngine,
) -> TenantBillingSubjects:
    session_factory = async_sessionmaker(maintenance_engine, expire_on_commit=False)
    suffix = uuid4().hex[:10]
    async with session_factory() as session, session.begin():
        await session.execute(text("SELECT set_config('app.support_session', 'true', true)"))
        tenant = Tenant(
            name=f"Billing boundary {suffix}",
            contact_email=f"billing-boundary-{suffix}@aurum.tj",
            status="active",
        )
        session.add(tenant)
        await session.flush()

        owner = AppUser(
            email=f"billing-owner-{suffix}@aurum.tj",
            full_name="Billing Owner",
            home_tenant_id=tenant.id,
            status="active",
        )
        seller = AppUser(
            email=f"billing-seller-{suffix}@aurum.tj",
            full_name="Billing Seller",
            home_tenant_id=tenant.id,
            status="active",
        )
        session.add_all([owner, seller])
        await session.flush()

        owner_membership = TenantMembership(
            tenant_id=tenant.id,
            user_id=owner.id,
            full_name=owner.full_name,
            status="active",
        )
        seller_membership = TenantMembership(
            tenant_id=tenant.id,
            user_id=seller.id,
            full_name=seller.full_name,
            status="active",
        )
        owner_role = await create_published_test_role(
            session,
            tenant_id=tenant.id,
            name=f"Billing owner {suffix}",
            permission_codes=["billing.overview.view", "billing.invoice.view"],
            level=3,
        )
        seller_role = await create_published_test_role(
            session,
            tenant_id=tenant.id,
            name=f"Billing seller {suffix}",
            permission_codes=[],
            level=4,
        )
        session.add_all([owner_membership, seller_membership])
        await session.flush()
        session.add_all(
            [
                UserAssignment(
                    tenant_id=tenant.id,
                    user_id=owner.id,
                    membership_id=owner_membership.id,
                    role_id=owner_role.id,
                ),
                UserAssignment(
                    tenant_id=tenant.id,
                    user_id=seller.id,
                    membership_id=seller_membership.id,
                    role_id=seller_role.id,
                ),
            ]
        )

    return TenantBillingSubjects(
        tenant_id=tenant.id,
        owner_user_id=owner.id,
        seller_user_id=seller.id,
    )


async def _cleanup_committed_subjects(
    maintenance_engine: AsyncEngine,
    subjects: TenantBillingSubjects,
) -> None:
    session_factory = async_sessionmaker(maintenance_engine, expire_on_commit=False)
    async with session_factory() as session, session.begin():
        await session.execute(text("SELECT set_config('app.support_session', 'true', true)"))
        await session.execute(
            delete(UserAssignment).where(UserAssignment.tenant_id == subjects.tenant_id)
        )
        role_ids = list(
            await session.scalars(select(Role.id).where(Role.tenant_id == subjects.tenant_id))
        )
        if role_ids:
            version_ids = list(
                await session.scalars(
                    select(AccessRoleVersion.id).where(AccessRoleVersion.role_id.in_(role_ids))
                )
            )
            if version_ids:
                await session.execute(
                    delete(AccessRoleVersionPermission).where(
                        AccessRoleVersionPermission.role_version_id.in_(version_ids)
                    )
                )
                await session.execute(
                    delete(AccessRoleVersion).where(AccessRoleVersion.id.in_(version_ids))
                )
            await session.execute(
                delete(RolePermission).where(RolePermission.role_id.in_(role_ids))
            )
            await session.execute(delete(Role).where(Role.id.in_(role_ids)))
        # Membership history can only disappear through the tenant cascade.
        await session.execute(delete(Tenant).where(Tenant.id == subjects.tenant_id))
        await session.execute(
            delete(AppUser).where(AppUser.id.in_([subjects.owner_user_id, subjects.seller_user_id]))
        )


async def _call_projection(
    app_engine: AsyncEngine,
    *,
    context_user_id: UUID | None,
    context_tenant_id: UUID | None,
    actor_user_id: UUID,
    tenant_id: UUID,
    forged_support: bool = False,
) -> dict[str, object]:
    async with app_engine.connect() as connection, connection.begin():
        if context_user_id is not None:
            await connection.execute(
                text("SELECT set_config('app.user_id', :value, true)"),
                {"value": str(context_user_id)},
            )
        if context_tenant_id is not None:
            await connection.execute(
                text("SELECT set_config('app.tenant_id', :value, true)"),
                {"value": str(context_tenant_id)},
            )
        if forged_support:
            await connection.execute(text("SELECT set_config('app.support_session', 'true', true)"))
        result = await connection.scalar(
            text(
                "SELECT public.read_tenant_billing_financial_account(" ":actor_user_id, :tenant_id)"
            ),
            {
                "actor_user_id": actor_user_id,
                "tenant_id": tenant_id,
            },
        )
        return dict(result)


async def test_tenant_financial_account_owner_success_and_seller_denied(
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    client: AsyncClient,
) -> None:
    subjects = await _seed_committed_subjects(db_engine)
    try:
        owner_token = create_access_token(
            subjects.owner_user_id,
            tenant_id=subjects.tenant_id,
            is_developer=False,
            is_administrator=False,
        )
        seller_token = create_access_token(
            subjects.seller_user_id,
            tenant_id=subjects.tenant_id,
            is_developer=False,
            is_administrator=False,
        )
        owner_response = await client.get(
            "/api/v1/billing/financial-account",
            headers={"Authorization": f"Bearer {owner_token}"},
        )
        assert owner_response.status_code == 200, owner_response.text
        assert owner_response.headers["cache-control"] == "private, no-store"
        assert owner_response.json() == {
            "subscription": None,
            "currency": "TJS",
            "outstanding_amount": "0.00",
            "credit_balance": "0.00",
            "invoices": [],
            "payments": [],
        }
        for private_field in (
            "tenant_id",
            "subscription_id",
            "price_application_id",
            "journal_balanced",
            "external_reference",
            "recipient_account_key",
        ):
            assert private_field not in owner_response.text

        seller_response = await client.get(
            "/api/v1/billing/financial-account",
            headers={"Authorization": f"Bearer {seller_token}"},
        )
        assert seller_response.status_code == 403
    finally:
        await _cleanup_committed_subjects(maintenance_engine, subjects)


async def test_tenant_financial_projection_rejects_direct_context_abuse(
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    subjects = await _seed_committed_subjects(db_engine)
    app_engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    try:
        account = await _call_projection(
            app_engine,
            context_user_id=subjects.owner_user_id,
            context_tenant_id=subjects.tenant_id,
            actor_user_id=subjects.owner_user_id,
            tenant_id=subjects.tenant_id,
        )
        assert account["invoices"] == []

        denied_calls = (
            {
                "context_user_id": subjects.seller_user_id,
                "context_tenant_id": subjects.tenant_id,
                "actor_user_id": subjects.seller_user_id,
                "tenant_id": subjects.tenant_id,
            },
            {
                "context_user_id": subjects.owner_user_id,
                "context_tenant_id": subjects.tenant_id,
                "actor_user_id": uuid4(),
                "tenant_id": subjects.tenant_id,
            },
            {
                "context_user_id": subjects.owner_user_id,
                "context_tenant_id": subjects.tenant_id,
                "actor_user_id": subjects.owner_user_id,
                "tenant_id": uuid4(),
            },
            {
                "context_user_id": None,
                "context_tenant_id": None,
                "actor_user_id": subjects.owner_user_id,
                "tenant_id": subjects.tenant_id,
            },
            {
                "context_user_id": subjects.owner_user_id,
                "context_tenant_id": subjects.tenant_id,
                "actor_user_id": subjects.owner_user_id,
                "tenant_id": subjects.tenant_id,
                "forged_support": True,
            },
        )
        for call in denied_calls:
            with pytest.raises(DBAPIError) as error:
                await _call_projection(app_engine, **call)
            assert getattr(error.value.orig, "sqlstate", None) == "42501"
    finally:
        await app_engine.dispose()
        await _cleanup_committed_subjects(maintenance_engine, subjects)
