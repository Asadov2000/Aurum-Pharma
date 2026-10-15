"""HTTP transition commands use saved identities and recover committed results safely."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.deps import CurrentUser
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_legacy_transition_posting import snapshot
from tests.domains.inventory.test_legacy_transition_source import (
    PERMISSIONS,
    accepted_batch,
    record_count,
)
from tests.domains.inventory.test_physical_inventory_api import inventory_actor
from tests.domains.inventory.test_unit_reconciliation_records_api import increase_quantity
from tests.domains.pos.test_physical_cash_refund_status import assert_no_cost_fields
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


def path(batch_id: UUID) -> str:
    return f"/api/v1/batches/{batch_id}/unit-transition"


def body(session: AsyncSession, record_id: UUID) -> dict[str, str]:
    return {
        "branch_id": str(runtime(session).stock.branch_id),
        "reconciliation_id": str(record_id),
        "command_id": str(uuid4()),
    }


@asynccontextmanager
async def transition_actor(session: AsyncSession) -> AsyncIterator[CurrentUser]:
    async with inventory_actor(session) as actor:
        actor.permissions = set(PERMISSIONS)
        actor.permission_scopes = {
            permission: frozenset({runtime(session).stock.branch_id}) for permission in PERMISSIONS
        }
        yield actor


async def test_post_replay_and_recovery_return_only_same_four_ids_without_extra_writes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    request = body(db_session, record_id)
    async with transition_actor(db_session):
        response = await client.post(path(batch_id), json=request)
        assert response.status_code == 200, response.text
        payload = response.json()
        assert set(payload) == {"command_id", "lot_id", "operation_id", "movement_id"}
        assert all(UUID(value) for value in payload.values())
        assert payload["command_id"] == request["command_id"]
        assert_no_cost_fields(payload)
        assert response.headers["cache-control"] == "private, no-store"
        await db_session.commit()
        context = runtime(db_session)
        await set_receiving_context(db_session, context.stock, context.actor_id)
        before = await snapshot(db_session)
        replay = await client.post(path(batch_id), json=request)
        recovered = await client.get(
            f"{path(batch_id)}/{request['command_id']}",
            params={"branch_id": request["branch_id"]},
        )
        assert replay.status_code == recovered.status_code == 200
        assert replay.json() == recovered.json() == payload
        assert replay.headers["cache-control"] == "private, no-store"
        assert recovered.headers["cache-control"] == "private, no-store"
        assert await snapshot(db_session) == before


@pytest.mark.parametrize("field", ["amount", "cost", "quantity", "actor_id", "tenant_id", "plan"])
async def test_post_rejects_client_supplied_values_and_authority(
    client: AsyncClient, db_session: AsyncSession, field: str
) -> None:
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    before = await snapshot(db_session)
    async with transition_actor(db_session):
        response = await client.post(
            path(batch_id), json={**body(db_session, record_id), field: "1"}
        )
    assert response.status_code == 422
    assert await snapshot(db_session) == before


@pytest.mark.parametrize("field", ["branch_id", "reconciliation_id", "command_id"])
async def test_malformed_identifiers_do_not_claim_a_command(
    client: AsyncClient, db_session: AsyncSession, field: str
) -> None:
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    before = await snapshot(db_session)
    async with transition_actor(db_session):
        response = await client.post(
            path(batch_id), json={**body(db_session, record_id), field: "invalid"}
        )
    assert response.status_code == 422
    assert await snapshot(db_session) == before


async def test_stale_saved_count_returns_validation_error_without_writes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    await increase_quantity(db_session, batch_id)
    before = await snapshot(db_session)
    async with transition_actor(db_session):
        response = await client.post(path(batch_id), json=body(db_session, record_id))
    assert response.status_code == 422, response.text
    assert await snapshot(db_session) == before


async def test_reused_command_with_different_record_is_conflict(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    request = body(db_session, record_id)
    async with transition_actor(db_session):
        saved = await client.post(path(batch_id), json=request)
        assert saved.status_code == 200, saved.text
        before = await snapshot(db_session)
        conflict = await client.post(
            path(batch_id), json={**request, "reconciliation_id": str(uuid4())}
        )
        assert conflict.status_code == 409, conflict.text
        assert await snapshot(db_session) == before


@pytest.mark.parametrize("wrong", ["record", "branch", "tenant"])
async def test_post_rejects_missing_record_and_untrusted_scope(
    client: AsyncClient, db_session: AsyncSession, wrong: str
) -> None:
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    request = body(db_session, uuid4() if wrong == "record" else record_id)
    before = await snapshot(db_session)
    async with transition_actor(db_session) as actor:
        if wrong == "branch":
            request["branch_id"] = str(uuid4())
        elif wrong == "tenant":
            actor.tenant_id = uuid4()
        response = await client.post(path(batch_id), json=request)
        assert response.status_code == (404 if wrong == "record" else 403), response.text
    assert await snapshot(db_session) == before


@pytest.mark.parametrize("wrong", ["batch", "command", "branch"])
async def test_recovery_does_not_leak_unrelated_identities(
    client: AsyncClient, db_session: AsyncSession, wrong: str
) -> None:
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    request = body(db_session, record_id)
    async with transition_actor(db_session):
        saved = await client.post(path(batch_id), json=request)
        assert saved.status_code == 200, saved.text
        before = await snapshot(db_session)
        response = await client.get(
            f"{path(uuid4() if wrong == 'batch' else batch_id)}/"
            f"{uuid4() if wrong == 'command' else request['command_id']}",
            params={"branch_id": str(uuid4()) if wrong == "branch" else request["branch_id"]},
        )
        assert response.status_code == (403 if wrong == "branch" else 404), response.text
        assert await snapshot(db_session) == before


@pytest.mark.parametrize("missing", PERMISSIONS)
async def test_forged_http_permissions_cannot_bypass_actual_database_access(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    missing: str,
) -> None:
    context = runtime(db_session)
    other = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        context.stock,
        permissions=tuple(permission for permission in PERMISSIONS if permission != missing),
    )
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    request = body(db_session, record_id)
    async with transition_actor(db_session) as actor:
        saved = await client.post(path(batch_id), json=request)
        assert saved.status_code == 200, saved.text
        before = await snapshot(db_session)
        await set_receiving_context(db_session, context.stock, other)
        actor.user_id = other
        denied = await client.post(path(batch_id), json={**request, "command_id": str(uuid4())})
        read = await client.get(
            f"{path(batch_id)}/{request['command_id']}", params={"branch_id": request["branch_id"]}
        )
        assert denied.status_code == read.status_code == 403
        await set_receiving_context(db_session, context.stock, context.actor_id)
        assert await snapshot(db_session) == before


async def test_lost_reply_can_be_recovered_in_readonly_tenant_but_not_reposted(
    client: AsyncClient, db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    context = runtime(db_session)
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    request = body(db_session, record_id)
    async with transition_actor(db_session):
        saved = await client.post(path(batch_id), json=request)
        assert saved.status_code == 200, saved.text
        await db_session.commit()
        async with maintenance_engine.begin() as owner:
            await owner.execute(
                text("UPDATE tenant SET status='readonly' WHERE id=:tenant"),
                {"tenant": context.stock.tenant_id},
            )
        await set_receiving_context(db_session, context.stock, context.actor_id)
        before = await snapshot(db_session)
        denied = await client.post(path(batch_id), json=request)
        assert denied.status_code == 422, denied.text
        recovered = await client.get(
            f"{path(batch_id)}/{request['command_id']}", params={"branch_id": request["branch_id"]}
        )
        assert recovered.status_code == 200 and recovered.json() == saved.json()
        assert await snapshot(db_session) == before


async def test_another_fully_authorized_actor_cannot_recover_someone_elses_command(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    context = runtime(db_session)
    other = await seed_receiving_actor(db_engine, maintenance_engine, context.stock)
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    request = body(db_session, record_id)
    async with transition_actor(db_session) as actor:
        saved = await client.post(path(batch_id), json=request)
        assert saved.status_code == 200, saved.text
        before = await snapshot(db_session)
        await set_receiving_context(db_session, context.stock, other)
        actor.user_id = other
        response = await client.get(
            f"{path(batch_id)}/{request['command_id']}", params={"branch_id": request["branch_id"]}
        )
        assert response.status_code == 404, response.text
        await set_receiving_context(db_session, context.stock, context.actor_id)
        assert await snapshot(db_session) == before
