"""Scoped quick action preferences preserve sidebar state and optimistic locking."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.auth.models import UserPreferences
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
DEFAULT_INCOMING = {"enabled": True, "order": ["open", "copy_number"], "hidden": []}
DEFAULT_RECEIPTS = {"enabled": True, "order": ["open", "copy_number"], "hidden": []}
DEFAULT_POS_CART = {
    "enabled": True,
    "order": ["copy_name", "copy_number", "increase_qty", "decrease_qty", "remove"],
    "hidden": [],
}
DEFAULT_EMPLOYEES = {
    "enabled": True,
    "order": [
        "open",
        "profile",
        "assign_access",
        "reissue_invitation",
        "activate",
        "revoke_sessions",
        "suspend",
        "offboard",
        "transfer_ownership",
    ],
    "hidden": [],
}
DEFAULT_QUICK_ACTIONS = {
    "catalog": DEFAULT_CATALOG,
    "batches": DEFAULT_BATCHES,
    "incoming": DEFAULT_INCOMING,
    "employees": DEFAULT_EMPLOYEES,
    "receipts": DEFAULT_RECEIPTS,
    "pos_cart": DEFAULT_POS_CART,
}


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
    assert saved.json()["quick_actions"] == {**DEFAULT_QUICK_ACTIONS, "catalog": catalog}
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
    assert changed_batches.json()["quick_actions"] == {
        **DEFAULT_QUICK_ACTIONS,
        "catalog": catalog,
        "batches": batches,
    }

    old_client = await auth_client.patch(
        "/api/v1/auth/preferences",
        headers=headers,
        json={"expected_version": 3, "workspace": {"start_route": "/batches"}},
    )
    assert old_client.status_code == 200, old_client.text
    assert old_client.json()["workspace"]["start_route"] == "/batches"
    assert old_client.json()["quick_actions"] == {
        **DEFAULT_QUICK_ACTIONS,
        "catalog": catalog,
        "batches": batches,
    }
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
    assert reread.json()["quick_actions"] == {
        **DEFAULT_QUICK_ACTIONS,
        "catalog": catalog,
        "batches": batches,
    }
    assert reread.json()["version"] == 4


async def test_legacy_storage_defaults_and_new_old_section_updates_preserve_each_other(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,
) -> None:
    user = await make_user()
    catalog = {"enabled": False, "order": ["open", "copy_name"], "hidden": ["copy_name"]}
    batches = {"enabled": True, "order": ["copy_number", "open"], "hidden": []}
    db_session.add(
        UserPreferences(
            user_id=user.id,
            workspace_preferences={
                "global": {
                    "favorite_routes": ["/catalog"],
                    "start_route": "/catalog",
                    "quick_actions": {"catalog": catalog, "batches": batches},
                }
            },
        )
    )
    await db_session.flush()
    headers = await _headers(db_session, user.id)
    legacy = await auth_client.get("/api/v1/auth/preferences", headers=headers)
    assert legacy.status_code == 200, legacy.text
    expected = {**DEFAULT_QUICK_ACTIONS, "catalog": catalog, "batches": batches}
    assert legacy.json()["quick_actions"] == expected
    assert legacy.json()["version"] == 1
    sidebar = legacy.json()["workspace"]

    incoming = {"enabled": False, "order": ["copy_number", "open"], "hidden": ["open"]}
    incoming_saved = await auth_client.patch(
        "/api/v1/auth/preferences",
        headers=headers,
        json={"expected_version": 1, "quick_actions": {"incoming": incoming}},
    )
    assert incoming_saved.status_code == 200, incoming_saved.text
    expected = {**expected, "incoming": incoming}
    assert incoming_saved.json()["quick_actions"] == expected
    assert incoming_saved.json()["workspace"] == sidebar

    employees = {
        "enabled": True,
        "order": list(reversed(DEFAULT_EMPLOYEES["order"])),
        "hidden": ["suspend", "offboard", "transfer_ownership"],
    }
    employees_saved = await auth_client.patch(
        "/api/v1/auth/preferences",
        headers=headers,
        json={"expected_version": 2, "quick_actions": {"employees": employees}},
    )
    assert employees_saved.status_code == 200, employees_saved.text
    expected = {**expected, "employees": employees}
    assert employees_saved.json()["quick_actions"] == expected
    assert employees_saved.json()["workspace"] == sidebar

    catalog_saved = await auth_client.patch(
        "/api/v1/auth/preferences",
        headers=headers,
        json={"expected_version": 3, "quick_actions": {"catalog": DEFAULT_CATALOG}},
    )
    assert catalog_saved.status_code == 200, catalog_saved.text
    expected = {**expected, "catalog": DEFAULT_CATALOG}
    assert catalog_saved.json()["quick_actions"] == expected
    assert catalog_saved.json()["workspace"] == sidebar

    old_sidebar_saved = await auth_client.patch(
        "/api/v1/auth/preferences",
        headers=headers,
        json={"expected_version": 4, "workspace": {"start_route": "/incoming"}},
    )
    assert old_sidebar_saved.status_code == 200, old_sidebar_saved.text
    assert old_sidebar_saved.json()["quick_actions"] == expected
    assert old_sidebar_saved.json()["workspace"]["start_route"] == "/incoming"
    assert old_sidebar_saved.json()["version"] == 5
    stale = await auth_client.patch(
        "/api/v1/auth/preferences",
        headers=headers,
        json={"expected_version": 4, "quick_actions": {"employees": DEFAULT_EMPLOYEES}},
    )
    assert stale.status_code == 409, stale.text
    reread = await auth_client.get("/api/v1/auth/preferences", headers=headers)
    assert reread.json()["quick_actions"] == expected
    assert reread.json()["version"] == 5


@pytest.mark.parametrize("section", ["receipts", "pos_cart"])
@pytest.mark.parametrize("legacy_section_count", [2, 4, 5])
async def test_new_preferences_preserve_legacy_sections_sidebar_and_version(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,
    legacy_section_count: int,
    section: str,
) -> None:
    user = await make_user()
    legacy_actions = {
        "catalog": {"enabled": False, "order": ["copy_name", "open"], "hidden": ["open"]},
        "batches": {"enabled": True, "order": ["copy_number", "open"], "hidden": []},
    }
    if legacy_section_count >= 4:
        legacy_actions.update(
            {
                "incoming": {
                    "enabled": False,
                    "order": ["copy_number", "open"],
                    "hidden": ["open"],
                },
                "employees": {"enabled": True, "order": ["profile", "open"], "hidden": []},
            }
        )
    if legacy_section_count == 5:
        legacy_actions["receipts"] = {
            "enabled": True,
            "order": ["copy_number", "open"],
            "hidden": ["open"],
        }
    db_session.add(
        UserPreferences(
            user_id=user.id,
            workspace_preferences={
                "global": {
                    "favorite_routes": ["/catalog"],
                    "start_route": "/catalog",
                    "quick_actions": legacy_actions,
                }
            },
        )
    )
    await db_session.flush()
    headers = await _headers(db_session, user.id)
    initial = await auth_client.get("/api/v1/auth/preferences", headers=headers)
    assert initial.status_code == 200, initial.text
    expected = {**DEFAULT_QUICK_ACTIONS, **legacy_actions}
    assert initial.json()["quick_actions"] == expected
    assert initial.json()["version"] == 1
    sidebar = initial.json()["workspace"]

    config = (
        {"enabled": False, "order": ["copy_number", "open"], "hidden": ["open"]}
        if section == "receipts"
        else {
            "enabled": False,
            "order": ["remove", "decrease_qty", "increase_qty", "copy_number", "copy_name"],
            "hidden": ["remove", "copy_number"],
        }
    )
    saved = await auth_client.patch(
        "/api/v1/auth/preferences",
        headers=headers,
        json={"expected_version": 1, "quick_actions": {section: config}},
    )
    assert saved.status_code == 200, saved.text
    expected = {**expected, section: config}
    assert saved.json()["quick_actions"] == expected
    assert saved.json()["workspace"] == sidebar
    assert saved.json()["version"] == 2

    old_section = await auth_client.patch(
        "/api/v1/auth/preferences",
        headers=headers,
        json={"expected_version": 2, "quick_actions": {"catalog": DEFAULT_CATALOG}},
    )
    assert old_section.status_code == 200, old_section.text
    expected = {**expected, "catalog": DEFAULT_CATALOG}
    assert old_section.json()["quick_actions"] == expected
    assert old_section.json()["workspace"] == sidebar

    old_sidebar = await auth_client.patch(
        "/api/v1/auth/preferences",
        headers=headers,
        json={"expected_version": 3, "workspace": {"start_route": "/sales"}},
    )
    assert old_sidebar.status_code == 200, old_sidebar.text
    assert old_sidebar.json()["quick_actions"] == expected
    assert old_sidebar.json()["version"] == 4

    stale = await auth_client.patch(
        "/api/v1/auth/preferences",
        headers=headers,
        json={"expected_version": 3, "quick_actions": {section: DEFAULT_QUICK_ACTIONS[section]}},
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["details"] == {"current_version": 4}
    reread = await auth_client.get("/api/v1/auth/preferences", headers=headers)
    assert reread.status_code == 200, reread.text
    assert reread.json()["quick_actions"] == expected
    assert reread.json()["workspace"] == old_sidebar.json()["workspace"]
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
        {"incoming": {"enabled": "true"}},
        {"incoming": {"unknown": True}},
        {"incoming": {"order": ["archive"]}},
        {"incoming": {"order": ["open", "open"]}},
        {"incoming": {"hidden": ["copy_number", "copy_number"]}},
        {"incoming": {"order": ["open"], "hidden": ["copy_number"]}},
        {"incoming": {"hidden": ["open", "copy_number"]}},
        {"employees": {"enabled": 0}},
        {"employees": {"unknown": True}},
        {"employees": {"order": ["copy_number"]}},
        {"employees": {"order": []}},
        {"employees": {"order": ["profile", "profile"]}},
        {"employees": {"hidden": ["suspend", "suspend"]}},
        {"employees": {"order": ["profile"], "hidden": ["activate"]}},
        {"employees": {"order": ["offboard"], "hidden": ["offboard"]}},
        {"receipts": None},
        {"catalog": DEFAULT_CATALOG, "receipts": None},
        {"receipts": {"unknown": True}},
        {"receipts": {"enabled": "false"}},
        {"receipts": {"enabled": 1}},
        {"receipts": {"enabled": None}},
        {"receipts": {"order": None}},
        {"receipts": {"hidden": None}},
        {"receipts": {"order": []}},
        {"receipts": {"order": ["print"]}},
        {"receipts": {"order": ["refund"]}},
        {"receipts": {"order": ["cancel"]}},
        {"receipts": {"hidden": ["edit"]}},
        {"receipts": {"order": ["open", "open"]}},
        {"receipts": {"hidden": ["copy_number", "copy_number"]}},
        {"receipts": {"order": ["open"], "hidden": ["copy_number"]}},
        {"receipts": {"hidden": ["open", "copy_number"]}},
        {"receipts": {"enabled": False, "hidden": ["open", "copy_number"]}},
        {"pos_cart": None},
        {"catalog": DEFAULT_CATALOG, "pos_cart": None},
        {"pos_cart": {"unknown": True}},
        {"pos_cart": {"enabled": "false"}},
        {"pos_cart": {"enabled": 1}},
        {"pos_cart": {"enabled": None}},
        {"pos_cart": {"order": None}},
        {"pos_cart": {"hidden": None}},
        {"pos_cart": {"order": []}},
        {"pos_cart": {"order": ["payment"]}},
        {"pos_cart": {"order": ["refund"]}},
        {"pos_cart": {"order": ["complete"]}},
        {"pos_cart": {"order": ["print"]}},
        {"pos_cart": {"order": ["open"]}},
        {"pos_cart": {"hidden": ["refund"]}},
        {"pos_cart": {"order": ["copy_name", "copy_name"]}},
        {"pos_cart": {"hidden": ["remove", "remove"]}},
        {"pos_cart": {"order": ["copy_name"], "hidden": ["remove"]}},
        {"pos_cart": {"hidden": DEFAULT_POS_CART["order"]}},
        {"pos_cart": {"enabled": False, "hidden": DEFAULT_POS_CART["order"]}},
        {"pos_cart": {"order": [*DEFAULT_POS_CART["order"], "copy_name"]}},
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
    assert reread.json()["quick_actions"] == DEFAULT_QUICK_ACTIONS


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
    receipts_a = {"enabled": False, "order": ["copy_number", "open"], "hidden": ["open"]}
    pos_cart_a = {
        "enabled": False,
        "order": ["remove", "copy_name", "increase_qty"],
        "hidden": ["remove"],
    }
    saved_a = await auth_client.patch(
        "/api/v1/auth/preferences",
        headers=headers_a,
        json={
            "expected_version": 1,
            "quick_actions": {"catalog": catalog_a, "receipts": receipts_a, "pos_cart": pos_cart_a},
        },
    )
    assert saved_a.status_code == 200, saved_a.text

    read_b = await auth_client.get("/api/v1/auth/preferences", headers=headers_b)
    assert read_b.status_code == 200, read_b.text
    assert read_b.json()["quick_actions"]["catalog"] == DEFAULT_CATALOG
    assert read_b.json()["quick_actions"]["receipts"] == DEFAULT_RECEIPTS
    assert read_b.json()["quick_actions"]["pos_cart"] == DEFAULT_POS_CART
    batches_b = {"enabled": False, "order": ["copy_number", "open"], "hidden": []}
    receipts_b = {"enabled": True, "order": ["open", "copy_number"], "hidden": ["copy_number"]}
    pos_cart_b = {
        "enabled": True,
        "order": ["copy_number", "copy_name", "decrease_qty"],
        "hidden": ["decrease_qty"],
    }
    saved_b = await auth_client.patch(
        "/api/v1/auth/preferences",
        headers=headers_b,
        json={
            "expected_version": 1,
            "quick_actions": {"batches": batches_b, "receipts": receipts_b, "pos_cart": pos_cart_b},
        },
    )
    assert saved_b.status_code == 200, saved_b.text
    reread_a = await auth_client.get("/api/v1/auth/preferences", headers=headers_a)
    assert reread_a.json()["quick_actions"] == {
        **DEFAULT_QUICK_ACTIONS,
        "catalog": catalog_a,
        "receipts": receipts_a,
        "pos_cart": pos_cart_a,
    }

    for user in (user_a, user_b):
        global_preferences = await auth_client.get(
            "/api/v1/auth/preferences",
            headers=await _headers(db_session, user.id),
        )
        assert global_preferences.status_code == 200, global_preferences.text
        assert global_preferences.json()["quick_actions"] == DEFAULT_QUICK_ACTIONS
        assert global_preferences.json()["version"] == 2
    reread_b = await auth_client.get("/api/v1/auth/preferences", headers=headers_b)
    assert reread_b.json()["quick_actions"] == {
        **DEFAULT_QUICK_ACTIONS,
        "batches": batches_b,
        "receipts": receipts_b,
        "pos_cart": pos_cart_b,
    }
    for user, other_tenant in ((user_a, tenant_b), (user_b, tenant_a)):
        denied = await auth_client.get(
            "/api/v1/auth/preferences",
            headers=await _headers(db_session, user.id, other_tenant.id),
        )
        assert denied.status_code == 401, denied.text
