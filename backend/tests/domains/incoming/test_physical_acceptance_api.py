"""Receiving HTTP selects exactly one journal under real application RLS."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.deps import CurrentUser, current_user, get_db
from app.main import app
from tests.domains.incoming.test_unit_acceptance import (
    IncomingContext,
    ReceivingRuntime,
    seed_prepared_incoming,
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance_v2 import seed_v2_incoming

db_session = receiving_session_fixture

TABLES = (
    "unit_stock_operation_v2",
    "unit_stock_lot_v2",
    "unit_stock_entry_v2",
    "batch",
    "batch_movement",
    "unit_stock_operation",
    "unit_stock_lot",
    "unit_stock_entry",
)


@asynccontextmanager
async def receiving_actor(session: AsyncSession) -> AsyncIterator[CurrentUser]:
    runtime = cast(ReceivingRuntime, session.info["receiving_runtime"])
    actor = CurrentUser(
        user_id=runtime.actor_id,
        tenant_id=runtime.stock.tenant_id,
        is_developer=False,
        is_administrator=False,
        permissions={"incoming.finalize", "incoming.view"},
        permission_scopes={
            code: frozenset({runtime.stock.branch_id})
            for code in ("incoming.finalize", "incoming.view")
        },
        password_verified_at=datetime.now(UTC),
    )

    async def override_db() -> AsyncIterator[AsyncSession]:
        yield session

    async def override_user() -> CurrentUser:
        return actor

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[current_user] = override_user
    try:
        yield actor
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(current_user, None)


async def journal_counts(session: AsyncSession, incoming: IncomingContext) -> tuple[int, ...]:
    values: list[int] = []
    for table in TABLES:
        count = await session.scalar(
            text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
            {"tenant": incoming.stock.tenant_id},
        )
        assert isinstance(count, int)
        values.append(count)
    return tuple(values)


def acceptance_path(incoming: IncomingContext) -> str:
    return f"/api/v1/incoming/{incoming.document_id}/accept"


async def physical_body(client: AsyncClient, incoming: IncomingContext) -> dict[str, str]:
    response = await client.get(f"/api/v1/incoming/{incoming.document_id}")
    assert response.status_code == 200
    revision = response.json()["acceptance_revision"]
    assert isinstance(revision, str) and len(revision) == 64
    return {"stock_mode": "physical_unit_v2", "expected_revision": revision}


@pytest.mark.parametrize("tenant_scope", [False, True])
async def test_physical_acceptance_posts_once_and_rejects_legacy_replay(
    client: AsyncClient, db_session: AsyncSession, tenant_scope: bool
) -> None:
    incoming = await seed_v2_incoming(db_session)
    assert await journal_counts(db_session, incoming) == (0,) * len(TABLES)
    async with receiving_actor(db_session) as actor:
        if tenant_scope:
            actor.permission_scopes["incoming.finalize"] = None
        details = await client.get(f"/api/v1/incoming/{incoming.document_id}")
        assert details.status_code == 200
        assert details.json()["physical_receiving_ready"] is True
        assert details.json()["stock_mode"] is None
        response = await client.post(
            acceptance_path(incoming), json=await physical_body(client, incoming)
        )
        assert response.status_code == 200
        accepted = response.json()
        assert accepted["status"] == "accepted"
        assert (
            await db_session.scalar(
                text("SELECT accepted_by FROM incoming_document WHERE id=:id"),
                {"id": incoming.document_id},
            )
            == incoming.actor_id
        )
        assert await journal_counts(db_session, incoming) == (1, 1, 1, 0, 0, 0, 0, 0)
        repeated = await client.post(
            acceptance_path(incoming), json={"stock_mode": "physical_unit_v2"}
        )
        assert repeated.status_code == 200
        assert repeated.json()["accepted_at"] == accepted["accepted_at"]
        details = await client.get(f"/api/v1/incoming/{incoming.document_id}")
        assert details.json()["stock_mode"] == "physical_unit_v2"
        assert (await client.post(acceptance_path(incoming))).status_code == 409
        assert (
            await client.post(acceptance_path(incoming), json={"stock_mode": "legacy_pack"})
        ).status_code == 409
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert await journal_counts(db_session, incoming) == (1, 1, 1, 0, 0, 0, 0, 0)
    lot = (
        await db_session.execute(
            text(
                "SELECT branch_id,closed_packs,initial_closed_packs,packaging "
                "FROM unit_stock_lot_v2 WHERE source_item_id=:item"
            ),
            {"item": incoming.item_ids[0]},
        )
    ).one()
    assert lot.branch_id == incoming.stock.branch_id
    assert lot.closed_packs == lot.initial_closed_packs == 2
    assert lot.packaging["units_per_pack"] == 30
    assert lot.packaging["units_per_blister"] == 10


@pytest.mark.parametrize("explicit_mode", [False, True])
async def test_default_and_explicit_legacy_acceptance_remain_idempotent(
    client: AsyncClient, db_session: AsyncSession, explicit_mode: bool
) -> None:
    incoming = await seed_v2_incoming(db_session)
    async with receiving_actor(db_session):
        if explicit_mode:
            response = await client.post(
                acceptance_path(incoming), json={"stock_mode": "legacy_pack"}
            )
        else:
            response = await client.post(acceptance_path(incoming))
        assert response.status_code == 200
        assert response.json()["status"] == "accepted"
        before = await journal_counts(db_session, incoming)
        assert before == (0, 0, 0, 1, 1, 0, 0, 0)
        repeated = await client.post(acceptance_path(incoming))
        assert repeated.status_code == 200
        assert repeated.json()["accepted_at"] == response.json()["accepted_at"]
        assert (
            await client.post(acceptance_path(incoming), json={"stock_mode": "physical_unit_v2"})
        ).status_code == 409
    assert await journal_counts(db_session, incoming) == before


@pytest.mark.parametrize("mode", ["physical", "unit_v1", "", 1, True])
async def test_unknown_stock_mode_is_rejected_without_writes(
    client: AsyncClient, db_session: AsyncSession, mode: object
) -> None:
    incoming = await seed_v2_incoming(db_session)
    async with receiving_actor(db_session):
        response = await client.post(acceptance_path(incoming), json={"stock_mode": mode})
        assert response.status_code == 422
    assert await journal_counts(db_session, incoming) == (0,) * len(TABLES)


@pytest.mark.parametrize("denial", ["permission", "branch", "actor", "tenant", "step_up"])
async def test_acceptance_authority_is_checked_before_stock_changes(
    client: AsyncClient, db_session: AsyncSession, denial: str
) -> None:
    incoming = await seed_v2_incoming(db_session)
    async with receiving_actor(db_session) as actor:
        body = await physical_body(client, incoming)
        if denial == "permission":
            actor.permissions.clear()
        elif denial == "branch":
            actor.permission_scopes["incoming.finalize"] = frozenset({uuid4()})
        elif denial == "actor":
            actor.user_id = uuid4()
        elif denial == "tenant":
            actor.tenant_id = uuid4()
        else:
            actor.password_verified_at = None
        response = await client.post(acceptance_path(incoming), json=body)
        assert response.status_code in {403, 404}
    assert await journal_counts(db_session, incoming) == (0,) * len(TABLES)


@pytest.mark.parametrize("invalid", ["snapshot", "expiry"])
async def test_bad_second_source_rolls_back_every_physical_journal_component(
    client: AsyncClient, db_session: AsyncSession, invalid: str
) -> None:
    runtime = cast(ReceivingRuntime, db_session.info["receiving_runtime"])
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET unit_sale_preparation="
            "jsonb_set(unit_sale_preparation,'{units_per_blister}','10') WHERE id=:id"
        ),
        {"id": runtime.stock.catalog_id},
    )
    incoming = await seed_prepared_incoming(
        db_session, catalogs=(runtime.stock.catalog_id, runtime.stock.catalog_id)
    )
    # The service claims sources in UUID order. Corrupt the last one so the
    # preceding claim really has to be rolled back, rather than failing first.
    second = max(incoming.item_ids, key=str)
    assignments = {
        "snapshot": "unit_packaging_snapshot_v2=NULL",
        "expiry": "expires_at=CURRENT_DATE - 1",
    }
    await db_session.execute(
        text(f"UPDATE incoming_item SET {assignments[invalid]} WHERE id=:id"),
        {"id": second},
    )
    async with receiving_actor(db_session):
        response = await client.post(
            acceptance_path(incoming), json=await physical_body(client, incoming)
        )
        assert response.status_code == 422
    assert await journal_counts(db_session, incoming) == (0,) * len(TABLES)
    assert (
        await db_session.scalar(
            text("SELECT status FROM incoming_document WHERE id=:id"),
            {"id": incoming.document_id},
        )
        == "draft"
    )
    assert (
        await db_session.scalar(
            text(
                "SELECT count(*) FROM incoming_item WHERE document_id=:id "
                "AND created_unit_lot_v2_id IS NOT NULL"
            ),
            {"id": incoming.document_id},
        )
        == 0
    )


async def test_physical_receiving_requires_reviewed_revision_without_writes(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    incoming = await seed_v2_incoming(db_session)
    async with receiving_actor(db_session):
        response = await client.post(
            acceptance_path(incoming), json={"stock_mode": "physical_unit_v2"}
        )
        assert response.status_code == 422
    assert await journal_counts(db_session, incoming) == (0,) * len(TABLES)


async def test_live_db_permission_denies_stale_claimed_finalize_capability(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    runtime = cast(ReceivingRuntime, db_session.info["receiving_runtime"])
    # Publish the fresh actor before document writes acquire policy locks.
    limited_actor = await seed_receiving_actor(
        db_engine, maintenance_engine, runtime.stock, permissions=("incoming.view",)
    )
    incoming = await seed_v2_incoming(db_session)
    async with receiving_actor(db_session) as actor:
        reviewed = await physical_body(client, incoming)
        await set_receiving_context(db_session, runtime.stock, limited_actor)
        actor.user_id = limited_actor
        # The dependency advertises finalize, while actual DB authority has
        # only view: the receiving journal must reject this stale claim.
        response = await client.post(acceptance_path(incoming), json=reviewed)
        assert response.status_code in {403, 404}
    await set_receiving_context(db_session, runtime.stock, runtime.actor_id)
    assert await journal_counts(db_session, incoming) == (0,) * len(TABLES)


async def test_reviewed_document_change_is_rejected_then_fresh_review_can_receive(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    incoming = await seed_v2_incoming(db_session)
    async with receiving_actor(db_session):
        reviewed = await physical_body(client, incoming)
        await db_session.execute(
            text("UPDATE incoming_item SET qty=3 WHERE id=:id"),
            {"id": incoming.item_ids[0]},
        )
        response = await client.post(acceptance_path(incoming), json=reviewed)
        assert response.status_code == 409
        assert await journal_counts(db_session, incoming) == (0,) * len(TABLES)
        fresh = await physical_body(client, incoming)
        assert fresh["expected_revision"] != reviewed["expected_revision"]
        response = await client.post(acceptance_path(incoming), json=fresh)
        assert response.status_code == 200
    assert await journal_counts(db_session, incoming) == (1, 1, 1, 0, 0, 0, 0, 0)
    assert (
        await db_session.scalar(
            text("SELECT closed_packs FROM unit_stock_lot_v2 WHERE source_item_id=:id"),
            {"id": incoming.item_ids[0]},
        )
        == 3
    )
