"""HTTP report boundaries: branch grants, export capability and tenant-local dates."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, current_user, get_db
from app.domains.pos.repository import POSRepository
from app.domains.pos.router import _service
from app.domains.pos.service import POSService
from app.main import app


@pytest.mark.parametrize("export", [False, True])
async def test_report_routes_keep_branch_scope_with_export_permission(
    client: AsyncClient, db_session: AsyncSession, pos_scaffold, export: bool
) -> None:
    scaffold = await pos_scaffold()
    service = POSService(POSRepository(db_session))
    shift = await service.open_shift(
        tenant_id=scaffold["tenant"].id,
        register_id=scaffold["register"].id,
        opened_by_user_id=scaffold["cashier"].id,
        opening_cash=Decimal("0"),
    )
    allowed_branch = uuid4()
    actor = CurrentUser(
        user_id=scaffold["cashier"].id,
        tenant_id=scaffold["tenant"].id,
        is_developer=False,
        is_administrator=False,
        permissions={"reports.view", "reports.export"},
        permission_scopes={
            "reports.view": frozenset({allowed_branch}),
            "reports.export": None,
        },
    )

    async def override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def override_user() -> CurrentUser:
        return actor

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = override_user
    try:
        suffix = ".xlsx" if export else ""
        params = {"branch_id": str(scaffold["branch"].id)}
        sales = await client.get(
            f"/api/v1/reports/sales-summary{suffix}",
            params={**params, "from": "2026-01-01", "to": "2026-01-31"},
        )
        stock = await client.get(
            f"/api/v1/reports/stock-on-date{suffix}",
            params={**params, "date": "2026-01-31"},
        )
        shift_report = await client.get(f"/api/v1/shifts/{shift.id}/z-report{suffix}")
        assert sales.status_code == 403
        assert stock.status_code == 403
        assert shift_report.status_code == 403

        actor.permissions = {"reports.view"}
        actor.permission_scopes = {"reports.view": None}
        if export:
            missing_export = await client.get(
                "/api/v1/reports/stock-on-date.xlsx", params={"date": "2026-01-31"}
            )
            assert missing_export.status_code == 403
        else:
            visible = await client.get(
                "/api/v1/reports/stock-on-date", params={"date": "2026-01-31"}
            )
            assert visible.status_code == 200
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(current_user, None)


async def test_stock_routes_default_to_tenant_local_date(
    client: AsyncClient, db_session: AsyncSession, pos_scaffold
) -> None:
    scaffold = await pos_scaffold()
    actor = CurrentUser(
        user_id=scaffold["cashier"].id,
        tenant_id=scaffold["tenant"].id,
        is_developer=False,
        is_administrator=False,
        permissions={"reports.view", "reports.export"},
        permission_scopes={"reports.view": None, "reports.export": None},
    )
    service = POSService(
        POSRepository(db_session), now=lambda: datetime(2026, 1, 1, 21, 0, tzinfo=UTC)
    )

    async def override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def override_user() -> CurrentUser:
        return actor

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = override_user
    app.dependency_overrides[_service] = lambda: service
    try:
        overview = await client.get("/api/v1/reports/stock-on-date")
        assert overview.status_code == 200
        assert overview.json()["on_date"] == "2026-01-02"
        workbook = await client.get("/api/v1/reports/stock-on-date.xlsx")
        assert workbook.status_code == 200
        assert "stock-2026-01-02.xlsx" in workbook.headers["content-disposition"]
        explicit = await client.get("/api/v1/reports/stock-on-date", params={"date": "2025-12-31"})
        assert explicit.status_code == 200
        assert explicit.json()["on_date"] == "2025-12-31"
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(current_user, None)
        app.dependency_overrides.pop(_service, None)
