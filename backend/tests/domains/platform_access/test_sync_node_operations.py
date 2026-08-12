"""Privileged sync-node operations remain recoverable, atomic, and auditable."""

from __future__ import annotations

from uuid import uuid4

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.sync.credentials import parse_edge_credential
from app.domains.sync.repository import SyncCloudRepository
from app.domains.sync.schemas import (
    SyncCredentialRotationSecretRead,
    SyncNodeCreate,
    SyncNodeCredentialRead,
)
from app.domains.sync.service import SyncAdminService
from tests.auth_helpers import create_support_access_token
from tests.platform_access_helpers import create_test_platform_user


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _action_payload(*, version: int, operation_id: str, name: str) -> dict[str, object]:
    return {
        "expected_version": version,
        "operation_id": operation_id,
        "confirmation_name": name,
        "reason_code": "routine_maintenance",
        "reason": "Плановая проверка безопасной замены ключа узла",
    }


async def _authenticate(session: AsyncSession, credential: str) -> object | None:
    parsed = parse_edge_credential(credential)
    return (
        (
            await session.execute(
                text("SELECT * FROM public.authenticate_edge_node(:kid, :digest)"),
                {"kid": parsed.kid, "digest": parsed.digest},
            )
        )
        .mappings()
        .one_or_none()
    )


async def test_staged_rotation_keeps_old_key_until_verified_completion(
    db_session: AsyncSession,
    platform_client: AsyncClient,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    node = await SyncAdminService(SyncCloudRepository(db_session)).create_node(
        SyncNodeCreate(
            tenant_id=scaffold["tenant"].id,
            branch_id=scaffold["branch"].id,
            display_name="Edge staged rotation",
        )
    )
    developer = await create_test_platform_user(db_session, access_kind="developer")
    token = await create_support_access_token(db_session, developer)
    operation_id = str(uuid4())
    payload = {
        **_action_payload(
            version=node.lifecycle_version,
            operation_id=operation_id,
            name=node.display_name,
        ),
        "credential_valid_days": 90,
    }

    started = await platform_client.post(
        f"/api/v1/admin/sync/nodes/{node.id}/credential-rotations",
        json=payload,
        headers=_headers(token),
    )
    assert started.status_code == 201, started.text
    assert started.headers["cache-control"] == "no-store"
    body = started.json()
    new_credential = body["credential"]
    assert isinstance(new_credential, str)
    assert body["replayed"] is False
    assert await _authenticate(db_session, node.credential) is not None

    replayed = await platform_client.post(
        f"/api/v1/admin/sync/nodes/{node.id}/credential-rotations",
        json=payload,
        headers=_headers(token),
    )
    assert replayed.status_code == 201, replayed.text
    assert replayed.json()["credential"] is None
    assert replayed.json()["replayed"] is True

    assert await _authenticate(db_session, new_credential) is not None
    rotation_status = await db_session.scalar(
        text("SELECT status FROM sync_node_credential_rotation WHERE id = :rotation_id"),
        {"rotation_id": body["rotation_id"]},
    )
    assert rotation_status == "verified"
    assert await _authenticate(db_session, node.credential) is not None

    completed = await platform_client.post(
        f"/api/v1/admin/sync/credential-rotations/{body['rotation_id']}/complete",
        json=_action_payload(
            version=body["node_version"],
            operation_id=str(uuid4()),
            name=node.display_name,
        ),
        headers=_headers(token),
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["rotation_status"] == "completed"
    assert await _authenticate(db_session, node.credential) is None
    assert await _authenticate(db_session, new_credential) is not None

    late_replay = await platform_client.post(
        f"/api/v1/admin/sync/nodes/{node.id}/credential-rotations",
        json=payload,
        headers=_headers(token),
    )
    assert late_replay.status_code == 201, late_replay.text
    assert late_replay.json()["status"] == "pending"
    assert late_replay.json()["node_version"] == body["node_version"]
    assert late_replay.json()["credential"] is None
    assert late_replay.json()["replayed"] is True

    event_dump = str(
        (
            await db_session.execute(
                text("""
                    SELECT event_type, request_hash, reason_code
                    FROM sync_node_admin_event
                    WHERE node_id = :node_id
                    ORDER BY created_at, id
                    """),
                {"node_id": node.id},
            )
        ).all()
    )
    assert "credential_rotation_started" in event_dump
    assert "credential_rotation_verified" in event_dump
    assert "credential_rotation_completed" in event_dump
    assert new_credential not in event_dump
    assert parse_edge_credential(new_credential).digest not in event_dump

    changed_replay = await platform_client.post(
        f"/api/v1/admin/sync/nodes/{node.id}/credential-rotations",
        json={**payload, "credential_valid_days": 120},
        headers=_headers(token),
    )
    assert changed_replay.status_code == 409


async def test_confirmed_revoke_is_idempotent_and_invalidates_credentials(
    db_session: AsyncSession,
    platform_client: AsyncClient,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    node = await SyncAdminService(SyncCloudRepository(db_session)).create_node(
        SyncNodeCreate(
            tenant_id=scaffold["tenant"].id,
            branch_id=scaffold["branch"].id,
            display_name="Edge revoke test",
        )
    )
    developer = await create_test_platform_user(db_session, access_kind="developer")
    token = await create_support_access_token(db_session, developer)
    operation_id = str(uuid4())
    payload = _action_payload(
        version=node.lifecycle_version,
        operation_id=operation_id,
        name=node.display_name,
    )

    revoked = await platform_client.post(
        f"/api/v1/admin/sync/nodes/{node.id}/revoke",
        json=payload,
        headers=_headers(token),
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["node_status"] == "revoked"
    assert revoked.json()["replayed"] is False
    assert await _authenticate(db_session, node.credential) is None

    replayed = await platform_client.post(
        f"/api/v1/admin/sync/nodes/{node.id}/revoke",
        json=payload,
        headers=_headers(token),
    )
    assert replayed.status_code == 200, replayed.text
    assert replayed.json()["replayed"] is True


async def test_verified_rotation_can_be_cancelled_or_revoked(
    db_session: AsyncSession,
    platform_client: AsyncClient,
    pos_scaffold,
) -> None:  # type: ignore[no-untyped-def]
    scaffold = await pos_scaffold()
    admin = SyncAdminService(SyncCloudRepository(db_session))
    cancelled_node = await admin.create_node(
        SyncNodeCreate(
            tenant_id=scaffold["tenant"].id,
            branch_id=scaffold["branch"].id,
            display_name="Edge verified cancel",
        )
    )
    revoked_node = await admin.create_node(
        SyncNodeCreate(
            tenant_id=scaffold["tenant"].id,
            branch_id=scaffold["branch"].id,
            display_name="Edge verified revoke",
        )
    )
    developer = await create_test_platform_user(db_session, access_kind="developer")
    token = await create_support_access_token(db_session, developer)

    async def start_and_verify(
        node: SyncNodeCredentialRead,
    ) -> tuple[SyncCredentialRotationSecretRead, str]:
        payload = {
            **_action_payload(
                version=node.lifecycle_version,
                operation_id=str(uuid4()),
                name=node.display_name,
            ),
            "credential_valid_days": 90,
        }
        response = await platform_client.post(
            f"/api/v1/admin/sync/nodes/{node.id}/credential-rotations",
            json=payload,
            headers=_headers(token),
        )
        assert response.status_code == 201, response.text
        body = SyncCredentialRotationSecretRead.model_validate(response.json())
        credential = body.credential
        assert isinstance(credential, str)
        assert await _authenticate(db_session, credential) is not None
        return body, credential

    cancelled_rotation, cancelled_credential = await start_and_verify(cancelled_node)
    cancelled = await platform_client.post(
        f"/api/v1/admin/sync/credential-rotations/{cancelled_rotation.rotation_id}/cancel",
        json=_action_payload(
            version=cancelled_rotation.node_version,
            operation_id=str(uuid4()),
            name=cancelled_node.display_name,
        ),
        headers=_headers(token),
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["rotation_status"] == "cancelled"
    assert await _authenticate(db_session, cancelled_node.credential) is not None
    assert await _authenticate(db_session, cancelled_credential) is None

    revoked_rotation, revoked_credential = await start_and_verify(revoked_node)
    revoked = await platform_client.post(
        f"/api/v1/admin/sync/nodes/{revoked_node.id}/revoke",
        json=_action_payload(
            version=revoked_rotation.node_version,
            operation_id=str(uuid4()),
            name=revoked_node.display_name,
        ),
        headers=_headers(token),
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["node_status"] == "revoked"
    assert await _authenticate(db_session, revoked_node.credential) is None
    assert await _authenticate(db_session, revoked_credential) is None
