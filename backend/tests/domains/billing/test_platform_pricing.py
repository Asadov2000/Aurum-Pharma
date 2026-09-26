"""Retired pricing commands fail explicitly while authenticated history is readable."""

from datetime import timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from tests.auth_helpers import create_support_access_token
from tests.platform_access_helpers import create_test_platform_user


@pytest.mark.parametrize(
    "command", ["plan", "draft", "schedule", "activate", "cancel", "apply", "invoice"]
)
async def test_retired_pricing_commands_cannot_create_charges(
    db_session: AsyncSession, platform_client: AsyncClient, command: str
) -> None:
    user = await create_test_platform_user(db_session, access_kind="developer")
    token = await create_support_access_token(db_session, user)
    identifier = uuid4()
    payload: dict[str, object] = {"operation_id": str(uuid4())}
    prefix = "/api/v1/admin/billing"
    if command == "plan":
        path = f"{prefix}/plans"
        payload.update(code="retired_plan", name="Retired plan")
    elif command == "draft":
        path = f"{prefix}/plans/{identifier}/prices"
        payload.update(
            monthly_price_per_branch="590.00",
            annual_discount_pct="20.00",
            audience="new_customers",
            notice_days=0,
            change_reason="Historical pricing is read only",
            terms_snapshot={},
        )
    elif command in {"apply", "invoice"}:
        suffix = "price-applications/initial" if command == "apply" else "financial-invoices"
        path = f"/api/v1/admin/tenants/{identifier}/subscriptions/{uuid4()}/{suffix}"
        payload["expected_row_version"] = 1
    else:
        path = f"{prefix}/prices/{identifier}/{command}"
        payload["expected_row_version"] = 1
        if command == "schedule":
            payload["effective_from"] = (utc_now() + timedelta(days=40)).isoformat()
        elif command == "cancel":
            payload.update(reason_code="commercial_change", reason="No recurring subscription")
    response = await platform_client.post(
        path, headers={"Authorization": f"Bearer {token}"}, json=payload
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "subscriptions_disabled"


async def test_pricing_history_requires_authentication(platform_client: AsyncClient) -> None:
    response = await platform_client.get("/api/v1/admin/billing/plans")
    assert response.status_code == 401


async def test_pricing_history_remains_readable(
    db_session: AsyncSession, platform_client: AsyncClient
) -> None:
    user = await create_test_platform_user(db_session, access_kind="developer")
    token = await create_support_access_token(db_session, user)
    response = await platform_client.get(
        "/api/v1/admin/billing/plans", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200, response.text
