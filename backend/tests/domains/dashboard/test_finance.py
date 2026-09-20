"""Financial data is read live with the caller's permissions, outside Redis."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import Response

from app.core.deps import CurrentUser
from app.domains.billing.service import BillingService
from app.domains.dashboard.router import get_summary
from app.domains.dashboard.schemas import DashboardSummary
from app.domains.dashboard.service import DashboardService


@pytest.mark.asyncio
async def test_finance_uses_live_ledger_and_never_enriches_shared_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared = DashboardSummary.model_validate(
        {
            "today": {
                "revenue": "0",
                "currency": "TJS",
                "receipts": 0,
                "active_shifts": 0,
                "cashiers_on_shift": 0,
            },
            "expiring": {"batches": [], "licenses": []},
            "finance": None,
            "checklist": {
                "draft_incoming_count": 0,
                "closed_shifts_count": 0,
                "latest_closed_shift_id": None,
            },
            "generated_at": datetime.now(UTC),
        }
    )
    service = DashboardService(MagicMock())
    monkeypatch.setattr(service, "get_summary", AsyncMock(return_value=shared))
    tenant_id = uuid4()
    user = CurrentUser(
        user_id=uuid4(),
        tenant_id=tenant_id,
        is_developer=False,
        is_administrator=False,
        permissions={"billing.overview.view", "billing.invoice.view"},
        permission_scopes={"billing.overview.view": None, "billing.invoice.view": None},
    )
    now = datetime.now(UTC).isoformat()
    invoice = {
        "invoice_id": str(uuid4()),
        "invoice_number": "TEST-1",
        "document_state": "issued",
        "settlement_state": "partially_paid",
        "collection_state": "overdue",
        "period_start": now,
        "period_end": now,
        "due_at": now,
        "issued_at": now,
        "total_amount": "590.00",
        "outstanding_amount": "390.00",
        "currency": "TJS",
    }
    account = {
        "subscription": None,
        "currency": "TJS",
        "outstanding_amount": "390.00",
        "credit_balance": "0",
        "invoices": [invoice],
        "payments": [],
    }
    read = AsyncMock(return_value=account)
    monkeypatch.setattr(BillingService, "read_tenant_financial_account", read)
    response = Response()
    first = await get_summary(response, user, service, False)
    assert first.finance is not None
    assert str(first.finance.open_invoices_total) == "390.00"
    assert first.finance.open_invoices_count == 1
    assert first.finance.has_overdue is True
    assert shared.finance is None
    assert response.headers["cache-control"] == "private, no-store"
    read.assert_awaited_once_with(actor_user_id=user.user_id, tenant_id=tenant_id)

    # A subsequent payment is visible even when operational summary is cached.
    account["outstanding_amount"] = "0.00"
    invoice.update(outstanding_amount="0.00", settlement_state="paid", collection_state="not_due")
    second = await get_summary(Response(), user, service, False)
    assert second.finance is not None
    assert second.finance.open_invoices_count == 0
    assert second.finance.has_overdue is False
    assert str(second.finance.open_invoices_total) == "0.00"

    # Same tenant and cached object, but branch-only billing permission is insufficient.
    user.permission_scopes["billing.invoice.view"] = frozenset({uuid4()})
    hidden = await get_summary(Response(), user, service, False)
    assert hidden.finance is None
    assert read.await_count == 2
    assert shared.finance is None
