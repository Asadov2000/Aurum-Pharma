"""Legacy billing writers stay unavailable after the ledger cutover begins."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.time import utc_now
from app.tasks.celery_app import celery_app


async def test_legacy_financial_write_routes_are_not_registered(
    platform_client: AsyncClient,
) -> None:
    tenant_id = uuid4()
    invoice_id = uuid4()
    routes = (
        f"/api/v1/admin/tenants/{tenant_id}/subscription",
        f"/api/v1/admin/tenants/{tenant_id}/invoices",
        f"/api/v1/admin/tenants/{tenant_id}/invoices/{invoice_id}/payments",
    )

    for route in routes:
        response = await platform_client.post(route, json={})
        assert response.status_code in (404, 405), route


def test_legacy_invoice_generator_is_not_scheduled() -> None:
    scheduled_tasks = {
        str(entry["task"])
        for entry in celery_app.conf.beat_schedule.values()
        if isinstance(entry, dict) and "task" in entry
    }

    assert "billing.generate_monthly_invoices" not in scheduled_tasks


async def test_legacy_archive_cannot_be_deleted_through_tenant_cascade(
    maintenance_engine: AsyncEngine,
) -> None:
    tenant_id = uuid4()
    subscription_id = uuid4()
    invoice_id = uuid4()
    now = utc_now()

    async with maintenance_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            plan_id = await connection.scalar(
                text("SELECT id FROM public.subscription_plan ORDER BY created_at LIMIT 1")
            )
            assert plan_id is not None
            await connection.execute(
                text(
                    "INSERT INTO public.tenant (id, name, contact_email, status) "
                    "VALUES (:id, :name, :email, 'active')"
                ),
                {
                    "id": tenant_id,
                    "name": f"Legacy archive guard {tenant_id.hex[:8]}",
                    "email": f"legacy-guard-{tenant_id.hex[:8]}@example.test",
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO public.tenant_subscription ("
                    "id, tenant_id, plan_id, status, billing_period, period_start, "
                    "period_end, branches_count, amount) VALUES ("
                    ":id, :tenant_id, :plan_id, 'active', 'monthly', :period_start, "
                    ":period_end, 1, :amount)"
                ),
                {
                    "id": subscription_id,
                    "tenant_id": tenant_id,
                    "plan_id": plan_id,
                    "period_start": now,
                    "period_end": now + timedelta(days=365),
                    "amount": Decimal("100.00"),
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO public.invoice ("
                    "id, tenant_id, subscription_id, invoice_number, due_at, amount) "
                    "VALUES (:id, :tenant_id, :subscription_id, :number, :due_at, :amount)"
                ),
                {
                    "id": invoice_id,
                    "tenant_id": tenant_id,
                    "subscription_id": subscription_id,
                    "number": f"LEGACY-GUARD-{invoice_id.hex}",
                    "due_at": now,
                    "amount": Decimal("100.00"),
                },
            )
            await connection.execute(
                text(
                    "INSERT INTO public.payment ("
                    "tenant_id, invoice_id, amount, method, paid_at) "
                    "VALUES (:tenant_id, :invoice_id, :amount, 'bank_transfer', :paid_at)"
                ),
                {
                    "tenant_id": tenant_id,
                    "invoice_id": invoice_id,
                    "amount": Decimal("100.00"),
                    "paid_at": now,
                },
            )
            await connection.execute(text("SET LOCAL ROLE aurum_support"))
            await connection.execute(text("SELECT set_config('app.support_session', 'true', true)"))

            with pytest.raises(DBAPIError) as exc_info:
                await connection.execute(
                    text("DELETE FROM public.tenant WHERE id = :tenant_id"),
                    {"tenant_id": tenant_id},
                )

            assert getattr(exc_info.value.orig, "sqlstate", None) == "23503"
        finally:
            await transaction.rollback()
