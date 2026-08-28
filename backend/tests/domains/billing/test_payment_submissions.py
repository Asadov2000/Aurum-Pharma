"""End-to-end coverage for tenant bank payment submissions."""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from starlette.requests import Request

from app.core.config import get_settings
from app.core.deps import _seed_request_db_context, get_db
from app.core.security import create_access_token, hash_token
from app.core.time import utc_now
from app.domains.auth.models import AppUser, Session
from app.domains.billing.models import SubscriptionPlan
from app.domains.billing.repository import BillingRepository
from app.domains.billing.service import BillingService
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.roles.models import (
    TenantMembership,
    UserAssignment,
)
from app.main import app
from tests.domains.billing.test_financial_kernel import _headers
from tests.domains.billing.test_subscription_pricing import (
    _platform_identity,
    _prepare_trial_context,
    _publish_new_customer_price,
)
from tests.role_version_helpers import create_published_test_role


@pytest_asyncio.fixture
async def payment_client(client: AsyncClient) -> AsyncIterator[AsyncClient]:
    settings = get_settings()
    app_engine = create_async_engine(settings.DATABASE_URL_APP, poolclass=NullPool)
    support_engine = create_async_engine(settings.DATABASE_URL_SUPPORT, poolclass=NullPool)
    app_sessions = async_sessionmaker(app_engine, expire_on_commit=False)
    support_sessions = async_sessionmaker(support_engine, expire_on_commit=False)

    async def _override(request: Request) -> AsyncIterator[AsyncSession]:
        sessions = (
            support_sessions
            if bool(getattr(request.state, "use_support_pool", False))
            else app_sessions
        )
        async with sessions() as session:
            async with session.begin():
                await _seed_request_db_context(request, session)
                yield session

    previous_override = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _override
    try:
        yield client
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous_override
        await app_engine.dispose()
        await support_engine.dispose()


async def _tenant_token(
    db_session: AsyncSession,
    *,
    tenant_id: UUID,
    permissions: tuple[str, ...],
) -> str:
    suffix = uuid4().hex[:10]
    user = AppUser(
        email=f"payment-submission-{suffix}@aurum.tj",
        full_name="Payment Submission Owner",
        home_tenant_id=tenant_id,
        status="active",
    )
    db_session.add(user)
    await db_session.flush()
    membership = TenantMembership(
        tenant_id=tenant_id,
        user_id=user.id,
        full_name=user.full_name,
        status="active",
    )
    role = await create_published_test_role(
        db_session,
        tenant_id=tenant_id,
        name=f"Payment submission role {suffix}",
        permission_codes=permissions,
        level=3,
    )
    db_session.add(membership)
    await db_session.flush()
    db_session.add(
        UserAssignment(
            tenant_id=tenant_id,
            user_id=user.id,
            membership_id=membership.id,
            role_id=role.id,
        )
    )
    session = Session(
        user_id=user.id,
        refresh_token_hash=hash_token(secrets.token_hex(32)),
        expires_at=utc_now() + timedelta(days=1),
    )
    db_session.add(session)
    await db_session.flush()
    await db_session.execute(text("SELECT set_config('app.support_access_session_id', '', true)"))
    await db_session.execute(text("SELECT set_config('app.auth_session_id', '', true)"))
    await db_session.execute(text("SELECT set_config('app.tenant_id', '', true)"))
    return create_access_token(
        user.id,
        tenant_id=tenant_id,
        is_developer=False,
        is_administrator=False,
        session_id=session.id,
    )


async def _seed_committed_payment_context(
    db_engine: AsyncEngine,
    client: AsyncClient,
    *,
    tenant_permissions: tuple[str, ...],
) -> tuple[UUID, dict[str, object], str, str, str]:
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:

        async def _override(request: Request) -> AsyncIterator[AsyncSession]:
            await _seed_request_db_context(request, session)
            yield session

        previous_override = app.dependency_overrides.get(get_db)
        app.dependency_overrides[get_db] = _override
        try:
            suffix = uuid4().hex[:8]
            plan_code = f"payment_submission_{suffix}"
            legacy_plan = SubscriptionPlan(
                code=plan_code,
                name=f"Payment submission plan {suffix}",
                description="Dedicated pricing plan for payment-submission tests.",
                price_per_branch=Decimal("590.00"),
                currency="TJS",
                billing_period="monthly",
                annual_discount_pct=Decimal("20.00"),
                features={"support": "standard"},
            )
            session.add(legacy_plan)
            await session.flush()
            billing_token = await _publish_new_customer_price(
                session,
                client,
                plan_code=plan_code,
                access_kind="administrator",
            )

            foundation = FoundationService(FoundationRepository(session))
            billing_repo = BillingRepository(session)
            tenant = await foundation.create_tenant(
                payload={
                    "name": f"Payment submission tenant {suffix}",
                    "contact_email": f"payment-submission-{suffix}@aurum.tj",
                }
            )
            subscription = await BillingService(billing_repo).create_subscription(
                tenant_id=tenant.id,
                plan_id=legacy_plan.id,
                billing_period="monthly",
                branches_count=1,
                status="trial",
            )
            await _prepare_trial_context(
                session,
                tenant_id=tenant.id,
                subscription=subscription,
                period_end=utc_now() + timedelta(days=1),
                branches_count=1,
            )
            price_response = await client.post(
                (
                    f"/api/v1/admin/tenants/{tenant.id}/subscriptions/{subscription.id}"
                    "/price-applications/initial"
                ),
                headers=_headers(billing_token),
                json={
                    "operation_id": str(uuid4()),
                    "expected_row_version": subscription.row_version,
                },
            )
            assert price_response.status_code == 201, price_response.text
            invoice_response = await client.post(
                (
                    f"/api/v1/admin/tenants/{tenant.id}/subscriptions/{subscription.id}"
                    "/financial-invoices"
                ),
                headers=_headers(billing_token),
                json={
                    "operation_id": str(uuid4()),
                    "expected_row_version": subscription.row_version,
                },
            )
            assert invoice_response.status_code == 201, invoice_response.text

            tenant_token = await _tenant_token(
                session,
                tenant_id=tenant.id,
                permissions=tenant_permissions,
            )
            _, reviewer_token = await _platform_identity(
                session,
                access_kind="administrator",
            )
            _, approver_token = await _platform_identity(
                session,
                access_kind="administrator",
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            if previous_override is None:
                app.dependency_overrides.pop(get_db, None)
            else:
                app.dependency_overrides[get_db] = previous_override

    return (
        tenant.id,
        invoice_response.json()["item"],
        tenant_token,
        reviewer_token,
        approver_token,
    )


async def _assert_independent_approval_detail(
    client: AsyncClient,
    *,
    tenant_id: UUID,
    review_id: str,
    reviewer_token: str,
    approver_token: str,
    normalized_reference: str,
) -> None:
    path = f"/api/v1/admin/billing/tenants/{tenant_id}/payment-reviews/{review_id}"
    reviewer_detail = await client.get(path, headers=_headers(reviewer_token))
    assert reviewer_detail.status_code == 403, reviewer_detail.text

    wrong_tenant_detail = await client.get(
        f"/api/v1/admin/billing/tenants/{uuid4()}/payment-reviews/{review_id}",
        headers=_headers(approver_token),
    )
    assert wrong_tenant_detail.status_code == 404, wrong_tenant_detail.text

    approval_detail = await client.get(path, headers=_headers(approver_token))
    assert approval_detail.status_code == 200, approval_detail.text
    assert approval_detail.headers["cache-control"] == "private, no-store"
    assert approval_detail.json()["external_reference"] == normalized_reference
    assert approval_detail.json()["recipient_account_key"] == "aurum_tjs_primary"


async def _assert_unregistered_account_rejected(
    client: AsyncClient,
    *,
    tenant_id: UUID,
    submission_id: str,
    row_version: int,
    reviewer_token: str,
) -> None:
    response = await client.post(
        (
            f"/api/v1/admin/billing/tenants/{tenant_id}/payment-submissions/"
            f"{submission_id}/review"
        ),
        headers=_headers(reviewer_token),
        json={
            "operation_id": str(uuid4()),
            "expected_row_version": row_version,
            "recipient_account_key": "unregistered_tjs_account",
        },
    )
    assert response.status_code == 422, response.text


async def test_tenant_submission_promotes_to_independent_payment_approval(
    db_engine: AsyncEngine,
    payment_client: AsyncClient,
) -> None:
    client = payment_client
    tenant_id, invoice, tenant_token, reviewer_token, approver_token = (
        await _seed_committed_payment_context(
            db_engine,
            client,
            tenant_permissions=(
                "billing.overview.view",
                "billing.invoice.view",
                "billing.payment_submission.create",
                "billing.payment_submission.withdraw",
            ),
        )
    )
    operation_id = str(uuid4())
    external_reference = f"TJ-CLIENT/PRIVATE-{uuid4().hex[:12]}"
    normalized_reference = external_reference.replace("-", "").replace("/", "").upper()
    create_payload = {
        "operation_id": operation_id,
        "target_invoice_id": invoice["invoice_id"],
        "amount": "200.00",
        "paid_at": (utc_now() - timedelta(minutes=5)).isoformat(),
        "external_reference": external_reference,
    }
    created = await client.post(
        "/api/v1/billing/payment-submissions",
        headers=_headers(tenant_token),
        json=create_payload,
    )
    replay = await client.post(
        "/api/v1/billing/payment-submissions",
        headers=_headers(tenant_token),
        json=create_payload,
    )
    assert created.status_code == 201, created.text
    assert created.headers["cache-control"] == "private, no-store"
    assert created.json()["applied"] is True
    assert replay.status_code == 201, replay.text
    assert replay.json()["applied"] is False
    assert replay.json()["item"] == created.json()["item"]
    assert external_reference not in created.text

    conflicting_replay = await client.post(
        "/api/v1/billing/payment-submissions",
        headers=_headers(tenant_token),
        json={**create_payload, "amount": "201.00"},
    )
    assert conflicting_replay.status_code == 409, conflicting_replay.text

    tenant_list = await client.get(
        "/api/v1/billing/payment-submissions",
        headers=_headers(tenant_token),
    )
    assert tenant_list.status_code == 200, tenant_list.text
    assert tenant_list.headers["cache-control"] == "private, no-store"
    assert tenant_list.json()["items"][0]["status"] == "submitted"
    assert external_reference not in tenant_list.text

    platform_queue = await client.get(
        f"/api/v1/admin/billing/tenants/{tenant_id}/payment-submissions",
        headers=_headers(reviewer_token),
    )
    assert platform_queue.status_code == 200, platform_queue.text
    assert external_reference not in platform_queue.text
    queue_item = platform_queue.json()["items"][0]

    detail = await client.get(
        (
            f"/api/v1/admin/billing/tenants/{tenant_id}/payment-submissions/"
            f"{queue_item['submission_id']}"
        ),
        headers=_headers(reviewer_token),
    )
    assert detail.status_code == 200, detail.text
    assert detail.headers["cache-control"] == "private, no-store"
    assert detail.json()["external_reference"] == normalized_reference

    await _assert_unregistered_account_rejected(
        client,
        tenant_id=tenant_id,
        submission_id=queue_item["submission_id"],
        row_version=queue_item["row_version"],
        reviewer_token=reviewer_token,
    )

    promoted = await client.post(
        (
            f"/api/v1/admin/billing/tenants/{tenant_id}/payment-submissions/"
            f"{queue_item['submission_id']}/review"
        ),
        headers=_headers(reviewer_token),
        json={
            "operation_id": str(uuid4()),
            "expected_row_version": queue_item["row_version"],
            "recipient_account_key": "aurum_tjs_primary",
        },
    )
    assert promoted.status_code == 201, promoted.text
    assert external_reference not in promoted.text
    review = promoted.json()["item"]

    await _assert_independent_approval_detail(
        client,
        tenant_id=tenant_id,
        review_id=review["review_id"],
        reviewer_token=reviewer_token,
        approver_token=approver_token,
        normalized_reference=normalized_reference,
    )

    late_withdrawal = await client.post(
        (f"/api/v1/billing/payment-submissions/{queue_item['submission_id']}" "/withdraw"),
        headers=_headers(tenant_token),
        json={"operation_id": str(uuid4()), "expected_row_version": 2},
    )
    assert late_withdrawal.status_code == 409, late_withdrawal.text

    self_approval = await client.post(
        (
            f"/api/v1/admin/billing/tenants/{tenant_id}/payment-reviews/"
            f"{review['review_id']}/approve"
        ),
        headers=_headers(reviewer_token),
        json={"operation_id": str(uuid4()), "expected_row_version": review["row_version"]},
    )
    assert self_approval.status_code == 422, self_approval.text

    approval = await client.post(
        (
            f"/api/v1/admin/billing/tenants/{tenant_id}/payment-reviews/"
            f"{review['review_id']}/approve"
        ),
        headers=_headers(approver_token),
        json={"operation_id": str(uuid4()), "expected_row_version": review["row_version"]},
    )
    assert approval.status_code == 200, approval.text

    final_list = await client.get(
        "/api/v1/billing/payment-submissions",
        headers=_headers(tenant_token),
    )
    assert final_list.status_code == 200, final_list.text
    assert final_list.json()["items"][0]["status"] == "approved"
    assert external_reference not in final_list.text
    account = await client.get(
        "/api/v1/billing/financial-account",
        headers=_headers(tenant_token),
    )
    assert account.status_code == 200, account.text
    expected_outstanding = Decimal(str(invoice["total_amount"])) - Decimal("200.00")
    assert Decimal(account.json()["outstanding_amount"]) == expected_outstanding


async def test_submission_permission_withdrawal_and_direct_table_access_guards(
    db_engine: AsyncEngine,
    payment_client: AsyncClient,
) -> None:
    client = payment_client
    tenant_id, invoice, allowed_token, _, _ = await _seed_committed_payment_context(
        db_engine,
        client,
        tenant_permissions=(
            "billing.overview.view",
            "billing.invoice.view",
            "billing.payment_submission.create",
            "billing.payment_submission.withdraw",
        ),
    )
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with session_factory() as session:
        await session.execute(text("SELECT set_config('app.support_session', 'true', true)"))
        denied_token = await _tenant_token(
            session,
            tenant_id=tenant_id,
            permissions=("billing.overview.view", "billing.invoice.view"),
        )
        await session.commit()
    payload = {
        "operation_id": str(uuid4()),
        "target_invoice_id": invoice["invoice_id"],
        "amount": "50.00",
        "paid_at": (utc_now() - timedelta(minutes=5)).isoformat(),
        "external_reference": "WITHDRAW-0001",
    }
    denied = await client.post(
        "/api/v1/billing/payment-submissions",
        headers=_headers(denied_token),
        json=payload,
    )
    assert denied.status_code == 403, denied.text

    short_reference = await client.post(
        "/api/v1/billing/payment-submissions",
        headers=_headers(allowed_token),
        json={**payload, "operation_id": str(uuid4()), "external_reference": "AB12"},
    )
    assert short_reference.status_code == 422, short_reference.text

    created = await client.post(
        "/api/v1/billing/payment-submissions",
        headers=_headers(allowed_token),
        json=payload,
    )
    assert created.status_code == 201, created.text
    item = created.json()["item"]
    withdraw_payload = {
        "operation_id": str(uuid4()),
        "expected_row_version": item["row_version"],
    }
    path = f"/api/v1/billing/payment-submissions/{item['submission_id']}/withdraw"
    withdrawn = await client.post(
        path,
        headers=_headers(allowed_token),
        json=withdraw_payload,
    )
    replay = await client.post(
        path,
        headers=_headers(allowed_token),
        json=withdraw_payload,
    )
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["item"]["status"] == "withdrawn"
    assert withdrawn.json()["applied"] is True
    assert replay.status_code == 200, replay.text
    assert replay.json()["applied"] is False

    for table in (
        "billing_payment_submission",
        "billing_payment_submission_event",
    ):
        async with db_engine.connect() as connection:
            direct_access = await connection.scalar(
                text(
                    "SELECT has_table_privilege("
                    "'aurum_app', :table, 'SELECT,INSERT,UPDATE,DELETE')"
                ),
                {"table": f"public.{table}"},
            )
        assert direct_access is False
