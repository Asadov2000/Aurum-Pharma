"""Authenticated, versioned user preferences."""

from __future__ import annotations

from uuid import UUID

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import create_access_token
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.roles.models import TenantMembership


def _headers(user_id: UUID, tenant_id: UUID | None = None) -> dict[str, str]:
    token = create_access_token(
        user_id,
        tenant_id=tenant_id,
        is_developer=False,
        is_administrator=False,
    )
    return {"Authorization": f"Bearer {token}"}


async def test_preferences_default_update_and_stale_version(
    auth_client: AsyncClient,
    make_user,
) -> None:
    user = await make_user(email="preferences@aurum.tj")
    headers = _headers(user.id)

    initial = await auth_client.get("/api/v1/auth/preferences", headers=headers)
    assert initial.status_code == 200, initial.text
    assert initial.headers["cache-control"] == "no-store"
    assert initial.json() == {
        "theme": "system",
        "density": "comfortable",
        "contrast": "standard",
        "reduce_motion": False,
        "accent": "teal",
        "workspace": {
            "desktop_mode": "auto",
            "hidden_routes": [],
            "favorite_routes": [],
            "route_order": [],
            "start_route": "/",
        },
        "version": 1,
        "updated_at": initial.json()["updated_at"],
    }

    updated = await auth_client.patch(
        "/api/v1/auth/preferences",
        headers=headers,
        json={
            "expected_version": 1,
            "theme": "dark",
            "density": "touch",
            "contrast": "high",
            "reduce_motion": True,
            "accent": "blue",
            "workspace": {
                "desktop_mode": "expanded",
                "favorite_routes": ["/catalog", "/pos"],
                "route_order": ["/pos", "/catalog"],
                "start_route": "/pos",
            },
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    assert updated.json()["theme"] == "dark"
    assert updated.json()["workspace"]["start_route"] == "/pos"

    stale = await auth_client.patch(
        "/api/v1/auth/preferences",
        headers=headers,
        json={"expected_version": 1, "theme": "light"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["details"] == {"current_version": 2}


async def test_preferences_workspace_is_scoped_to_active_context(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    make_user,
) -> None:
    foundation = FoundationService(FoundationRepository(db_session))
    tenant_a = await foundation.create_tenant(
        payload={"name": "Preferences A", "contact_email": "preferences-a@aurum.tj"}
    )
    user = await make_user(
        email="preferences-scoped@aurum.tj",
        home_tenant_id=tenant_a.id,
    )
    db_session.add_all(
        [
            TenantMembership(
                tenant_id=tenant_a.id,
                user_id=user.id,
                full_name=user.full_name,
                status="active",
            ),
        ]
    )
    await db_session.flush()

    saved_a = await auth_client.patch(
        "/api/v1/auth/preferences",
        headers=_headers(user.id, tenant_a.id),
        json={
            "expected_version": 1,
            "workspace": {"favorite_routes": ["/catalog"], "start_route": "/catalog"},
        },
    )
    assert saved_a.status_code == 200, saved_a.text

    read_global = await auth_client.get(
        "/api/v1/auth/preferences",
        headers=_headers(user.id),
    )
    assert read_global.status_code == 200, read_global.text
    assert read_global.json()["workspace"]["favorite_routes"] == []
    assert read_global.json()["workspace"]["start_route"] == "/"

    read_a = await auth_client.get(
        "/api/v1/auth/preferences",
        headers=_headers(user.id, tenant_a.id),
    )
    assert read_a.json()["workspace"]["favorite_routes"] == ["/catalog"]


async def test_preferences_reject_unknown_workspace_routes(
    auth_client: AsyncClient,
    make_user,
) -> None:
    user = await make_user(email="preferences-invalid@aurum.tj")
    response = await auth_client.patch(
        "/api/v1/auth/preferences",
        headers=_headers(user.id),
        json={
            "expected_version": 1,
            "workspace": {"favorite_routes": ["/not-an-aurum-route"]},
        },
    )
    assert response.status_code == 422
