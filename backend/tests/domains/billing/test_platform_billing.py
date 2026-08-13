"""Read-only platform billing workspace."""

from __future__ import annotations

from decimal import Decimal

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.domains.billing.repository import BillingRepository
from app.domains.billing.service import BillingService
from tests.auth_helpers import create_support_access_token
from tests.platform_access_helpers import create_test_platform_user


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_platform_billing_workspace_is_capability_gated(
    platform_client: AsyncClient,
) -> None:
    overview = await platform_client.get("/api/v1/admin/billing/overview")
    invoices = await platform_client.get("/api/v1/admin/billing/invoices")

    assert overview.status_code == 401
    assert invoices.status_code == 401


async def test_platform_billing_workspace_returns_safe_financial_read_model(
    db_session: AsyncSession,
    platform_client: AsyncClient,
    make_tenant_with_plan,
) -> None:
    tenant, plan = await make_tenant_with_plan()
    service = BillingService(BillingRepository(db_session))
    subscription = await service.create_subscription(
        tenant_id=tenant.id,
        plan_id=plan.id,
        billing_period="monthly",
        branches_count=1,
        status="active",
    )
    invoice = await service.create_invoice(
        tenant_id=tenant.id,
        subscription_id=subscription.id,
        amount=Decimal("100.00"),
        due_in_days=0,
    )
    await service.record_payment(
        tenant_id=tenant.id,
        invoice_id=invoice.id,
        amount=Decimal("40.00"),
        paid_at=utc_now(),
        method="bank_transfer",
        reference="TEST-PLATFORM-READ",
        notes=None,
        recorded_by=None,
    )
    developer = await create_test_platform_user(db_session, access_kind="developer")
    token = await create_support_access_token(db_session, developer)

    overview = await platform_client.get(
        "/api/v1/admin/billing/overview",
        headers=_headers(token),
    )
    invoices = await platform_client.get(
        "/api/v1/admin/billing/invoices",
        params={"q": tenant.name, "status": "overdue", "page": 1, "page_size": 20},
        headers=_headers(token),
    )

    assert overview.status_code == 200, overview.text
    assert overview.headers["cache-control"] == "private, no-store"
    overview_payload = overview.json()
    assert overview_payload["open_invoices"] >= 1
    assert Decimal(overview_payload["outstanding_amount"]) >= Decimal("60.00")

    assert invoices.status_code == 200, invoices.text
    assert invoices.headers["cache-control"] == "private, no-store"
    payload = invoices.json()
    assert payload["total"] == 1
    assert payload["items"][0]["tenant_name"] == tenant.name
    assert payload["items"][0]["invoice_number"] == invoice.invoice_number
    assert payload["items"][0]["paid_amount"] == "40.00"
    assert payload["items"][0]["outstanding_amount"] == "60.00"
    assert payload["items"][0]["status"] == "overdue"
    assert "tenant_id" not in payload["items"][0]
    assert "id" not in payload["items"][0]
    assert tenant.contact_email not in invoices.text


async def test_platform_billing_read_model_ignores_cross_tenant_payment_links(
    db_session: AsyncSession,
    make_tenant_with_plan,
) -> None:
    invoice_tenant, plan = await make_tenant_with_plan()
    other_tenant, _ = await make_tenant_with_plan()
    repository = BillingRepository(db_session)
    service = BillingService(repository)
    subscription = await service.create_subscription(
        tenant_id=invoice_tenant.id,
        plan_id=plan.id,
        billing_period="monthly",
        branches_count=1,
        status="active",
    )
    invoice = await service.create_invoice(
        tenant_id=invoice_tenant.id,
        subscription_id=subscription.id,
        amount=Decimal("100.00"),
        due_in_days=7,
    )

    # The legacy schema lacks a composed tenant FK. The global read model must
    # still refuse to count a corrupted cross-tenant link as money on the invoice.
    await repository.insert_payment(
        tenant_id=other_tenant.id,
        invoice_id=invoice.id,
        amount=Decimal("90.00"),
        method="bank_transfer",
        reference="CORRUPTED-CROSS-TENANT-LINK",
        paid_at=utc_now(),
        recorded_by=None,
        notes=None,
    )

    records, total = await service.list_platform_invoices(
        query=invoice.invoice_number,
        status=None,
        page=1,
        page_size=20,
    )

    assert total == 1
    assert records[0].paid_amount == Decimal("0.00")
    assert records[0].outstanding_amount == Decimal("100.00")
