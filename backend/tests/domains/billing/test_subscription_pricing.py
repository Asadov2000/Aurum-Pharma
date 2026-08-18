"""Protected application of published pricing to tenant subscriptions."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.domains.auth.models import AppUser
from app.domains.billing.models import TenantSubscription
from app.domains.billing.repository import BillingRepository
from app.domains.billing.service import BillingService
from tests.auth_helpers import create_support_access_token
from tests.platform_access_helpers import create_test_platform_user


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _prepare_trial_context(
    db_session: AsyncSession,
    *,
    tenant_id: UUID,
    subscription: TenantSubscription,
    period_end: datetime,
    branches_count: int,
) -> None:
    await db_session.execute(
        text(
            "UPDATE public.tenant_subscription "
            "SET period_end = :period_end WHERE id = :subscription_id"
        ),
        {"period_end": period_end, "subscription_id": subscription.id},
    )
    await db_session.execute(
        text(
            "UPDATE public.tenant SET status = 'trial', "
            "trial_started_at = :period_start, trial_ends_at = :period_end "
            "WHERE id = :tenant_id"
        ),
        {
            "period_start": subscription.period_start,
            "period_end": period_end,
            "tenant_id": tenant_id,
        },
    )
    await db_session.execute(
        text(
            "INSERT INTO public.branch (tenant_id, name) "
            "SELECT :tenant_id, 'Pricing branch ' || series::TEXT "
            "FROM generate_series(1, :branches_count) AS series"
        ),
        {"tenant_id": tenant_id, "branches_count": branches_count},
    )
    await db_session.refresh(subscription)


async def _platform_identity(
    db_session: AsyncSession,
    *,
    access_kind: str = "developer",
) -> tuple[AppUser, str]:
    user = await create_test_platform_user(db_session, access_kind=access_kind)
    return user, await create_support_access_token(db_session, user)


async def _publish_new_customer_price(
    db_session: AsyncSession,
    client: AsyncClient,
    *,
    activate: bool = True,
    plan_code: str = "aurum_pharma",
    access_kind: str = "developer",
) -> str:
    _, author_token = await _platform_identity(db_session, access_kind=access_kind)
    plan_response = await client.post(
        "/api/v1/admin/billing/plans",
        headers=_headers(author_token),
        json={
            "operation_id": str(uuid4()),
            "code": plan_code,
            "name": "Aurum Pharma",
            "description": "Версионируемый тариф для первого платного периода.",
        },
    )
    assert plan_response.status_code == 201, plan_response.text
    plan_id = plan_response.json()["item"]["plan_id"]

    draft_response = await client.post(
        f"/api/v1/admin/billing/plans/{plan_id}/prices",
        headers=_headers(author_token),
        json={
            "operation_id": str(uuid4()),
            "monthly_price_per_branch": "590.00",
            "annual_discount_pct": "20.00",
            "audience": "new_customers",
            "notice_days": 0,
            "change_reason": "Публикация тарифа для проверки подписочного расчёта.",
            "terms_snapshot": {"support": "standard", "revision": 1},
        },
    )
    assert draft_response.status_code == 201, draft_response.text
    draft = draft_response.json()["item"]

    _, approver_token = await _platform_identity(db_session, access_kind=access_kind)
    scheduled_response = await client.post(
        f"/api/v1/admin/billing/prices/{draft['price_version_id']}/schedule",
        headers=_headers(approver_token),
        json={
            "operation_id": str(uuid4()),
            "expected_row_version": draft["row_version"],
            "effective_from": (utc_now() + timedelta(seconds=1)).isoformat(),
        },
    )
    assert scheduled_response.status_code == 200, scheduled_response.text
    scheduled = scheduled_response.json()["item"]
    if not activate:
        return approver_token
    await asyncio.sleep(1.1)
    activated_response = await client.post(
        f"/api/v1/admin/billing/prices/{draft['price_version_id']}/activate",
        headers=_headers(approver_token),
        json={
            "operation_id": str(uuid4()),
            "expected_row_version": scheduled["row_version"],
        },
    )
    assert activated_response.status_code == 200, activated_response.text
    return approver_token


async def test_initial_price_application_is_exact_idempotent_and_tenant_bound(
    db_session: AsyncSession,
    platform_client: AsyncClient,
    make_tenant_with_plan,
) -> None:
    token = await _publish_new_customer_price(db_session, platform_client)
    tenant, legacy_plan = await make_tenant_with_plan()
    other_tenant, _ = await make_tenant_with_plan()
    service = BillingService(BillingRepository(db_session))
    subscription = await service.create_subscription(
        tenant_id=tenant.id,
        plan_id=legacy_plan.id,
        billing_period="monthly",
        branches_count=2,
        status="trial",
    )
    legacy_amount = subscription.amount
    period_start = datetime(2027, 1, 30, 19, 15, tzinfo=UTC)
    await _prepare_trial_context(
        db_session,
        tenant_id=tenant.id,
        subscription=subscription,
        period_end=period_start,
        branches_count=2,
    )
    operation_id = str(uuid4())
    payload = {
        "operation_id": operation_id,
        "expected_row_version": subscription.row_version,
    }
    path = (
        f"/api/v1/admin/tenants/{tenant.id}/subscriptions/{subscription.id}"
        "/price-applications/initial"
    )

    first = await platform_client.post(path, headers=_headers(token), json=payload)
    replay = await platform_client.post(path, headers=_headers(token), json=payload)
    cross_ledger_reuse = await platform_client.post(
        "/api/v1/admin/billing/plans",
        headers=_headers(token),
        json={
            "operation_id": operation_id,
            "code": f"duplicate_operation_{uuid4().hex}",
            "name": "Duplicate operation guard",
            "description": "Must roll back before creating a second financial effect.",
        },
    )
    stale = await platform_client.post(
        path,
        headers=_headers(token),
        json={"operation_id": str(uuid4()), "expected_row_version": 1},
    )
    wrong_tenant = await platform_client.post(
        (
            f"/api/v1/admin/tenants/{other_tenant.id}/subscriptions/{subscription.id}"
            "/price-applications/initial"
        ),
        headers=_headers(token),
        json={"operation_id": str(uuid4()), "expected_row_version": subscription.row_version},
    )

    assert first.status_code == 201, first.text
    assert first.headers["cache-control"] == "private, no-store"
    assert first.json()["applied"] is True
    item = first.json()["item"]
    assert item["source_type"] == "price_version"
    assert item["branches_count"] == 2
    assert item["monthly_price_per_branch"] == "590.00"
    assert item["calculated_amount"] == "1180.00"
    assert datetime.fromisoformat(item["period_start"]) == period_start
    assert datetime.fromisoformat(item["period_end"]) == datetime(2027, 2, 27, 19, 15, tzinfo=UTC)
    assert replay.status_code == 201, replay.text
    assert replay.json()["applied"] is False
    assert replay.json()["item"] == item
    assert cross_ledger_reuse.status_code == 409, cross_ledger_reuse.text
    assert stale.status_code == 409, stale.text
    assert wrong_tenant.status_code == 404, wrong_tenant.text

    await db_session.refresh(subscription)
    assert subscription.amount == legacy_amount


async def test_yearly_price_application_uses_published_discount(
    db_session: AsyncSession,
    platform_client: AsyncClient,
    make_tenant_with_plan,
) -> None:
    token = await _publish_new_customer_price(db_session, platform_client)
    tenant, legacy_plan = await make_tenant_with_plan()
    subscription = await BillingService(BillingRepository(db_session)).create_subscription(
        tenant_id=tenant.id,
        plan_id=legacy_plan.id,
        billing_period="yearly",
        branches_count=3,
        status="trial",
    )
    await _prepare_trial_context(
        db_session,
        tenant_id=tenant.id,
        subscription=subscription,
        period_end=subscription.period_end,
        branches_count=3,
    )

    response = await platform_client.post(
        (
            f"/api/v1/admin/tenants/{tenant.id}/subscriptions/{subscription.id}"
            "/price-applications/initial"
        ),
        headers=_headers(token),
        json={
            "operation_id": str(uuid4()),
            "expected_row_version": subscription.row_version,
        },
    )

    assert response.status_code == 201, response.text
    assert Decimal(response.json()["item"]["calculated_amount"]) == Decimal("16992.00")


async def test_initial_price_application_rejects_expired_trial(
    db_session: AsyncSession,
    platform_client: AsyncClient,
    make_tenant_with_plan,
) -> None:
    token = await _publish_new_customer_price(db_session, platform_client)
    tenant, legacy_plan = await make_tenant_with_plan()
    subscription = await BillingService(BillingRepository(db_session)).create_subscription(
        tenant_id=tenant.id,
        plan_id=legacy_plan.id,
        billing_period="monthly",
        branches_count=1,
        status="trial",
    )
    await _prepare_trial_context(
        db_session,
        tenant_id=tenant.id,
        subscription=subscription,
        period_end=utc_now() - timedelta(seconds=1),
        branches_count=1,
    )

    response = await platform_client.post(
        (
            f"/api/v1/admin/tenants/{tenant.id}/subscriptions/{subscription.id}"
            "/price-applications/initial"
        ),
        headers=_headers(token),
        json={
            "operation_id": str(uuid4()),
            "expected_row_version": subscription.row_version,
        },
    )

    assert response.status_code == 422, response.text


async def test_initial_price_application_waits_for_scheduled_price(
    db_session: AsyncSession,
    platform_client: AsyncClient,
    make_tenant_with_plan,
) -> None:
    token = await _publish_new_customer_price(
        db_session,
        platform_client,
        activate=False,
    )
    tenant, legacy_plan = await make_tenant_with_plan()
    subscription = await BillingService(BillingRepository(db_session)).create_subscription(
        tenant_id=tenant.id,
        plan_id=legacy_plan.id,
        billing_period="monthly",
        branches_count=1,
        status="trial",
    )
    await _prepare_trial_context(
        db_session,
        tenant_id=tenant.id,
        subscription=subscription,
        period_end=subscription.period_end,
        branches_count=1,
    )

    response = await platform_client.post(
        (
            f"/api/v1/admin/tenants/{tenant.id}/subscriptions/{subscription.id}"
            "/price-applications/initial"
        ),
        headers=_headers(token),
        json={
            "operation_id": str(uuid4()),
            "expected_row_version": subscription.row_version,
        },
    )

    assert response.status_code == 409, response.text


async def test_initial_price_application_requires_authentication(
    platform_client: AsyncClient,
) -> None:
    response = await platform_client.post(
        f"/api/v1/admin/tenants/{uuid4()}/subscriptions/{uuid4()}" "/price-applications/initial",
        json={"operation_id": str(uuid4()), "expected_row_version": 1},
    )
    assert response.status_code == 401
