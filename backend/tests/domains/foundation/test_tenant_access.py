"""Independent administrative policy: real HTTP, RLS and database guards."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.domains.tenant_access.schemas import AccessPolicyUpdate


async def _policy(
    client: AsyncClient, token: str, tenant_id: UUID, **fields: object
) -> dict[str, object]:
    response = await client.patch(
        f"/api/v1/admin/tenants/{tenant_id}/access-policy",
        headers={"Authorization": f"Bearer {token}"},
        json={"expected_version": 1, "reason": "Ограничение для проверки", **fields},
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, object], response.json())


@pytest.mark.asyncio
async def test_policy_default_revision_and_audit(
    auth_client: AsyncClient, db_session: AsyncSession, support_token: str, make_tenant
) -> None:
    tenant = await make_tenant()
    response = await auth_client.get(
        f"/api/v1/admin/tenants/{tenant.id}/access-policy",
        headers={"Authorization": f"Bearer {support_token}"},
    )
    assert response.status_code == 200
    assert response.json()["mode"] == "full"
    assert response.json()["max_users"] is None
    policy = await _policy(auth_client, support_token, tenant.id, max_branches=2)
    assert policy["version"] == 2
    stale = await auth_client.patch(
        f"/api/v1/admin/tenants/{tenant.id}/access-policy",
        headers={"Authorization": f"Bearer {support_token}"},
        json={"expected_version": 1, "reason": "Устаревшее изменение"},
    )
    assert stale.status_code == 409
    audit_count = await db_session.scalar(
        text(
            "SELECT count(*) FROM audit_log WHERE tenant_id=:id "
            "AND table_name='tenant_access_policy' AND action='UPDATE'"
        ),
        {"id": tenant.id},
    )
    assert audit_count == 1


@pytest.mark.asyncio
async def test_policy_cannot_be_changed_by_tenant(
    auth_client: AsyncClient, tenant_admin_token
) -> None:
    token, tenant, _user = await tenant_admin_token()
    response = await auth_client.patch(
        f"/api/v1/admin/tenants/{tenant.id}/access-policy",
        headers={"Authorization": f"Bearer {token}"},
        json={"expected_version": 1, "reason": "Не разрешено"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_policy_requires_recent_confirmation(
    auth_client: AsyncClient, db_session: AsyncSession, make_tenant, make_user
) -> None:
    from tests.auth_helpers import create_support_access_token

    tenant = await make_tenant()
    admin = await make_user(is_administrator=True)
    stale_token = await create_support_access_token(
        db_session, admin, mfa_verified_at=datetime.now(UTC) - timedelta(hours=1)
    )
    response = await auth_client.patch(
        f"/api/v1/admin/tenants/{tenant.id}/access-policy",
        headers={"Authorization": f"Bearer {stale_token}"},
        json={"expected_version": 1, "reason": "Старое подтверждение"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_policy_rls_and_database_update_denial(db_engine: AsyncEngine) -> None:
    from app.core.config import get_settings

    first, second = uuid4(), uuid4()
    app_engine = create_async_engine(get_settings().DATABASE_URL_APP, poolclass=NullPool)
    try:
        async with db_engine.begin() as conn:
            for tenant_id in (first, second):
                await conn.execute(
                    text(
                        "INSERT INTO tenant(id,name,contact_email) VALUES(:id,'Policy RLS',:email)"
                    ),
                    {"id": tenant_id, "email": f"{tenant_id}@example.test"},
                )
        async with app_engine.begin() as conn:
            await conn.execute(
                text("SELECT set_config('app.tenant_id', :id, true)"), {"id": str(first)}
            )
            ids = list(await conn.scalars(text("SELECT tenant_id FROM tenant_access_policy")))
            assert ids == [first]
            assert second not in ids
            with pytest.raises(DBAPIError):
                async with conn.begin_nested():
                    await conn.execute(text("UPDATE tenant_access_policy SET mode='full'"))
    finally:
        await app_engine.dispose()
        async with db_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM tenant WHERE id IN (:first,:second)"),
                {"first": first, "second": second},
            )


@pytest.mark.asyncio
async def test_database_quota_blocks_only_growth_and_reactivation(
    auth_client: AsyncClient, db_session: AsyncSession, support_token: str, make_tenant
) -> None:
    tenant = await make_tenant()
    await _policy(auth_client, support_token, tenant.id, max_branches=1)
    insert = text("INSERT INTO branch(id,tenant_id,name) VALUES (:id,:tenant,'Policy branch')")
    first = uuid4()
    await db_session.execute(insert, {"id": first, "tenant": tenant.id})
    with pytest.raises(DBAPIError, match="tenant_access_quota_exceeded"):
        async with db_session.begin_nested():
            await db_session.execute(insert, {"id": uuid4(), "tenant": tenant.id})
    await db_session.execute(text("UPDATE branch SET is_active=false WHERE id=:id"), {"id": first})
    await db_session.execute(insert, {"id": uuid4(), "tenant": tenant.id})
    with pytest.raises(DBAPIError, match="tenant_access_quota_exceeded"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE branch SET is_active=true WHERE id=:id"), {"id": first}
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [{"mode": "readonly"}, {"blocked_modules": ["settings"]}])
async def test_database_mutations_blocked_read_access_preserved(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    support_token: str,
    make_tenant,
    fields: dict[str, object],
) -> None:
    tenant = await make_tenant()
    await _policy(auth_client, support_token, tenant.id, **fields)
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM tenant_settings WHERE tenant_id=:id"), {"id": tenant.id}
        )
        == 1
    )
    with pytest.raises(DBAPIError, match="tenant_access_restricted"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE tenant_settings SET updated_at=now() WHERE tenant_id=:id"),
                {"id": tenant.id},
            )


def test_policy_validation_rejects_ambiguous_inputs() -> None:
    from pydantic import ValidationError

    for invalid in (
        {"max_users": 0},
        {"max_users": True},
        {"blocked_modules": ["unknown"]},
        {"blocked_modules": ["pos", "pos"]},
        {"reason": "   "},
        {"expires_at": "2030-01-01T00:00:00"},
    ):
        with pytest.raises(ValidationError):
            AccessPolicyUpdate.model_validate(
                {"expected_version": 1, "reason": "Проверка", **invalid}
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("revoke_session", [False, True])
async def test_database_rechecks_confirmation(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    support_token: str,
    make_tenant,
    revoke_session: bool,
) -> None:
    tenant = await make_tenant()
    await _policy(auth_client, support_token, tenant.id, max_users=5)
    if revoke_session:
        await db_session.execute(
            text(
                "UPDATE session SET revoked_at=now() "
                "WHERE id=current_setting('app.auth_session_id')::uuid"
            )
        )
    else:
        await db_session.execute(text("SELECT set_config('app.auth_session_id','',true)"))
    with pytest.raises(DBAPIError, match="tenant_access_policy_denied"):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "UPDATE tenant_access_policy SET version=version+1, max_users=NULL "
                    "WHERE tenant_id=:id"
                ),
                {"id": tenant.id},
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [("readonly", "setup"), ("archived", "archived")])
async def test_legacy_review_preserves_launch_and_archive_boundaries(
    auth_client: AsyncClient,
    db_session: AsyncSession,
    support_token: str,
    status: str,
    expected: str,
) -> None:
    tenant_id = uuid4()
    await db_session.execute(
        text(
            "INSERT INTO tenant(id,name,contact_email,status) VALUES(:id,'Legacy',:email,:status)"
        ),
        {"id": tenant_id, "email": f"{tenant_id}@example.test", "status": status},
    )
    # Editing the explanation while keeping the restriction must not lose the
    # provenance needed to clear the legacy lifecycle state on later review.
    await _policy(auth_client, support_token, tenant_id, mode="readonly", max_users=5)
    policy = await _policy(auth_client, support_token, tenant_id, mode="full", expected_version=2)
    assert policy["mode"] == "full"
    assert (
        await db_session.scalar(text("SELECT status FROM tenant WHERE id=:id"), {"id": tenant_id})
        == expected
    )
    if expected == "archived":
        with pytest.raises(DBAPIError, match="tenant_access_restricted"):
            async with db_session.begin_nested():
                await db_session.execute(
                    text("INSERT INTO branch(tenant_id,name) VALUES(:id,'Forbidden')"),
                    {"id": tenant_id},
                )


@pytest.mark.asyncio
async def test_temporary_restriction_expires_without_a_worker(
    auth_client: AsyncClient, db_session: AsyncSession, support_token: str, make_tenant
) -> None:
    tenant = await make_tenant()
    await _policy(
        auth_client,
        support_token,
        tenant.id,
        mode="readonly",
        expires_at=(datetime.now(UTC) + timedelta(seconds=2)).isoformat(),
    )
    with pytest.raises(DBAPIError, match="tenant_access_restricted"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("INSERT INTO branch(tenant_id,name) VALUES(:id,'Too early')"),
                {"id": tenant.id},
            )
    await db_session.execute(text("SELECT pg_sleep(2.1)"))
    await db_session.execute(
        text("INSERT INTO branch(tenant_id,name) VALUES(:id,'Available')"), {"id": tenant.id}
    )


@pytest.mark.asyncio
async def test_tenant_read_does_not_disclose_platform_notes(
    auth_client: AsyncClient, tenant_admin_token
) -> None:
    token, tenant, _user = await tenant_admin_token()
    response = await auth_client.get(
        "/api/v1/tenant/access-policy", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.json()["tenant_id"] == str(tenant.id)
    assert "reason" not in response.json()
    assert "updated_by" not in response.json()


@pytest.mark.asyncio
async def test_policy_edit_waits_for_business_transaction_and_shared_writes_coexist(
    db_engine: AsyncEngine,
) -> None:
    tenant_id = uuid4()
    insert = text(
        "INSERT INTO supplier(tenant_id,name,create_request_fingerprint) "
        "VALUES(:id,'Concurrent ' || gen_random_uuid()::text,repeat('a',64))"
    )
    async with db_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO tenant(id,name,contact_email) VALUES(:id,'Lock test',:email)"),
            {"id": tenant_id, "email": f"{tenant_id}@example.test"},
        )

    async def edit_without_confirmation() -> None:
        async with db_engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE tenant_access_policy SET version=version+1, mode='readonly' "
                    "WHERE tenant_id=:id"
                ),
                {"id": tenant_id},
            )

    pending: asyncio.Task[None] | None = None
    try:
        async with db_engine.connect() as first:
            transaction = await first.begin()
            try:
                await first.execute(insert, {"id": tenant_id})
                # Two ordinary business transactions take compatible shared locks.
                async with db_engine.begin() as second:
                    await asyncio.wait_for(second.execute(insert, {"id": tenant_id}), 2)
                pending = asyncio.create_task(edit_without_confirmation())
                # Even direct SQL policy edits wait for the entire business transaction.
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(pending), 0.2)
            finally:
                await transaction.rollback()
        # Once the lock is released, the independent database authorization check runs.
        with pytest.raises(DBAPIError, match="tenant_access_policy_denied"):
            await asyncio.wait_for(pending, 2)
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        async with db_engine.begin() as conn:
            await conn.execute(text("DELETE FROM supplier WHERE tenant_id=:id"), {"id": tenant_id})
            await conn.execute(text("DELETE FROM tenant WHERE id=:id"), {"id": tenant_id})


@pytest.mark.asyncio
async def test_concurrent_growth_cannot_exceed_quota(db_engine: AsyncEngine) -> None:
    from app.core.security import decode_access_token
    from app.domains.tenant_access.repository import TenantAccessRepository
    from app.domains.tenant_access.service import TenantAccessService
    from tests.auth_helpers import create_support_access_token
    from tests.platform_access_helpers import create_test_platform_user

    tenant_id = uuid4()
    # This committed fixture is needed for two real transactions. Its unique
    # platform account history stays in the disposable test database; no audit
    # records or protected account history are deleted for cleanup.
    async with AsyncSession(db_engine) as setup:
        async with setup.begin():
            actor = await create_test_platform_user(setup, access_kind="developer")
            claims = decode_access_token(await create_support_access_token(setup, actor))
            for key, value in {
                "app.user_id": str(actor.id),
                "app.auth_session_id": str(claims["sid"]),
                "app.mfa_verified_at": str(claims["mfa_at"]),
                "app.support_session": "true",
                "app.tenant_id": "",
            }.items():
                await setup.execute(
                    text("SELECT set_config(:key,:value,true)"), {"key": key, "value": value}
                )
            await setup.execute(
                text("INSERT INTO tenant(id,name,contact_email) VALUES(:id,'Quota race',:email)"),
                {"id": tenant_id, "email": f"{tenant_id}@example.test"},
            )
            await TenantAccessService(TenantAccessRepository(setup)).update(
                tenant_id,
                AccessPolicyUpdate(expected_version=1, reason="Race limit", max_branches=1),
            )

    ready: asyncio.Queue[None] = asyncio.Queue()
    start = asyncio.Event()

    async def insert_branch() -> bool:
        try:
            async with db_engine.begin() as conn:
                await ready.put(None)
                await start.wait()
                await conn.execute(
                    text("INSERT INTO branch(tenant_id,name) VALUES(:id,'Concurrent quota')"),
                    {"id": tenant_id},
                )
            return True
        except DBAPIError as exc:
            assert "tenant_access_quota_exceeded" in str(exc.orig)
            return False

    tasks = [asyncio.create_task(insert_branch()) for _ in range(2)]
    try:
        await asyncio.wait_for(ready.get(), 3)
        await asyncio.wait_for(ready.get(), 3)
        start.set()
        outcomes = await asyncio.wait_for(asyncio.gather(*tasks), 5)
        assert sorted(outcomes) == [False, True]
        async with db_engine.begin() as conn:
            assert (
                await conn.scalar(
                    text("SELECT count(*) FROM branch WHERE tenant_id=:id"), {"id": tenant_id}
                )
                == 1
            )
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        async with db_engine.begin() as conn:
            for table in ("sync_stream", "sync_writer_epoch", "sync_node"):
                await conn.execute(
                    text(f"DELETE FROM {table} WHERE tenant_id=:id"), {"id": tenant_id}
                )
            await conn.execute(text("DELETE FROM branch WHERE tenant_id=:id"), {"id": tenant_id})
            await conn.execute(text("DELETE FROM tenant WHERE id=:id"), {"id": tenant_id})


@pytest.mark.asyncio
async def test_tenant_role_cannot_escape_policy_by_becoming_global(
    db_session: AsyncSession, make_tenant
) -> None:
    from tests.role_version_helpers import create_published_test_role

    tenant = await make_tenant()
    role = await create_published_test_role(
        db_session, tenant_id=tenant.id, name="Tenant-only policy role", permission_codes=()
    )
    with pytest.raises(DBAPIError, match="tenant_access_identity_immutable"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE role SET tenant_id=NULL WHERE id=:id"), {"id": role.id}
            )


@pytest.mark.asyncio
async def test_partial_policy_edit_preserves_other_restrictions(
    auth_client: AsyncClient, support_token: str, make_tenant
) -> None:
    tenant = await make_tenant()
    await _policy(
        auth_client,
        support_token,
        tenant.id,
        mode="readonly",
        blocked_modules=["pos"],
        max_branches=2,
        max_registers=3,
    )
    policy = await _policy(auth_client, support_token, tenant.id, expected_version=2, max_users=5)
    assert policy["mode"] == "readonly"
    assert policy["blocked_modules"] == ["pos"]
    assert policy["max_branches"] == 2
    assert policy["max_registers"] == 3
    assert policy["max_users"] == 5
