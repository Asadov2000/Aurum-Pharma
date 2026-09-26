"""Cash approval posts real ledger entries without restoring access/subscriptions."""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.time import utc_now
from tests.auth_helpers import create_session_access_token, create_support_access_token
from tests.domains.billing.historical import seed_historical_account
from tests.domains.billing.test_financial_kernel import _financial_account, _headers
from tests.domains.billing.test_subscription_pricing import _platform_identity
from tests.domains.billing.test_tenant_financial_account import _seed_committed_subjects


@pytest_asyncio.fixture
async def cash_runtime_client(client: AsyncClient) -> AsyncIterator[AsyncClient]:
    """Keep the real request boundary, closing its pools before this loop ends."""
    from app.core.db import app_engine, support_engine

    try:
        yield client
    finally:
        # Unlike platform_client, this test exercises real independently committed
        # requests. Pooled asyncpg connections must not survive its per-test loop.
        await asyncio.gather(app_engine.dispose(), support_engine.dispose())


async def _create(
    client: AsyncClient,
    tenant: UUID,
    token: str,
    *,
    invoice: UUID | None = None,
    amount: str = "125.00",
) -> dict[str, object]:
    payload = {
        "operation_id": str(uuid4()),
        "amount": amount,
        "paid_at": (utc_now() - timedelta(minutes=1)).isoformat(),
        "purpose": "Аванс за обслуживание",
        "target_invoice_id": str(invoice) if invoice else None,
    }
    path = f"/api/v1/admin/billing/tenants/{tenant}/cash-payments"
    response = await client.post(path, headers=_headers(token), json=payload)
    assert response.status_code == 201, response.text
    replay = await client.post(path, headers=_headers(token), json=payload)
    assert replay.status_code == 201, replay.text
    assert replay.json() == response.json()
    return response.json()


async def test_cash_advance_requires_second_actor_and_has_immutable_receipt(
    db_session: AsyncSession,
    platform_client: AsyncClient,
    maintenance_engine: AsyncEngine,
) -> None:
    tenant, _, _ = await seed_historical_account(maintenance_engine)
    _, receiver = await _platform_identity(db_session)
    _, approver = await _platform_identity(db_session)
    base = f"/api/v1/admin/billing/tenants/{tenant}/cash-payments"
    before = await _financial_account(platform_client, tenant_id=tenant, token=receiver)
    doc = await _create(platform_client, tenant, receiver)
    assert str(doc["document_number"]).startswith("AC-")
    assert doc["status"] == "pending_approval" and doc["can_approve"] is False
    pending = await _financial_account(platform_client, tenant_id=tenant, token=receiver)
    assert pending == before
    receipt = await platform_client.get(f"{base}/{doc['id']}/receipt", headers=_headers(receiver))
    assert receipt.status_code == 404
    decision = {"operation_id": str(uuid4()), "expected_row_version": 1}
    own = await platform_client.post(
        f"{base}/{doc['id']}/approve", headers=_headers(receiver), json=decision
    )
    assert own.status_code == 422
    response = await platform_client.post(
        f"{base}/{doc['id']}/approve", headers=_headers(approver), json=decision
    )
    assert response.status_code == 200, response.text
    assert response.json()["credit_amount"] == "125.00"
    assert response.json()["allocated_amount"] == "0.00"
    replay = await platform_client.post(
        f"{base}/{doc['id']}/approve", headers=_headers(approver), json=decision
    )
    assert replay.status_code == 200 and replay.json() == response.json()
    receipt = await platform_client.get(f"{base}/{doc['id']}/receipt", headers=_headers(receiver))
    assert receipt.status_code == 200, receipt.text
    assert receipt.headers["cache-control"] == "private, no-store"
    assert receipt.json()["amount"] == "125.00" and receipt.json()["allocations"] == []
    after = await _financial_account(platform_client, tenant_id=tenant, token=receiver)
    assert after["outstanding_amount"] == before["outstanding_amount"]
    assert after["credit_balance"] == "125.00" and after["journal_balanced"] is True
    assert (
        await db_session.scalar(
            text("SELECT status FROM public.tenant WHERE id=:tenant"), {"tenant": tenant}
        )
        == "readonly"
    )
    assert (
        await db_session.scalar(
            text("SELECT mode FROM public.tenant_access_policy WHERE tenant_id=:tenant"),
            {"tenant": tenant},
        )
        == "readonly"
    )
    payment = after["payments"][0]
    assert payment["payment_method"] == "cash" and payment["can_adjust"] is False
    adjustment = await platform_client.post(
        f"/api/v1/admin/billing/tenants/{tenant}/payments/{payment['payment_id']}/adjustments",
        headers=_headers(receiver),
        json={
            "operation_id": str(uuid4()),
            "adjustment_kind": "correction",
            "amount": "1.00",
            "reason_code": "amount_correction",
            "reason_note": "Cash correction must use separate procedure",
        },
    )
    assert adjustment.status_code == 422, adjustment.text


async def test_cash_targets_only_selected_historical_invoice(
    db_session: AsyncSession,
    platform_client: AsyncClient,
    maintenance_engine: AsyncEngine,
) -> None:
    tenant, _, invoices = await seed_historical_account(maintenance_engine)
    invoice = invoices[0]
    _, receiver = await _platform_identity(db_session)
    _, approver = await _platform_identity(db_session)
    doc = await _create(platform_client, tenant, receiver, invoice=invoice, amount="615.00")
    base = f"/api/v1/admin/billing/tenants/{tenant}/cash-payments"
    response = await platform_client.post(
        f"{base}/{doc['id']}/approve",
        headers=_headers(approver),
        json={"operation_id": str(uuid4()), "expected_row_version": 1},
    )
    assert response.status_code == 200, response.text
    assert response.json()["allocated_amount"] == "590.00"
    assert response.json()["credit_amount"] == "25.00"
    receipt = await platform_client.get(f"{base}/{doc['id']}/receipt", headers=_headers(approver))
    assert receipt.json()["allocations"][0]["invoice_id"] == str(invoice)


async def test_cash_foreign_scope_version_rejection_and_stale_mfa(
    db_session: AsyncSession,
    platform_client: AsyncClient,
    maintenance_engine: AsyncEngine,
) -> None:
    tenant, _, _ = await seed_historical_account(maintenance_engine)
    other, _, other_invoices = await seed_historical_account(maintenance_engine)
    _, receiver = await _platform_identity(db_session)
    approver_user, approver = await _platform_identity(db_session)
    doc = await _create(platform_client, tenant, receiver)
    base = f"/api/v1/admin/billing/tenants/{tenant}/cash-payments"
    decision = {"operation_id": str(uuid4()), "expected_row_version": 2}
    wrong_version = await platform_client.post(
        f"{base}/{doc['id']}/approve", headers=_headers(approver), json=decision
    )
    assert wrong_version.status_code == 409
    foreign = await platform_client.post(
        f"/api/v1/admin/billing/tenants/{other}/cash-payments/{doc['id']}/approve",
        headers=_headers(approver),
        json={**decision, "expected_row_version": 1},
    )
    assert foreign.status_code == 404
    foreign_invoice = await platform_client.post(
        base,
        headers=_headers(receiver),
        json={
            "operation_id": str(uuid4()),
            "amount": "1.00",
            "paid_at": utc_now().isoformat(),
            "purpose": "Foreign invoice",
            "target_invoice_id": str(other_invoices[0]),
        },
    )
    assert foreign_invoice.status_code == 404
    stale = await create_support_access_token(
        db_session, approver_user, mfa_verified_at=utc_now() - timedelta(hours=1)
    )
    denied = await platform_client.post(
        f"{base}/{doc['id']}/approve",
        headers=_headers(stale),
        json={**decision, "expected_row_version": 1},
    )
    assert denied.status_code == 403
    rejected = await platform_client.post(
        f"{base}/{doc['id']}/reject",
        headers=_headers(approver),
        json={**decision, "expected_row_version": 1, "reason": "Ошибка регистрации наличных"},
    )
    assert rejected.status_code == 200, rejected.text
    receipt = await platform_client.get(f"{base}/{doc['id']}/receipt", headers=_headers(receiver))
    assert receipt.status_code == 404
    account = await _financial_account(platform_client, tenant_id=tenant, token=receiver)
    assert account["payments"] == []


@pytest.mark.parametrize(
    "amount", ["NaN", "Infinity", "0.00", "-1.00", "1.001", "1000000000000.00"]
)
async def test_cash_rejects_invalid_money(
    db_session: AsyncSession,
    platform_client: AsyncClient,
    amount: str,
) -> None:
    _, token = await _platform_identity(db_session)
    result = await platform_client.post(
        f"/api/v1/admin/billing/tenants/{uuid4()}/cash-payments",
        headers=_headers(token),
        json={
            "operation_id": str(uuid4()),
            "amount": amount,
            "paid_at": utc_now().isoformat(),
            "purpose": "Аванс",
        },
    )
    assert result.status_code == 422


async def test_cash_tables_are_private_and_forced_rls(maintenance_engine: AsyncEngine) -> None:
    async with maintenance_engine.connect() as conn:
        assert await conn.scalar(text("""
          SELECT relrowsecurity AND relforcerowsecurity FROM pg_class
          WHERE oid='public.billing_cash_document'::regclass
        """))
        for role in ("aurum_app", "aurum_support", "aurum_edge_cash_executor"):
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert not await conn.scalar(
                    text(
                        "SELECT has_table_privilege("
                        ":role,'public.billing_cash_document',:privilege)"
                    ),
                    {"role": role, "privilege": privilege},
                )


async def test_cash_concurrent_confirmation_tenant_boundary_and_document_snapshot(
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    cash_runtime_client: AsyncClient,
) -> None:
    client = cash_runtime_client
    # Immutable committed history belongs to this disposable database and is not deleted.
    subjects = await _seed_committed_subjects(db_engine)
    other = await _seed_committed_subjects(db_engine)
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory.begin() as session:
        _, receiver = await _platform_identity(session)
        _, first = await _platform_identity(session)
        _, second = await _platform_identity(session)
        owner = await create_session_access_token(
            session, user_id=subjects.owner_user_id, tenant_id=subjects.tenant_id
        )
        seller = await create_session_access_token(
            session, user_id=subjects.seller_user_id, tenant_id=subjects.tenant_id
        )
        foreign_owner = await create_session_access_token(
            session, user_id=other.owner_user_id, tenant_id=other.tenant_id
        )
    doc = await _create(client, subjects.tenant_id, receiver)
    own_list = await client.get("/api/v1/billing/cash-payments", headers=_headers(owner))
    assert own_list.status_code == 200 and own_list.json()["items"] == []
    path = f"/api/v1/admin/billing/tenants/{subjects.tenant_id}/cash-payments/{doc['id']}/approve"
    responses = await asyncio.wait_for(
        asyncio.gather(
            *[
                client.post(
                    path,
                    headers=_headers(token),
                    json={
                        "operation_id": str(uuid4()),
                        "expected_row_version": 1,
                    },
                )
                for token in (first, second)
            ]
        ),
        timeout=20,
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    own_list = await client.get("/api/v1/billing/cash-payments", headers=_headers(owner))
    assert own_list.status_code == 200, own_list.text
    assert own_list.json()["total"] == 1
    assert own_list.json()["items"][0]["can_approve"] is False
    tenant_path = f"/api/v1/billing/cash-payments/{doc['id']}/receipt"
    receipt = await client.get(tenant_path, headers=_headers(owner))
    assert receipt.status_code == 200
    for token, expected in ((seller, 403), (foreign_owner, 404)):
        denied = await client.get(tenant_path, headers=_headers(token))
        assert denied.status_code == expected, denied.text
    async with maintenance_engine.begin() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM public.billing_payment WHERE cash_document_id=:id"),
                {"id": UUID(str(doc["id"]))},
            )
            == 1
        )
        payment = (
            await connection.execute(
                text(
                    "SELECT review_id,recipient_account_key,external_reference,payment_method "
                    "FROM public.billing_payment WHERE cash_document_id=:id"
                ),
                {"id": UUID(str(doc["id"]))},
            )
        ).one()
        assert tuple(payment) == (None, None, None, "cash")
        await connection.execute(
            text("UPDATE public.tenant SET name='Renamed after receipt' WHERE id=:id"),
            {"id": subjects.tenant_id},
        )
        for statement in (
            "UPDATE public.billing_cash_document SET purpose='Edited' WHERE id=:id",
            "DELETE FROM public.billing_cash_document WHERE id=:id",
        ):
            with pytest.raises(DBAPIError) as error:
                async with connection.begin_nested():
                    await connection.execute(text(statement), {"id": UUID(str(doc["id"]))})
            assert error.value.orig.sqlstate == "55000"
    reread = await client.get(tenant_path, headers=_headers(owner))
    assert reread.json() == receipt.json()


async def test_cash_confirmation_rolls_back_ledger_receipt_and_idempotency_together(
    db_session: AsyncSession,
    platform_client: AsyncClient,
    maintenance_engine: AsyncEngine,
) -> None:
    tenant, _, _ = await seed_historical_account(maintenance_engine)
    _, receiver = await _platform_identity(db_session)
    _, approver = await _platform_identity(db_session)
    doc = await _create(platform_client, tenant, receiver)
    base = f"/api/v1/admin/billing/tenants/{tenant}/cash-payments"
    decision = {"operation_id": str(uuid4()), "expected_row_version": 1}
    async with db_session.begin_nested() as transaction:
        result = await platform_client.post(
            f"{base}/{doc['id']}/approve", headers=_headers(approver), json=decision
        )
        assert result.status_code == 200, result.text
        account = await _financial_account(platform_client, tenant_id=tenant, token=receiver)
        assert account["credit_balance"] == "125.00"
        await transaction.rollback()
    account = await _financial_account(platform_client, tenant_id=tenant, token=receiver)
    assert account["credit_balance"] == "0.00" and account["payments"] == []
    receipt = await platform_client.get(f"{base}/{doc['id']}/receipt", headers=_headers(receiver))
    assert receipt.status_code == 404
    result = await platform_client.post(
        f"{base}/{doc['id']}/approve", headers=_headers(approver), json=decision
    )
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "confirmed"


async def test_cash_balance_limit_rejects_without_partial_payment(
    db_session: AsyncSession,
    platform_client: AsyncClient,
    maintenance_engine: AsyncEngine,
) -> None:
    tenant, _, _ = await seed_historical_account(maintenance_engine)
    _, receiver = await _platform_identity(db_session)
    _, approver = await _platform_identity(db_session)
    base = f"/api/v1/admin/billing/tenants/{tenant}/cash-payments"
    for amount, expected in (("999999999999.99", 200), ("1.00", 422)):
        doc = await _create(platform_client, tenant, receiver, amount=amount)
        result = await platform_client.post(
            f"{base}/{doc['id']}/approve",
            headers=_headers(approver),
            json={"operation_id": str(uuid4()), "expected_row_version": 1},
        )
        assert result.status_code == expected, result.text
    account = await _financial_account(platform_client, tenant_id=tenant, token=receiver)
    assert account["credit_balance"] == "999999999999.99"
    assert len(account["payments"]) == 1 and account["journal_balanced"] is True
