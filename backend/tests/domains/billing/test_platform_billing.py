"""Read-only platform billing workspace."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.time import utc_now
from app.domains.billing.models import (
    Invoice,
    Payment,
    SubscriptionPlan,
    TenantSubscription,
)
from app.domains.billing.repository import (
    BillingRepository,
    PlatformBillingOverview,
    PlatformInvoiceRecord,
)
from app.domains.billing.service import BillingService
from app.domains.foundation.models import Tenant
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = utc_now()
    tenant_name = f"Legacy archive {uuid4().hex[:8]}"
    contact_email = f"legacy-{uuid4().hex[:8]}@example.test"
    invoice = Invoice(
        id=uuid4(),
        tenant_id=uuid4(),
        subscription_id=uuid4(),
        invoice_number=f"LEGACY-{uuid4().hex}",
        issued_at=now - timedelta(days=10),
        due_at=now - timedelta(days=1),
        amount=Decimal("100.00"),
        currency="TJS",
        discount_amount=Decimal("0.00"),
        status="pending",
        created_at=now - timedelta(days=10),
        updated_at=now - timedelta(days=10),
    )

    async def _overview(_service: BillingService) -> PlatformBillingOverview:
        return PlatformBillingOverview(
            tenants_total=1,
            active_subscriptions=1,
            attention_subscriptions=1,
            open_invoices=1,
            overdue_invoices=1,
            outstanding_amount=Decimal("60.00"),
        )

    async def _invoices(
        _service: BillingService,
        *,
        query: str | None,
        status: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[PlatformInvoiceRecord], int]:
        del query, status, page, page_size
        return (
            [
                PlatformInvoiceRecord(
                    invoice=invoice,
                    tenant_name=tenant_name,
                    subscription_status="active",
                    paid_amount=Decimal("40.00"),
                    outstanding_amount=Decimal("60.00"),
                )
            ],
            1,
        )

    monkeypatch.setattr(BillingService, "get_platform_overview", _overview)
    monkeypatch.setattr(BillingService, "list_platform_invoices", _invoices)
    developer = await create_test_platform_user(db_session, access_kind="developer")
    token = await create_support_access_token(db_session, developer)

    overview = await platform_client.get(
        "/api/v1/admin/billing/overview",
        headers=_headers(token),
    )
    invoices = await platform_client.get(
        "/api/v1/admin/billing/invoices",
        params={"q": tenant_name, "status": "overdue", "page": 1, "page_size": 20},
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
    assert payload["items"][0]["tenant_name"] == tenant_name
    assert payload["items"][0]["invoice_number"] == invoice.invoice_number
    assert payload["items"][0]["paid_amount"] == "40.00"
    assert payload["items"][0]["outstanding_amount"] == "60.00"
    assert payload["items"][0]["status"] == "overdue"
    assert "tenant_id" not in payload["items"][0]
    assert "id" not in payload["items"][0]
    assert contact_email not in invoices.text


async def test_platform_billing_read_model_ignores_cross_tenant_payment_links(
    maintenance_engine: AsyncEngine,
) -> None:
    now = utc_now()
    invoice_tenant = Tenant(
        id=uuid4(),
        name=f"Legacy invoice tenant {uuid4().hex[:8]}",
        contact_email=f"invoice-{uuid4().hex[:8]}@example.test",
        status="active",
    )
    other_tenant = Tenant(
        id=uuid4(),
        name=f"Legacy payment tenant {uuid4().hex[:8]}",
        contact_email=f"payment-{uuid4().hex[:8]}@example.test",
        status="active",
    )
    plan = SubscriptionPlan(
        id=uuid4(),
        code=f"legacy_archive_{uuid4().hex}",
        name="Legacy archive fixture",
        price_per_branch=Decimal("100.00"),
    )
    subscription = TenantSubscription(
        id=uuid4(),
        tenant_id=invoice_tenant.id,
        plan_id=plan.id,
        status="active",
        billing_period="monthly",
        period_start=now,
        period_end=now + timedelta(days=30),
        branches_count=1,
        amount=Decimal("100.00"),
    )
    invoice = Invoice(
        id=uuid4(),
        tenant_id=invoice_tenant.id,
        subscription_id=subscription.id,
        invoice_number=f"LEGACY-{uuid4().hex}",
        issued_at=now,
        due_at=now + timedelta(days=7),
        amount=Decimal("100.00"),
    )
    payment = Payment(
        id=uuid4(),
        tenant_id=other_tenant.id,
        invoice_id=invoice.id,
        amount=Decimal("90.00"),
        method="bank_transfer",
        reference="CORRUPTED-CROSS-TENANT-LINK",
        paid_at=now,
    )

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            async with AsyncSession(bind=connection, expire_on_commit=False) as session:
                session.add_all([invoice_tenant, other_tenant, plan])
                await session.flush()
                session.add(subscription)
                await session.flush()
                session.add(invoice)
                await session.flush()
                session.add(payment)
                await session.flush()
                records, total = await BillingService(
                    BillingRepository(session)
                ).list_platform_invoices(
                    query=invoice.invoice_number,
                    status=None,
                    page=1,
                    page_size=20,
                )
        finally:
            await transaction.rollback()

    assert total == 1
    assert records[0].paid_amount == Decimal("0.00")
    assert records[0].outstanding_amount == Decimal("100.00")
