"""Retired price application commands preserve authentication and historical rows."""

from __future__ import annotations

from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.domains.auth.models import AppUser
from tests.auth_helpers import create_support_access_token
from tests.domains.billing.historical import seed_historical_account
from tests.platform_access_helpers import create_test_platform_user


async def _platform_identity(
    db_session: AsyncSession,
    *,
    access_kind: str = "developer",
) -> tuple[AppUser, str]:
    user = await create_test_platform_user(db_session, access_kind=access_kind)
    return user, await create_support_access_token(db_session, user)


async def test_initial_price_application_is_retired_even_for_existing_subscription(
    db_session: AsyncSession,
    platform_client: AsyncClient,
    maintenance_engine: AsyncEngine,
) -> None:
    tenant_id, subscription_id, _ = await seed_historical_account(maintenance_engine)
    _, token = await _platform_identity(db_session)
    path = (
        f"/api/v1/admin/tenants/{tenant_id}/subscriptions/{subscription_id}"
        "/price-applications/initial"
    )
    payload = {"operation_id": str(uuid4()), "expected_row_version": 1}
    for _ in range(2):
        response = await platform_client.post(
            path, headers={"Authorization": f"Bearer {token}"}, json=payload
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == "subscriptions_disabled"
    async with maintenance_engine.connect() as connection:
        count = await connection.scalar(
            text(
                "SELECT count(*) FROM public.billing_subscription_price_application "
                "WHERE tenant_id=:tenant"
            ),
            {"tenant": tenant_id},
        )
    assert count == 1


async def test_initial_price_application_requires_authentication(
    platform_client: AsyncClient,
) -> None:
    response = await platform_client.post(
        f"/api/v1/admin/tenants/{uuid4()}/subscriptions/{uuid4()}/price-applications/initial",
        json={"operation_id": str(uuid4()), "expected_row_version": 1},
    )
    assert response.status_code == 401
