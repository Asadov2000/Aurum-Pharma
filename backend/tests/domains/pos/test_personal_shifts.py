"""Personal register history is narrower than reports and independent of current roles."""

from collections.abc import AsyncIterator
from decimal import Decimal
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.domains.auth.models import AppUser
from app.domains.foundation.repository import FoundationRepository
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.roles.models import TenantMembership
from app.main import app
from tests.auth_helpers import create_support_access_token, create_tenant_access_token
from tests.platform_access_helpers import create_test_platform_user


@pytest_asyncio.fixture
async def personal_client(
    db_session: AsyncSession, client: AsyncClient
) -> AsyncIterator[AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = override
    try:
        yield client
    finally:
        app.dependency_overrides.pop(get_db, None)


async def _login(db: AsyncSession, user: AppUser, *, membership_status: str = "active") -> str:
    db.add(
        TenantMembership(
            tenant_id=user.home_tenant_id,
            user_id=user.id,
            full_name=user.full_name,
            status=membership_status,
        )
    )
    await db.flush()
    await db.execute(
        text(
            "SELECT set_config('app.support_session', 'false', true), "
            "set_config('app.support_access_session_id', '', true), "
            "set_config('app.auth_session_id', '', true), "
            "set_config('app.user_id', :actor, true), "
            "set_config('app.tenant_id', :tenant, true)"
        ),
        {"actor": str(user.id), "tenant": str(user.home_tenant_id)},
    )
    return await create_tenant_access_token(db, user)


async def test_personal_shift_requires_login(personal_client: AsyncClient) -> None:
    assert (await personal_client.get("/api/v1/shifts/mine")).status_code == 401


@pytest.mark.parametrize("membership_status", ["pending", "suspended", "offboarded"])
async def test_inactive_membership_cannot_read_own_history(
    db_session: AsyncSession,
    personal_client: AsyncClient,
    pos_scaffold,
    membership_status: str,
) -> None:
    scaffold = await pos_scaffold()
    token = await _login(db_session, scaffold["cashier"], membership_status=membership_status)
    response = await personal_client.get(
        "/api/v1/shifts/mine", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "authentication_required"


async def test_own_history_is_private_and_available_without_report_permission(
    db_session: AsyncSession, personal_client: AsyncClient, pos_scaffold
) -> None:
    scaffold = await pos_scaffold()
    foreign = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    own = await service.open_shift(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        opened_by_user_id=scaffold["cashier"].id,
        opening_cash=Decimal("100"),
    )
    await service.close_shift(
        shift_id=own.id,
        closing_cash_actual=Decimal("95"),
        closed_by_user_id=scaffold["cashier"].id,
    )
    colleague = AppUser(
        email=f"colleague-{uuid4().hex}@example.invalid",
        full_name="Other cashier",
        home_tenant_id=scaffold["tenant"].id,
        status="active",
    )
    db_session.add(colleague)
    await db_session.flush()
    await service.open_shift(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        opened_by_user_id=colleague.id,
        opening_cash=Decimal("200"),
    )
    await service.open_shift(
        tenant_id=foreign["tenant"].id,
        register_id=foreign["register"].id,
        opened_by_user_id=foreign["cashier"].id,
        opening_cash=Decimal("300"),
    )
    settings = await FoundationRepository(db_session).get_settings(scaffold["tenant"].id)
    assert settings is not None
    settings.report_timezone = "Europe/Moscow"
    await db_session.flush()
    token = await _login(db_session, scaffold["cashier"])
    headers = {"Authorization": f"Bearer {token}"}
    response = await personal_client.get(
        "/api/v1/shifts/mine",
        headers=headers,
        params={"cashier_id": str(colleague.id), "tenant_id": str(foreign["tenant"].id)},
    )
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["timezone"] == "Europe/Moscow"
    assert payload["total"] == 1
    assert payload["page"] == 1
    assert payload["page_size"] == 25
    assert payload["items"][0]["id"] == str(own.id)
    assert set(payload["items"][0]) == {
        "id",
        "branch_id",
        "branch_name",
        "register_id",
        "register_name",
        "opened_at",
        "closed_at",
        "status",
        "sales_total",
        "returns_total",
        "currency",
    }
    assert (await personal_client.get("/api/v1/shifts", headers=headers)).status_code == 403
    assert (
        await personal_client.get(f"/api/v1/shifts/{own.id}/z-report", headers=headers)
    ).status_code == 403


async def test_open_personal_shift_uses_live_sales_and_returns_not_cached_counts(
    db_session: AsyncSession, pos_scaffold
) -> None:
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    shift = await service.open_shift(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        opened_by_user_id=scaffold["cashier"].id,
        opening_cash=Decimal("0"),
    )
    sale = await service.create_sale(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        cashier_user_id=scaffold["cashier"].id,
    )
    items, _ = await service.add_item(
        sale_id=sale.id, catalog_id=scaffold["item"].id, qty=Decimal("2")
    )
    await service.add_payment(sale_id=sale.id, payment_method="cash", amount=Decimal("20"))
    await service.complete(sale_id=sale.id)
    await service.refund(
        parent_sale_id=sale.id,
        items=[(items[0].id, Decimal("1"))],
        reason="quality_issue",
        comment=None,
        cashier_user_id=scaffold["cashier"].id,
    )
    result = await service.list_personal_shifts(
        tenant_id=scaffold["tenant"].id,
        user_id=scaffold["cashier"].id,
        page=1,
        page_size=25,
    )
    assert result.items[0].id == shift.id
    assert result.items[0].status == "open"
    assert result.items[0].sales_total == Decimal("20")
    assert result.items[0].returns_total == Decimal("10")
    assert "sales_count" not in result.items[0].model_dump()


async def test_personal_history_paginates_and_survives_inactive_branch(
    db_session: AsyncSession, personal_client: AsyncClient, pos_scaffold
) -> None:
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    ids = []
    for _ in range(2):
        shift = await service.open_shift(
            tenant_id=scaffold["tenant"].id,
            register_id=scaffold["register"].id,
            opened_by_user_id=scaffold["cashier"].id,
            opening_cash=Decimal("0"),
        )
        await service.close_shift(
            shift_id=shift.id,
            closing_cash_actual=Decimal("0"),
            closed_by_user_id=scaffold["cashier"].id,
        )
        ids.append(str(shift.id))
    scaffold["branch"].is_active = False
    await db_session.flush()
    token = await _login(db_session, scaffold["cashier"])
    headers = {"Authorization": f"Bearer {token}"}
    pages = [
        await personal_client.get(
            "/api/v1/shifts/mine", headers=headers, params={"page": page, "page_size": 1}
        )
        for page in (1, 2, 3)
    ]
    assert all(response.status_code == 200 for response in pages)
    assert all(response.json()["total"] == 2 for response in pages)
    assert {pages[0].json()["items"][0]["id"], pages[1].json()["items"][0]["id"]} == set(ids)
    assert pages[2].json()["items"] == []
    for invalid in ({"page": 0}, {"page_size": 0}, {"page_size": 51}):
        response = await personal_client.get("/api/v1/shifts/mine", headers=headers, params=invalid)
        assert response.status_code == 422


@pytest.mark.parametrize("kind", ["developer", "administrator"])
async def test_platform_account_cannot_read_personal_tenant_shifts(
    db_session: AsyncSession, personal_client: AsyncClient, kind: str
) -> None:
    actor = await create_test_platform_user(db_session, access_kind=kind)
    token = await create_support_access_token(db_session, actor)
    response = await personal_client.get(
        "/api/v1/shifts/mine", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 403
