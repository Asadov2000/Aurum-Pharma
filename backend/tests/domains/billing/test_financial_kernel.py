"""Integration coverage for the immutable billing financial kernel."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.domains.billing.models import TenantSubscription
from app.domains.billing.repository import BillingRepository
from app.domains.billing.service import BillingService
from tests.domains.billing.test_subscription_pricing import (
    _platform_identity,
    _prepare_trial_context,
    _publish_new_customer_price,
)


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _create_initial_invoice(
    db_session: AsyncSession,
    platform_client: AsyncClient,
    make_tenant_with_plan,
    trial_remaining: timedelta = timedelta(days=1),
) -> tuple[str, UUID, TenantSubscription, dict[str, object]]:
    billing_token = await _publish_new_customer_price(db_session, platform_client)
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
        period_end=utc_now() + trial_remaining,
        branches_count=1,
    )
    price_response = await platform_client.post(
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

    invoice_response = await platform_client.post(
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
    return billing_token, tenant.id, subscription, invoice_response.json()["item"]


async def _review_and_approve(
    platform_client: AsyncClient,
    *,
    tenant_id: UUID,
    invoice_id: str,
    amount: str,
    reference: str,
    reviewer_token: str,
    approver_token: str,
) -> Response:
    review_response = await platform_client.post(
        f"/api/v1/admin/billing/tenants/{tenant_id}/payment-reviews",
        headers=_headers(reviewer_token),
        json={
            "operation_id": str(uuid4()),
            "target_invoice_id": invoice_id,
            "amount": amount,
            "paid_at": (utc_now() - timedelta(minutes=5)).isoformat(),
            "recipient_account_key": "aurum_tjs_primary",
            "external_reference": reference,
        },
    )
    assert review_response.status_code == 201, review_response.text
    review = review_response.json()["item"]
    return await platform_client.post(
        (
            f"/api/v1/admin/billing/tenants/{tenant_id}/payment-reviews/"
            f"{review['review_id']}/approve"
        ),
        headers=_headers(approver_token),
        json={
            "operation_id": str(uuid4()),
            "expected_row_version": review["row_version"],
        },
    )


async def _financial_account(
    platform_client: AsyncClient,
    *,
    tenant_id: UUID,
    token: str,
) -> dict[str, object]:
    response = await platform_client.get(
        f"/api/v1/admin/billing/tenants/{tenant_id}/financial-account",
        headers=_headers(token),
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    return response.json()


async def test_invoice_is_exact_idempotent_tenant_bound_and_balanced(
    db_session: AsyncSession,
    platform_client: AsyncClient,
    make_tenant_with_plan,
) -> None:
    token, tenant_id, subscription, invoice = await _create_initial_invoice(
        db_session,
        platform_client,
        make_tenant_with_plan,
    )
    operation_id = str(uuid4())
    path = (
        f"/api/v1/admin/tenants/{tenant_id}/subscriptions/{subscription.id}" "/financial-invoices"
    )
    duplicate_period = await platform_client.post(
        path,
        headers=_headers(token),
        json={
            "operation_id": operation_id,
            "expected_row_version": subscription.row_version,
        },
    )
    assert duplicate_period.status_code == 409, duplicate_period.text

    other_tenant, _ = await make_tenant_with_plan()
    wrong_tenant = await platform_client.post(
        (
            f"/api/v1/admin/tenants/{other_tenant.id}/subscriptions/{subscription.id}"
            "/financial-invoices"
        ),
        headers=_headers(token),
        json={
            "operation_id": str(uuid4()),
            "expected_row_version": subscription.row_version,
        },
    )
    assert wrong_tenant.status_code == 404, wrong_tenant.text

    account = await _financial_account(platform_client, tenant_id=tenant_id, token=token)
    assert account["journal_balanced"] is True
    assert account["outstanding_amount"] == "590.00"
    assert account["credit_balance"] == "0.00"
    assert account["invoices"] == [invoice]
    assert invoice["price_application_kind"] == "initial"
    assert invoice["settlement_state"] == "unpaid"
    assert invoice["total_amount"] == "590.00"

    for table in (
        "billing_invoice",
        "billing_payment",
        "billing_payment_allocation",
        "billing_journal_entry",
    ):
        can_write = await db_session.scalar(
            text("SELECT has_table_privilege('aurum_app', :table, 'INSERT,UPDATE,DELETE')"),
            {"table": f"public.{table}"},
        )
        assert can_write is False


async def test_partial_payment_needs_independent_approval_and_full_cover_restores_access(
    db_session: AsyncSession,
    platform_client: AsyncClient,
    make_tenant_with_plan,
) -> None:
    _, tenant_id, subscription, invoice = await _create_initial_invoice(
        db_session,
        platform_client,
        make_tenant_with_plan,
    )
    _, reviewer_token = await _platform_identity(db_session)
    _, approver_token = await _platform_identity(db_session)
    review_response = await platform_client.post(
        f"/api/v1/admin/billing/tenants/{tenant_id}/payment-reviews",
        headers=_headers(reviewer_token),
        json={
            "operation_id": str(uuid4()),
            "target_invoice_id": invoice["invoice_id"],
            "amount": "200.00",
            "paid_at": (utc_now() - timedelta(minutes=5)).isoformat(),
            "recipient_account_key": "aurum_tjs_primary",
            "external_reference": "TJ-2026/0001",
        },
    )
    assert review_response.status_code == 201, review_response.text
    review = review_response.json()["item"]
    assert review["status"] == "pending_approval"
    assert "external_reference" not in review_response.text
    approval_path = (
        f"/api/v1/admin/billing/tenants/{tenant_id}/payment-reviews/"
        f"{review['review_id']}/approve"
    )
    self_approval = await platform_client.post(
        approval_path,
        headers=_headers(reviewer_token),
        json={
            "operation_id": str(uuid4()),
            "expected_row_version": review["row_version"],
        },
    )
    assert self_approval.status_code == 422, self_approval.text

    approval_operation_id = str(uuid4())
    approval_payload = {
        "operation_id": approval_operation_id,
        "expected_row_version": review["row_version"],
    }
    partial = await platform_client.post(
        approval_path,
        headers=_headers(approver_token),
        json=approval_payload,
    )
    replay = await platform_client.post(
        approval_path,
        headers=_headers(approver_token),
        json=approval_payload,
    )
    assert partial.status_code == 200, partial.text
    assert partial.json()["applied"] is True
    assert partial.json()["item"]["allocated_amount"] == "200.00"
    assert partial.json()["item"]["target_outstanding_amount"] == "390.00"
    assert partial.json()["item"]["access_restored"] is False
    assert replay.status_code == 200, replay.text
    assert replay.json()["applied"] is False
    assert replay.json()["item"] == partial.json()["item"]

    full = await _review_and_approve(
        platform_client,
        tenant_id=tenant_id,
        invoice_id=str(invoice["invoice_id"]),
        amount="390.00",
        reference="TJ-2026/0002",
        reviewer_token=reviewer_token,
        approver_token=approver_token,
    )
    assert full.status_code == 200, full.text
    assert full.json()["item"]["target_outstanding_amount"] == "0.00"
    assert full.json()["item"]["blocking_outstanding_amount"] == "0.00"
    assert full.json()["item"]["access_restored"] is True
    assert full.json()["item"]["subscription_status"] == "active"

    await db_session.refresh(subscription)
    tenant_status = await db_session.scalar(
        text("SELECT status FROM public.tenant WHERE id = :tenant_id"),
        {"tenant_id": tenant_id},
    )
    assert subscription.status == "active"
    assert tenant_status == "active"
    account = await _financial_account(
        platform_client,
        tenant_id=tenant_id,
        token=approver_token,
    )
    assert account["outstanding_amount"] == "0.00"
    assert account["credit_balance"] == "0.00"
    assert account["journal_balanced"] is True
    assert len(account["payments"]) == 2
    assert account["invoices"][0]["settlement_state"] == "paid"


async def test_renewals_repeat_and_payment_covers_oldest_debt_before_credit(
    db_session: AsyncSession,
    platform_client: AsyncClient,
    make_tenant_with_plan,
) -> None:
    _, tenant_id, subscription, initial_invoice = await _create_initial_invoice(
        db_session,
        platform_client,
        make_tenant_with_plan,
        trial_remaining=timedelta(seconds=3),
    )
    _, reviewer_token = await _platform_identity(db_session)
    _, approver_token = await _platform_identity(db_session)
    initial_payment = await _review_and_approve(
        platform_client,
        tenant_id=tenant_id,
        invoice_id=str(initial_invoice["invoice_id"]),
        amount="590.00",
        reference="RENEWAL0001",
        reviewer_token=reviewer_token,
        approver_token=approver_token,
    )
    assert initial_payment.status_code == 200, initial_payment.text
    assert initial_payment.json()["item"]["access_restored"] is True

    await asyncio.sleep(3.1)
    initial_period_start = datetime.fromisoformat(str(initial_invoice["period_start"]))

    await db_session.execute(
        text(
            "UPDATE public.tenant_subscription "
            "SET period_end = :period_end WHERE tenant_id = :tenant_id AND id = :subscription_id"
        ),
        {
            "period_end": initial_period_start + timedelta(seconds=1),
            "tenant_id": tenant_id,
            "subscription_id": subscription.id,
        },
    )
    await db_session.refresh(subscription)
    overdue_response = await platform_client.post(
        (
            f"/api/v1/admin/tenants/{tenant_id}/subscriptions/{subscription.id}"
            "/financial-invoices"
        ),
        headers=_headers(approver_token),
        json={
            "operation_id": str(uuid4()),
            "expected_row_version": subscription.row_version,
        },
    )
    assert overdue_response.status_code == 201, overdue_response.text
    overdue_invoice = overdue_response.json()["item"]
    assert overdue_invoice["price_application_kind"] == "renewal"
    assert overdue_invoice["total_amount"] == "590.00"

    await db_session.execute(
        text(
            "UPDATE public.tenant_subscription "
            "SET period_end = :period_end WHERE tenant_id = :tenant_id AND id = :subscription_id"
        ),
        {
            "period_end": utc_now() + timedelta(days=1),
            "tenant_id": tenant_id,
            "subscription_id": subscription.id,
        },
    )
    await db_session.refresh(subscription)
    current_response = await platform_client.post(
        (
            f"/api/v1/admin/tenants/{tenant_id}/subscriptions/{subscription.id}"
            "/financial-invoices"
        ),
        headers=_headers(approver_token),
        json={
            "operation_id": str(uuid4()),
            "expected_row_version": subscription.row_version,
        },
    )
    assert current_response.status_code == 201, current_response.text
    current_invoice = current_response.json()["item"]
    assert current_invoice["price_application_kind"] == "renewal"

    overdue_due_at = datetime.fromisoformat(str(overdue_invoice["due_at"]))
    wait_for_due = (overdue_due_at - utc_now()).total_seconds() + 0.2
    if wait_for_due > 0:
        await asyncio.sleep(wait_for_due)

    covering_payment = await _review_and_approve(
        platform_client,
        tenant_id=tenant_id,
        invoice_id=str(current_invoice["invoice_id"]),
        amount="1300.00",
        reference="RENEWAL0002",
        reviewer_token=reviewer_token,
        approver_token=approver_token,
    )
    assert covering_payment.status_code == 200, covering_payment.text
    payment = covering_payment.json()["item"]
    assert payment["allocated_amount"] == "1180.00"
    assert payment["credit_amount"] == "120.00"
    assert payment["blocking_outstanding_amount"] == "0.00"
    assert [item["invoice_id"] for item in payment["allocations"]] == [
        overdue_invoice["invoice_id"],
        current_invoice["invoice_id"],
    ]
    assert payment["access_restored"] is True

    account = await _financial_account(
        platform_client,
        tenant_id=tenant_id,
        token=approver_token,
    )
    assert account["outstanding_amount"] == "0.00"
    assert account["credit_balance"] == "120.00"
    assert account["journal_balanced"] is True
    assert len(account["invoices"]) == 3
    assert len({item["invoice_number"] for item in account["invoices"]}) == 3
