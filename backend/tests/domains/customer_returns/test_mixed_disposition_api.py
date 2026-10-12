"""HTTP decisions use the real app writer and separate resolve from history access."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal, cast
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.deps import CurrentUser
from app.domains.customer_returns.mixed_disposition_schemas import (
    MixedDispositionHistory,
    ResolvedMixedDisposition,
)
from tests.domains.customer_returns.test_mixed_disposition import request
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_unit_ledger import LedgerContext, seed_ledger_context
from tests.domains.pos.test_physical_cart_api import cart_actor
from tests.domains.pos.test_physical_cash_refund_status import assert_no_cost_fields
from tests.domains.pos.test_unit_return import runtime
from tests.isolation.test_mixed_disposition_migration import confirmed, unchanged_facts

db_session = receiving_session_fixture
PATH = "/api/v1/customer-returns/mixed/dispositions"


@asynccontextmanager
async def decision_actor(session: AsyncSession) -> AsyncIterator[CurrentUser]:
    async with cart_actor(session) as actor:
        actor.permissions = {"customer_returns.resolve", "customer_returns.view"}
        actor.permission_scopes = {
            code: frozenset({runtime(session).stock.branch_id}) for code in actor.permissions
        }
        yield actor


async def rejected_post(
    client: AsyncClient, session: AsyncSession, body: dict[str, object]
) -> Response:
    # HTTP converts DB denials to responses. Roll back its failed statement before
    # reusing this fixture's overridden session to assert unchanged history.
    savepoint = await session.begin_nested()
    try:
        return await client.post(PATH, json=body)
    finally:
        await savepoint.rollback()


@pytest.mark.parametrize("intake_kind", ["cash_refund", "zero_return"])
@pytest.mark.parametrize("kind", ["legacy_pack", "physical_unit"])
async def test_post_replay_and_history_preserve_whole_target_without_stock_or_cash_changes(
    client: AsyncClient,
    db_session: AsyncSession,
    intake_kind: Literal["cash_refund", "zero_return"],
    kind: Literal["legacy_pack", "physical_unit"],
) -> None:
    rows = await confirmed(db_session, intake_kind=intake_kind)
    row = next(row for row in rows if row.inventory_kind == kind)
    chosen = request(row, decision_operation_id=uuid4(), comment="\u2003 Проверено \t")
    body = {**chosen.model_dump(mode="json"), "comment": "\u2003 Проверено \t"}
    before = await unchanged_facts(db_session)
    async with decision_actor(db_session):
        empty = await client.get(PATH)
        assert empty.status_code == 200 and empty.json()["total"] == 0
        response = await client.post(PATH, json=body)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        saved = ResolvedMixedDisposition.model_validate(response.json())
        assert saved.request() == chosen
        assert saved.comment == "Проверено"
        assert saved.tenant_id == runtime(db_session).stock.tenant_id
        assert saved.actor_id == runtime(db_session).actor_id
        assert saved.intake_kind == intake_kind and saved.target.inventory_kind == kind
        target = saved.target.model_dump(mode="json")
        if kind == "legacy_pack":
            assert target["legacy_quantity"] == str(row.legacy_quantity)
            assert target["unit_label"] == "уп." and "base_units" not in target
        else:
            assert target["base_units"] == row.base_units
            assert target["unit_label"] == row.unit_label and "legacy_quantity" not in target
        replay = await client.post(PATH, json=body)
        assert replay.status_code == 200 and replay.json() == response.json()
        assert replay.headers["cache-control"] == "private, no-store"
        history = await client.get(PATH)
        assert history.status_code == 200, history.text
        assert history.headers["cache-control"] == "private, no-store"
        parsed = MixedDispositionHistory.model_validate(history.json())
        assert parsed.items == [saved] and parsed.total == 1
        pending = await client.get("/api/v1/customer-returns/mixed")
        assert pending.status_code == 200 and pending.json()["total"] == 1
        assert pending.json()["items"][0]["inventory_kind"] != kind
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert_no_cost_fields(history.json())
    assert await unchanged_facts(db_session) == before


async def test_real_resolve_only_actor_can_post_and_another_resolver_replays_original_author(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    row = (await confirmed(db_session))[0]
    context = runtime(db_session)
    before = await unchanged_facts(db_session)
    body = request(row, decision_operation_id=uuid4()).model_dump(mode="json")
    await db_session.commit()
    actors = [
        await seed_receiving_actor(
            db_engine, maintenance_engine, context.stock, permissions=("customer_returns.resolve",)
        )
        for _ in range(2)
    ]
    replies = []
    for actor_id in actors:
        await set_receiving_context(db_session, context.stock, actor_id)
        assert await db_session.scalar(text("SELECT session_user")) == "aurum_app"
        assert await db_session.scalar(text("SELECT count(*) FROM pos_mixed_return_command")) == 0
        async with decision_actor(db_session) as actor:
            actor.user_id = actor_id
            actor.permissions = {"customer_returns.resolve"}
            response = await client.post(PATH, json=body)
            assert response.status_code == 200, response.text
            replies.append(response.json())
            assert (await client.get(PATH)).status_code == 403
        await db_session.commit()
    assert replies[0] == replies[1] and replies[0]["actor_id"] == str(actors[0])
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await unchanged_facts(db_session) == before


@pytest.mark.parametrize("status", ["readonly", "archived"])
async def test_readonly_tenant_blocks_new_post_and_replay_but_allows_history(
    client: AsyncClient,
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    status: str,
) -> None:
    row = (await confirmed(db_session))[0]
    context = runtime(db_session)
    body = request(row, decision_operation_id=uuid4()).model_dump(mode="json")
    async with decision_actor(db_session):
        response = await client.post(PATH, json=body)
    assert response.status_code == 200, response.text
    await db_session.commit()
    async with maintenance_engine.begin() as owner:
        await owner.execute(
            text("UPDATE tenant SET status=:status WHERE id=:tenant"),
            {"status": status, "tenant": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await unchanged_facts(db_session)
    async with decision_actor(db_session):
        for candidate in (body, {**body, "decision_operation_id": str(uuid4())}):
            denied = await rejected_post(client, db_session, candidate)
            assert denied.status_code == 422
            assert denied.json()["error"]["details"] == {"status": status}
        history = await client.get(PATH)
        assert history.status_code == 200 and history.json()["items"] == [response.json()]
        assert history.headers["cache-control"] == "private, no-store"
    assert await unchanged_facts(db_session) == before


@pytest.mark.parametrize("profile", ["view_only", "refund_only", "empty_scope"])
async def test_post_requires_resolve_permission_with_usable_scope(
    client: AsyncClient, db_session: AsyncSession, profile: str
) -> None:
    row = (await confirmed(db_session))[0]
    body = request(row, decision_operation_id=uuid4()).model_dump(mode="json")
    before = await unchanged_facts(db_session)
    async with decision_actor(db_session) as actor:
        if profile == "view_only":
            actor.permissions = {"customer_returns.view"}
        elif profile == "refund_only":
            actor.permissions = {"pos.refund"}
        else:
            actor.permission_scopes["customer_returns.resolve"] = frozenset()
        denied = await rejected_post(client, db_session, body)
        assert denied.status_code == 403
    async with decision_actor(db_session):
        history = await client.get(PATH)
        assert history.status_code == 200 and history.json()["total"] == 0
    assert await unchanged_facts(db_session) == before


@pytest.mark.parametrize("scope", ["branch", "tenant"])
async def test_actual_database_assignments_limit_new_posts_replays_and_history(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    scope: str,
) -> None:
    row = (await confirmed(db_session))[0]
    context = runtime(db_session)
    body = request(row, decision_operation_id=uuid4()).model_dump(mode="json")
    async with decision_actor(db_session):
        saved = await client.post(PATH, json=body)
    assert saved.status_code == 200, saved.text
    await db_session.commit()
    async with AsyncSession(db_engine) as support:
        async with support.begin():
            await support.execute(text("SELECT set_config('app.support_session','true',true)"))
            if scope == "tenant":
                other = await seed_ledger_context(support)
            else:
                branch_id = await support.scalar(
                    text(
                        "INSERT INTO branch(tenant_id,name) "
                        "VALUES(:tenant,'HTTP disposition scope') RETURNING id"
                    ),
                    {"tenant": context.stock.tenant_id},
                )
                assert isinstance(branch_id, UUID)
                other = LedgerContext(context.stock.tenant_id, branch_id, context.stock.catalog_id)
    other_actor = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        other,
        permissions=("customer_returns.resolve", "customer_returns.view"),
    )
    await set_receiving_context(db_session, other, other_actor)
    async with decision_actor(db_session) as actor:
        actor.user_id = other_actor
        actor.tenant_id = other.tenant_id
        # Even tenant-wide HTTP claims cannot expand the actual branch assignment.
        actor.permission_scopes = {code: None for code in actor.permissions}
        for candidate in (body, {**body, "decision_operation_id": str(uuid4())}):
            denied = await rejected_post(client, db_session, candidate)
            assert denied.status_code == (403 if scope == "branch" else 404), denied.text
        history = await client.get(
            PATH,
            params={
                "branch_id": str(context.stock.branch_id),
                "tenant_id": str(context.stock.tenant_id),
            },
        )
        assert history.status_code == 200 and history.json()["items"] == []
        assert history.json()["total"] == 0
    await set_receiving_context(db_session, context.stock, context.actor_id)
    async with decision_actor(db_session):
        history = await client.get(PATH)
        assert history.status_code == 200 and history.json()["items"] == [saved.json()]


async def test_history_supports_view_only_access_pagination_and_branch_filters(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    rows = await confirmed(db_session)
    context = runtime(db_session)
    async with decision_actor(db_session):
        for row in rows:
            body = request(row, decision_operation_id=uuid4()).model_dump(mode="json")
            response = await client.post(PATH, json=body)
            assert response.status_code == 200, response.text
    await db_session.commit()
    viewer = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("customer_returns.view",)
    )
    await set_receiving_context(db_session, context.stock, viewer)
    async with decision_actor(db_session) as actor:
        actor.user_id = viewer
        actor.permissions = {"customer_returns.view"}
        pages = [
            await client.get(PATH, params={"page": page, "page_size": 1}) for page in (1, 2, 3)
        ]
        assert all(response.status_code == 200 for response in pages)
        parsed = [MixedDispositionHistory.model_validate(response.json()) for response in pages]
        assert [page.total for page in parsed] == [2, 2, 2]
        assert [len(page.items) for page in parsed] == [1, 1, 0]
        assert parsed[0].items[0].decision_operation_id != parsed[1].items[0].decision_operation_id
        filtered = await client.get(PATH, params={"branch_id": str(context.stock.branch_id)})
        assert filtered.status_code == 200 and filtered.json()["total"] == 2
        absent = await client.get(PATH, params={"branch_id": str(uuid4())})
        assert absent.status_code == 200 and absent.json()["total"] == 0
        actor.permission_scopes["customer_returns.view"] = frozenset({uuid4()})
        outside = await client.get(PATH)
        assert outside.status_code == 200 and outside.json()["items"] == []


@pytest.mark.parametrize("conflict", ["same_key_changed_content", "new_key_same_target"])
async def test_conflicts_do_not_duplicate_history_or_change_source_facts(
    client: AsyncClient, db_session: AsyncSession, conflict: str
) -> None:
    row = (await confirmed(db_session))[0]
    body = request(row, decision_operation_id=uuid4()).model_dump(mode="json")
    before = await unchanged_facts(db_session)
    async with decision_actor(db_session):
        saved = await client.post(PATH, json=body)
        assert saved.status_code == 200, saved.text
        changed = (
            {**body, "comment": "Another decision"}
            if conflict == "same_key_changed_content"
            else {**body, "decision_operation_id": str(uuid4())}
        )
        denied = await rejected_post(client, db_session, changed)
        assert denied.status_code == 409, denied.text
        history = await client.get(PATH)
        assert history.status_code == 200 and history.json()["items"] == [saved.json()]
    assert await unchanged_facts(db_session) == before


async def test_strict_http_payload_rejects_identity_quantity_and_authority_claims(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    row = (await confirmed(db_session))[0]
    body = request(row, decision_operation_id=uuid4()).model_dump(mode="json")
    target = cast(dict[str, object], body["target"])
    before = await unchanged_facts(db_session)
    async with decision_actor(db_session):
        patches = [
            {"tenant_id": str(uuid4())},
            {"actor_id": str(uuid4())},
            {"branch_id": str(uuid4())},
            {"permissions": ["customer_returns.resolve"]},
            {"quantity": "0.001"},
            {"expected_total_amount": "0.00"},
            {"decision_operation_id": str(row.operation_id)},
            {"disposition_type": "restock"},
            {"target": {**target, "base_units": 1}},
            {"target": {**target, "legacy_quantity": "0.001"}},
        ]
        for patch in patches:
            denied = await rejected_post(client, db_session, {**body, **patch})
            assert denied.status_code == 422, denied.text
        history = await client.get(PATH)
        assert history.status_code == 200 and history.json()["total"] == 0
    assert await unchanged_facts(db_session) == before


@pytest.mark.parametrize(
    "params",
    [
        {"page": "0"},
        {"page": "2147483648"},
        {"page_size": "0"},
        {"page_size": "201"},
        {"branch_id": "not-a-uuid"},
    ],
)
async def test_history_rejects_invalid_pagination_and_branch_params(
    client: AsyncClient, db_session: AsyncSession, params: dict[str, str]
) -> None:
    async with decision_actor(db_session):
        response = await client.get(PATH, params=params)
    assert response.status_code == 422


async def test_both_endpoints_require_current_tenant_context(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    row = (await confirmed(db_session))[0]
    body = request(row, decision_operation_id=uuid4()).model_dump(mode="json")
    before = await unchanged_facts(db_session)
    async with decision_actor(db_session) as actor:
        actor.tenant_id = None
        assert (await rejected_post(client, db_session, body)).status_code == 403
        assert (await client.get(PATH)).status_code == 403
    assert await unchanged_facts(db_session) == before
