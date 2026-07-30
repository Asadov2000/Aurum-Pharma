"""Tenant + tenant_settings: lifecycle, defaults, validation."""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService


async def test_create_tenant_creates_default_settings(db_session: AsyncSession) -> None:
    service = FoundationService(FoundationRepository(db_session))

    tenant = await service.create_tenant(
        payload={"name": "Defaults co.", "contact_email": "d@aurum.tj"}
    )
    settings = await service.get_settings(tenant.id)

    assert settings.tenant_id == tenant.id
    assert settings.expiry_thresholds == {"yellow": 6, "orange": 3, "red": 1}
    assert settings.expired_sale_mode == "strict"
    assert settings.refund_reason_mode == "optional"
    assert settings.session_admin_minutes == 480
    assert settings.session_pos_minutes == 480
    assert settings.pin_mode_enabled is False
    assert settings.pos_payment_methods == ["cash", "card", "qr"]
    assert settings.pos_mixed_payment_enabled is True


async def test_tenant_settings_thresholds_invalid_returns_422(
    auth_client: AsyncClient, tenant_admin_token
) -> None:
    token, _, _ = await tenant_admin_token()

    response = await auth_client.patch(
        "/api/v1/tenant/settings",
        headers={"Authorization": f"Bearer {token}"},
        json={"expiry_thresholds": {"yellow": 2, "orange": 5, "red": 1}},
    )
    assert response.status_code == 422


async def test_tenant_settings_thresholds_valid(
    auth_client: AsyncClient, tenant_admin_token
) -> None:
    token, _, _ = await tenant_admin_token()

    response = await auth_client.patch(
        "/api/v1/tenant/settings",
        headers={"Authorization": f"Bearer {token}"},
        json={"expiry_thresholds": {"yellow": 9, "orange": 4, "red": 2}},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["expiry_thresholds"] == {"yellow": 9, "orange": 4, "red": 2}


@pytest.mark.parametrize(
    "methods",
    [
        [],
        ["cash", "cash"],
        ["cash", "bank_transfer"],
        ["cash", "card", "qr", "cash"],
    ],
)
async def test_tenant_settings_reject_invalid_pos_payment_methods(
    auth_client: AsyncClient,
    tenant_admin_token,
    methods: list[str],
) -> None:
    token, _, _ = await tenant_admin_token()

    response = await auth_client.patch(
        "/api/v1/tenant/settings",
        headers={"Authorization": f"Bearer {token}"},
        json={"pos_payment_methods": methods},
    )

    assert response.status_code == 422


async def test_tenant_settings_update_pos_payment_configuration(
    auth_client: AsyncClient,
    tenant_admin_token,
) -> None:
    token, _, _ = await tenant_admin_token()

    response = await auth_client.patch(
        "/api/v1/tenant/settings",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "pos_payment_methods": ["cash", "qr"],
            "pos_mixed_payment_enabled": False,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["pos_payment_methods"] == ["cash", "qr"]
    assert response.json()["pos_mixed_payment_enabled"] is False


async def test_pos_payment_settings_are_tenant_isolated(
    auth_client: AsyncClient,
    tenant_admin_token,
) -> None:
    first_token, _, _ = await tenant_admin_token()
    second_token, _, _ = await tenant_admin_token()

    updated = await auth_client.patch(
        "/api/v1/tenant/settings",
        headers={"Authorization": f"Bearer {first_token}"},
        json={
            "pos_payment_methods": ["qr"],
            "pos_mixed_payment_enabled": False,
        },
    )
    first = await auth_client.get(
        "/api/v1/tenant/settings",
        headers={"Authorization": f"Bearer {first_token}"},
    )
    second = await auth_client.get(
        "/api/v1/tenant/settings",
        headers={"Authorization": f"Bearer {second_token}"},
    )

    assert updated.status_code == 200, updated.text
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["pos_payment_methods"] == ["qr"]
    assert first.json()["pos_mixed_payment_enabled"] is False
    assert second.json()["pos_payment_methods"] == ["cash", "card", "qr"]
    assert second.json()["pos_mixed_payment_enabled"] is True


async def test_pos_payment_settings_database_check_rejects_duplicates(
    db_session: AsyncSession,
    make_tenant,
) -> None:
    tenant = await make_tenant()

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "UPDATE tenant_settings "
                    'SET pos_payment_methods = \'["cash","cash"]\'::jsonb '
                    "WHERE tenant_id = :tenant_id"
                ),
                {"tenant_id": tenant.id},
            )


async def test_admin_create_tenant_endpoint(auth_client: AsyncClient, support_token: str) -> None:
    response = await auth_client.post(
        "/api/v1/admin/tenants",
        headers={"Authorization": f"Bearer {support_token}"},
        json={"name": "Via HTTP", "contact_email": "http@aurum.tj"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["name"] == "Via HTTP"
    assert body["status"] == "setup"


async def test_admin_endpoints_reject_non_support(
    auth_client: AsyncClient,
    make_user,
) -> None:
    """An active regular user is authenticated but cannot enter support APIs."""
    from app.core.security import create_access_token

    regular_user = await make_user()
    token = create_access_token(
        user_id=regular_user.id,
        tenant_id=None,
        is_developer=False,
        is_administrator=False,
    )
    response = await auth_client.get(
        "/api/v1/admin/tenants",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


async def test_update_tenant_to_trial_fills_dates(db_session: AsyncSession, make_tenant) -> None:
    tenant = await make_tenant(status="setup")
    assert tenant.trial_started_at is None

    service = FoundationService(FoundationRepository(db_session))
    updated = await service.update_tenant(tenant.id, fields={"status": "trial"})

    assert updated.status == "trial"
    assert updated.trial_started_at is not None
    assert updated.trial_ends_at is not None
    assert updated.trial_ends_at > updated.trial_started_at
