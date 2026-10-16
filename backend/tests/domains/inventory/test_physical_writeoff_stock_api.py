"""Write-off selection reads expose physical identities under live branch authority."""

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.incoming.test_unit_acceptance_v2 import accept_v2, seed_v2_incoming
from tests.domains.inventory.test_legacy_opened_transition import opened, partial_source
from tests.domains.inventory.test_legacy_transition_posting import post as post_transition
from tests.domains.inventory.test_physical_writeoff_posting import post, selection, writeoff_actor
from tests.domains.inventory.test_unit_ledger_v2_dispensing import (
    cart_line,
    dispense,
    packs,
    receive_source,
    remaining,
)
from tests.domains.inventory.test_unit_physical_blocking import set_blocked
from tests.domains.pos.test_physical_cash_refund_status import assert_no_cost_fields
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture
PATH = "/api/v1/batches/physical"


def path(lot_id: UUID) -> str:
    return f"{PATH}/{lot_id}/write-off-stock"


async def test_read_safe_physical_state_then_exclude_exhausted_tombstone(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await receive_source(db_session)
    await dispense(db_session, source, (cart_line(source, "unit", 3),))
    pack_id, pack_state, count = (await packs(db_session, source))[0]
    params = {"branch_id": str(source.incoming.stock.branch_id)}
    async with writeoff_actor(db_session):
        response = await client.get(path(source.lot_id), params=params)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        payload = response.json()
        assert set(payload) == {"lot", "opened_packs", "total", "page", "page_size"}
        assert payload["lot"]["base_units"] == "57"
        assert payload["lot"]["packaging"] == source.packaging.model_dump(mode="json")
        assert payload["opened_packs"] == [
            {"pack_id": str(pack_id), "remaining_units": str(count), "state": pack_state}
        ]
        assert (payload["total"], payload["page"], payload["page_size"]) == (1, 1, 50)
        assert_no_cost_fields(payload)
        assert await remaining(db_session, source) == 57
        await post(
            db_session, source, selection(pack_id=pack_id, blisters=((1, 7), (2, 10), (3, 10)))
        )
        exhausted = await client.get(path(source.lot_id), params=params)
        assert exhausted.status_code == 200
        assert exhausted.json()["opened_packs"] == []
        assert exhausted.json()["total"] == 0
        assert exhausted.json()["lot"]["base_units"] == "30"


async def test_page_order_and_totals_cover_all_opened_packs(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    low, high = UUID(int=931), UUID(int=932)
    source = await partial_source(
        db_session,
        quantity="1.000",
        closed=0,
        observed=[opened(15, pack_id=high), opened(15, pack_id=low)],
    )
    result = await post_transition(db_session, source.batch_id, source.record_id, uuid4())
    params = {"branch_id": str(runtime(db_session).stock.branch_id), "page_size": 1}
    async with writeoff_actor(db_session):
        for page, expected in ((1, low), (2, high), (3, None)):
            response = await client.get(path(result["lot_id"]), params={**params, "page": page})
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["total"] == 2
            assert payload["lot"]["opened_units"] == payload["lot"]["base_units"] == "30"
            assert payload["page"] == page and payload["page_size"] == 1
            assert [item["pack_id"] for item in payload["opened_packs"]] == (
                [str(expected)] if expected is not None else []
            )
            assert_no_cost_fields(payload)


@pytest.mark.parametrize("permissions", [("batches.view",), ("batches.write_off",), ("pos.sell",)])
async def test_live_database_permissions_override_overstated_http_claims(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    permissions: tuple[str, ...],
) -> None:
    source = await receive_source(db_session)
    original = runtime(db_session)
    await db_session.commit()
    actor_id = await seed_receiving_actor(
        db_engine, maintenance_engine, original.stock, permissions=permissions
    )
    db_session.info["receiving_runtime"] = replace(original, actor_id=actor_id)
    await set_receiving_context(db_session, original.stock, actor_id)
    async with writeoff_actor(db_session):
        response = await client.get(
            path(source.lot_id), params={"branch_id": str(original.stock.branch_id)}
        )
        assert response.status_code == 403, response.text


@pytest.mark.parametrize("changed", ["tenant", "branch", "actor"])
async def test_database_identity_cannot_be_overridden_by_http_context(
    client: AsyncClient, db_session: AsyncSession, changed: str
) -> None:
    source = await receive_source(db_session)
    branch_id = source.incoming.stock.branch_id
    async with writeoff_actor(db_session) as actor:
        if changed == "tenant":
            actor.tenant_id = uuid4()
        elif changed == "actor":
            actor.user_id = uuid4()
        else:
            branch_id = uuid4()
            actor.permission_scopes = {code: frozenset({branch_id}) for code in actor.permissions}
        response = await client.get(path(source.lot_id), params={"branch_id": str(branch_id)})
        assert response.status_code == 403


async def test_missing_lot_and_http_scope_are_not_disclosed(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    source = await receive_source(db_session)
    params = {"branch_id": str(source.incoming.stock.branch_id)}
    async with writeoff_actor(db_session) as actor:
        assert (await client.get(path(uuid4()), params=params)).status_code == 404
        wrong_branch = await client.get(path(source.lot_id), params={"branch_id": str(uuid4())})
        assert wrong_branch.status_code == 403
        actor.permissions = {"batches.write_off"}
        assert (await client.get(path(source.lot_id), params=params)).status_code == 403


@pytest.mark.parametrize("params", [{"page": 0}, {"page_size": 0}, {"page_size": 201}])
async def test_invalid_pagination_is_rejected(
    client: AsyncClient, db_session: AsyncSession, params: dict[str, int]
) -> None:
    source = await receive_source(db_session)
    async with writeoff_actor(db_session):
        response = await client.get(
            path(source.lot_id),
            params={"branch_id": str(source.incoming.stock.branch_id), **params},
        )
        assert response.status_code == 422


async def test_blocked_stock_remains_readable_for_readonly_tenant(
    client: AsyncClient, db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    source = await receive_source(db_session)
    await set_blocked(db_session, source, True)
    context = runtime(db_session)
    await db_session.commit()
    async with maintenance_engine.begin() as owner:
        await owner.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:tenant"),
            {"tenant": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    async with writeoff_actor(db_session):
        response = await client.get(
            path(source.lot_id), params={"branch_id": str(context.stock.branch_id)}
        )
        assert response.status_code == 200, response.text
        assert response.json()["lot"]["is_blocked"] is True
        assert response.json()["lot"]["base_units"] == "60"


async def test_expired_stock_is_visible_without_rewriting_accepted_history(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    context = runtime(db_session)
    incoming = await seed_v2_incoming(db_session)
    await db_session.execute(
        text(
            "INSERT INTO tenant_settings(tenant_id,report_timezone) VALUES(:tenant,'Etc/GMT+12') "
            "ON CONFLICT(tenant_id) DO UPDATE SET report_timezone=EXCLUDED.report_timezone"
        ),
        {"tenant": context.stock.tenant_id},
    )
    await db_session.execute(
        text(
            "UPDATE incoming_item SET expires_at="
            "(statement_timestamp() AT TIME ZONE 'Etc/GMT+12')::date+1 WHERE id=:id"
        ),
        {"id": incoming.item_ids[0]},
    )
    await accept_v2(db_session, incoming)
    lot_id = await db_session.scalar(
        text("SELECT id FROM unit_stock_lot_v2 WHERE source_item_id=:id"),
        {"id": incoming.item_ids[0]},
    )
    assert isinstance(lot_id, UUID)
    await db_session.execute(
        text("UPDATE tenant_settings SET report_timezone='Etc/GMT-14' WHERE tenant_id=:tenant"),
        {"tenant": context.stock.tenant_id},
    )
    async with writeoff_actor(db_session):
        response = await client.get(
            path(lot_id), params={"branch_id": str(context.stock.branch_id)}
        )
        assert response.status_code == 200, response.text
        assert response.json()["lot"]["base_units"] == "60"
