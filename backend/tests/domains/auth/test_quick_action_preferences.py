"""Scoped quick action preferences preserve sidebar state and optimistic locking."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.roles.models import TenantMembership
from tests.domains.auth.test_preferences import _headers

DEFAULT_CATALOG = {
    "enabled": True,
    "order": ["open", "edit", "copy_name", "copy_barcode", "archive", "restore"],
    "hidden": [],
}
DEFAULT_BATCHES = {"enabled": True, "order": ["open", "copy_number"], "hidden": []}


async def test_quick_actions_merge_preserves_sidebar_sections_and_version(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,
) -> None:
    user = await make_user()
    headers = await _headers(db_session, user.id)
    catalog = {"enabled": False, "order": ["copy_name", "open", "edit"], "hidden": ["edit"]}
    saved = await auth_client.patch(
        "/api/v1/auth/preferences",
        headers=headers,
        json={
            "expected_version": 1,
            "workspace": {"favorite_routes": ["/catalog"], "start_route": "/catalog"},
            "quick_actions": {"catalog": catalog},
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["quick_actions"] == {"catalog": catalog, "batches": DEFAULT_BATCHES}
    assert saved.json()["version"] == 2
    sidebar = saved.json()["workspace"]

    batches = {"enabled": True, "order": ["copy_number", "open"], "hidden": ["open"]}
    changed_batches = await auth_client.patch(
        "/api/v1/auth/preferences",
        headers=headers,
        json={"expected_version": 2, "quick_actions": {"batches": batches}},
    )
    assert changed_batches.status_code == 200, changed_batches.text
    assert changed_batches.json()["workspace"] == sidebar
    assert changed_batches.json()["quick_actions"] == {"catalog": catalog, "batches": batches}

    old_client = await auth_client.patch(
        "/api/v1/auth/preferences",
        headers=headers,
        json={"expected_version": 3, "workspace": {"start_route": "/batches"}},
    )
    assert old_client.status_code == 200, old_client.text
    assert old_client.json()["workspace"]["start_route"] == "/batches"
    assert old_client.json()["quick_actions"] == {"catalog": catalog, "batches": batches}
    assert old_client.json()["version"] == 4

    stale = await auth_client.patch(
        "/api/v1/auth/preferences",
        headers=headers,
        json={"expected_version": 3, "quick_actions": {"catalog": DEFAULT_CATALOG}},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["details"] == {"current_version": 4}
    reread = await auth_client.get("/api/v1/auth/preferences", headers=headers)
    assert reread.status_code == 200, reread.text
    assert reread.json()["quick_actions"] == {"catalog": catalog, "batches": batches}
    assert reread.json()["version"] == 4


@pytest.mark.parametrize(
    "quick_actions",
    [
        {},
        {"sales": DEFAULT_CATALOG},
        {"catalog": {"unknown": True}},
        {"catalog": {"enabled": "false"}},
        {"batches": {"enabled": 1}},
        {"catalog": {"order": []}},
        {"catalog": {"order": ["open", "open"]}},
        {"catalog": {"hidden": ["edit", "edit"]}},
        {"catalog": {"order": ["open"], "hidden": ["edit"]}},
        {"catalog": {"order": ["open"], "hidden": ["open"]}},
        {"catalog": {"enabled": False, "order": ["open"], "hidden": ["open"]}},
        {"catalog": {"order": ["copy_number"]}},
        {"batches": {"order": ["edit"]}},
        {"batches": {"hidden": ["copy_barcode"]}},
        {"batches": {"order": ["open", "copy_number"], "hidden": ["open", "copy_number"]}},
    ],
)
async def test_quick_actions_reject_invalid_config_without_updating(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,
    quick_actions: dict[str, object],
) -> None:
    user = await make_user()
    headers = await _headers(db_session, user.id)
    response = await auth_client.patch(
        "/api/v1/auth/preferences",
        headers=headers,
        json={"expected_version": 1, "quick_actions": quick_actions},
    )
    assert response.status_code == 422, response.text
    reread = await auth_client.get("/api/v1/auth/preferences", headers=headers)
    assert reread.status_code == 200, reread.text
    assert reread.json()["version"] == 1
    assert reread.json()["quick_actions"] == {
        "catalog": DEFAULT_CATALOG,
        "batches": DEFAULT_BATCHES,
    }


async def test_quick_actions_are_isolated_between_users_and_tenants(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,
) -> None:
    foundation = FoundationService(FoundationRepository(db_session))
    tenant_a = await foundation.create_tenant(
        payload={"name": "Quick actions A", "contact_email": "quick-a@example.test"}
    )
    tenant_b = await foundation.create_tenant(
        payload={"name": "Quick actions B", "contact_email": "quick-b@example.test"}
    )
    user_a = await make_user(home_tenant_id=tenant_a.id)
    user_b = await make_user(home_tenant_id=tenant_b.id)
    for user, tenant in ((user_a, tenant_a), (user_b, tenant_b)):
        db_session.add(
            TenantMembership(
                tenant_id=tenant.id,
                user_id=user.id,
                full_name=user.full_name,
                status="active",
            )
        )
    await db_session.flush()

    headers_a = await _headers(db_session, user_a.id, tenant_a.id)
    headers_b = await _headers(db_session, user_b.id, tenant_b.id)
    catalog_a = {"enabled": False, "order": ["open", "edit"], "hidden": ["edit"]}
    saved_a = await auth_client.patch(
        "/api/v1/auth/preferences",
        headers=headers_a,
        json={"expected_version": 1, "quick_actions": {"catalog": catalog_a}},
    )
    assert saved_a.status_code == 200, saved_a.text

    read_b = await auth_client.get("/api/v1/auth/preferences", headers=headers_b)
    assert read_b.status_code == 200, read_b.text
    assert read_b.json()["quick_actions"]["catalog"] == DEFAULT_CATALOG
    batches_b = {"enabled": False, "order": ["copy_number", "open"], "hidden": []}
    saved_b = await auth_client.patch(
        "/api/v1/auth/preferences",
        headers=headers_b,
        json={"expected_version": 1, "quick_actions": {"batches": batches_b}},
    )
    assert saved_b.status_code == 200, saved_b.text
    reread_a = await auth_client.get("/api/v1/auth/preferences", headers=headers_a)
    assert reread_a.json()["quick_actions"] == {"catalog": catalog_a, "batches": DEFAULT_BATCHES}

    for user in (user_a, user_b):
        global_preferences = await auth_client.get(
            "/api/v1/auth/preferences",
            headers=await _headers(db_session, user.id),
        )
        assert global_preferences.status_code == 200, global_preferences.text
        assert global_preferences.json()["quick_actions"] == {
            "catalog": DEFAULT_CATALOG,
            "batches": DEFAULT_BATCHES,
        }
        assert global_preferences.json()["version"] == 2
    reread_b = await auth_client.get("/api/v1/auth/preferences", headers=headers_b)
    assert reread_b.json()["quick_actions"] == {"catalog": DEFAULT_CATALOG, "batches": batches_b}
    for user, other_tenant in ((user_a, tenant_b), (user_b, tenant_a)):
        denied = await auth_client.get(
            "/api/v1/auth/preferences",
            headers=await _headers(db_session, user.id, other_tenant.id),
        )
        assert denied.status_code == 401, denied.text
