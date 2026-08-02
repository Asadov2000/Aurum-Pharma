"""Management search endpoints use exact permissions and private POST bodies."""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, current_user, get_db
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.roles.repository import RolesRepository
from app.domains.roles.service import RolesService
from app.domains.suppliers.repository import SuppliersRepository
from app.domains.suppliers.service import SuppliersService
from app.main import app


async def test_management_search_permissions_contracts_and_no_store(
    client: AsyncClient,
    db_session: AsyncSession,
    redis: Redis,
) -> None:
    suffix = uuid4().hex[:8]
    foundation = FoundationService(FoundationRepository(db_session))
    tenant = await foundation.create_tenant(
        payload={
            "name": f"Management search {suffix}",
            "contact_email": f"management-search-{suffix}@aurum.tj",
        }
    )
    branch = await foundation.create_branch(
        tenant_id=tenant.id,
        fields={"name": "Management Branch"},
    )
    await foundation.create_register(
        tenant_id=tenant.id,
        fields={"branch_id": branch.id, "name": "Management Register"},
    )
    await SuppliersService(SuppliersRepository(db_session)).create_supplier(
        tenant_id=tenant.id,
        fields={"name": "Management Supplier"},
    )
    await RolesService(RolesRepository(db_session)).create_tenant_account(
        tenant_id=tenant.id,
        email=f"management-user-{suffix}@aurum.tj",
        full_name="Management User",
    )

    actor = CurrentUser(
        user_id=uuid4(),
        tenant_id=tenant.id,
        is_developer=False,
        is_administrator=False,
        permissions={"pos.sell"},
        permission_scopes={"pos.sell": None},
    )

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_user() -> CurrentUser:
        return actor

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[current_user] = _override_user
    try:
        old_branches = await client.get("/api/v1/branches")
        old_registers = await client.get("/api/v1/registers")
        assert old_branches.status_code == 200
        assert old_registers.status_code == 200

        denied_paths = (
            "/api/v1/users/search",
            "/api/v1/branches/search",
            "/api/v1/registers/search",
            "/api/v1/suppliers/search",
        )
        for path in denied_paths:
            denied = await client.post(path, json={})
            assert denied.status_code == 403, path

        denied_options = await client.post("/api/v1/suppliers/options/search", json={})
        assert denied_options.status_code == 403

        actor.permissions = {"incoming.create"}
        actor.permission_scopes = {"incoming.create": None}
        incoming_options = await client.post(
            "/api/v1/suppliers/options/search",
            json={"q": "Management", "limit": 10},
        )
        assert incoming_options.status_code == 200
        assert incoming_options.headers["cache-control"] == "private, no-store"
        assert [item["name"] for item in incoming_options.json()["items"]] == [
            "Management Supplier"
        ]

        actor.permissions = {
            "users.view",
            "branches.view",
            "registers.view",
            "suppliers.view",
        }
        actor.permission_scopes = {
            "users.view": None,
            "branches.view": None,
            "registers.view": None,
            "suppliers.view": None,
        }
        expected_totals = {
            "/api/v1/users/search": 1,
            "/api/v1/branches/search": 1,
            "/api/v1/registers/search": 1,
            "/api/v1/suppliers/search": 1,
        }
        for path, expected_total in expected_totals.items():
            response = await client.post(path, json={"page": 1, "page_size": 10})
            assert response.status_code == 200, response.text
            assert response.headers["cache-control"] == "private, no-store"
            assert response.headers["pragma"] == "no-cache"
            assert response.json()["total"] == expected_total

        invalid_payloads = (
            ("/api/v1/users/search", {"unexpected": "value"}),
            ("/api/v1/branches/search", {"branch_type": "warehouse"}),
            ("/api/v1/registers/search", {"page": "1"}),
            ("/api/v1/suppliers/search", {"is_active": "true"}),
        )
        for path, payload in invalid_payloads:
            invalid = await client.post(path, json=payload)
            assert invalid.status_code == 422, path
    finally:
        app.dependency_overrides.pop(current_user, None)
        app.dependency_overrides.pop(get_db, None)
