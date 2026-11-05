"""HTTP refund recovery reads an actor's saved payout without further side effects."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.deps import CurrentUser
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.pos.physical_cash_refund_schemas import PhysicalCashRefundResultV3
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.pos.test_physical_cart_api import cart_actor
from tests.domains.pos.test_physical_cash_checkout import complete
from tests.domains.pos.test_physical_cash_refund import close_shift, facts, refund
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


def status_path(parent_sale_id: UUID, operation_id: UUID) -> str:
    return f"/api/v1/sales/{parent_sale_id}/physical-cash-refunds/operations/{operation_id}"


@asynccontextmanager
async def refund_actor(session: AsyncSession) -> AsyncIterator[CurrentUser]:
    async with cart_actor(session) as actor:
        actor.permissions = {"pos.refund"}
        actor.permission_scopes = {"pos.refund": frozenset({runtime(session).stock.branch_id})}
        yield actor


def assert_no_cost_fields(value: object) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            assert "cost" not in str(key).lower()
            assert_no_cost_fields(child)
    elif isinstance(value, list):
        for child in value:
            assert_no_cost_fields(child)


async def completed_refund(session: AsyncSession) -> PhysicalCashRefundResultV3:
    source = await checkout_source(session)
    item = await put_physical(session, source.sale.id, sale_format="pack", quantity=2)
    await complete(session, await saved_request(session, source.sale))
    return await refund(session, source.sale.id, item.id, source.sale.shift_id, 7)


async def test_completed_http_recovery_is_cost_free_uncached_and_read_only(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    result = await completed_refund(db_session)
    before = await facts(db_session)
    async with refund_actor(db_session):
        response = await client.get(status_path(result.parent_sale_id, result.operation_id))
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        payload = response.json()
        assert payload == {
            "state": "completed",
            "operation_id": str(result.operation_id),
            "parent_sale_id": str(result.parent_sale_id),
            "sale_id": str(result.sale_id),
            "total_amount": str(result.total_amount),
            "receipt": result.receipt.model_dump(mode="json"),
        }
        assert_no_cost_fields(payload)
        assert (
            await client.get(status_path(result.parent_sale_id, result.operation_id))
        ).json() == payload
    assert await facts(db_session) == before


async def test_unknown_operation_and_other_parent_do_not_expose_completed_refund(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    result = await completed_refund(db_session)
    other = await checkout_source(db_session)
    await put_physical(db_session, other.sale.id, sale_format="unit")
    await complete(db_session, await saved_request(db_session, other.sale))
    before = await facts(db_session)
    unknown = uuid4()
    async with refund_actor(db_session):
        for parent_id, operation_id in (
            (result.parent_sale_id, unknown),
            (other.sale.id, result.operation_id),
        ):
            response = await client.get(status_path(parent_id, operation_id))
            assert response.status_code == 200
            assert response.json() == {
                "state": "not_found",
                "operation_id": str(operation_id),
                "parent_sale_id": str(parent_id),
            }
            assert response.headers["cache-control"] == "private, no-store"
        assert (await client.get(status_path(uuid4(), result.operation_id))).status_code == 404
    assert await facts(db_session) == before


async def test_closed_shift_still_allows_recovery_of_immutable_completed_refund(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    result = await completed_refund(db_session)
    await close_shift(db_session, result.shift_id)
    before = await facts(db_session)
    async with refund_actor(db_session):
        response = await client.get(status_path(result.parent_sale_id, result.operation_id))
        assert response.status_code == 200
        assert response.json()["receipt"] == result.receipt.model_dump(mode="json")
        assert response.json()["state"] == "completed"
    assert await facts(db_session) == before


async def test_readonly_tenant_can_recover_completed_refund_without_posting(
    client: AsyncClient, db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    result = await completed_refund(db_session)
    context = runtime(db_session)
    await db_session.commit()
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:id"),
            {"id": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await facts(db_session)
    async with refund_actor(db_session):
        response = await client.get(status_path(result.parent_sale_id, result.operation_id))
        assert response.status_code == 200
        assert response.json()["receipt"] == result.receipt.model_dump(mode="json")
        assert response.json()["state"] == "completed"
    assert await facts(db_session) == before


@pytest.mark.parametrize("failure", ["permission", "branch", "claimed_actor", "claimed_tenant"])
async def test_http_claims_cannot_bypass_refund_permission_scope_or_actual_db_identity(
    client: AsyncClient, db_session: AsyncSession, failure: str
) -> None:
    result = await completed_refund(db_session)
    before = await facts(db_session)
    async with refund_actor(db_session) as actor:
        if failure == "permission":
            actor.permissions = {"sales.view.tenant"}
            actor.permission_scopes = {"sales.view.tenant": frozenset({result.branch_id})}
        elif failure == "branch":
            actor.permission_scopes = {"pos.refund": frozenset({uuid4()})}
        elif failure == "claimed_actor":
            actor.user_id = uuid4()
        else:
            actor.tenant_id = uuid4()
        response = await client.get(status_path(result.parent_sale_id, result.operation_id))
        assert response.status_code == 403
        assert "receipt" not in response.json()
    assert await facts(db_session) == before


async def test_actual_actor_without_refund_right_cannot_recover_using_claimed_permission(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    result = await completed_refund(db_session)
    context = runtime(db_session)
    before = await facts(db_session)
    await db_session.commit()
    actor_id = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.sell",)
    )
    await set_receiving_context(db_session, context.stock, actor_id)
    async with refund_actor(db_session) as claimed:
        claimed.user_id = actor_id
        response = await client.get(status_path(result.parent_sale_id, result.operation_id))
        assert response.status_code == 403
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await facts(db_session) == before


async def test_branch_scoped_refund_only_actor_recovers_without_sell_permission(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    result = await completed_refund(db_session)
    context = runtime(db_session)
    before = await facts(db_session)
    await db_session.commit()
    refund_only = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text(
                "UPDATE user_assignment SET role_id=(SELECT role_id FROM user_assignment "
                "WHERE user_id=:other AND tenant_id=:tenant AND branch_id=:branch) "
                "WHERE user_id=:actor AND tenant_id=:tenant AND branch_id=:branch"
            ),
            {
                "other": refund_only,
                "actor": context.actor_id,
                "tenant": context.stock.tenant_id,
                "branch": context.stock.branch_id,
            },
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert (
        await db_session.scalar(
            text("SELECT tenant_actor_has_scoped_permission(:tenant,'pos.sell',:branch)"),
            {"tenant": context.stock.tenant_id, "branch": context.stock.branch_id},
        )
        is False
    )
    # Compare under the same reduced rights: stock rows are hidden without pos.sell.
    before = await facts(db_session)
    async with refund_actor(db_session):
        response = await client.get(status_path(result.parent_sale_id, result.operation_id))
        assert response.status_code == 200
        assert response.json()["state"] == "completed"
        assert response.json()["receipt"] == result.receipt.model_dump(mode="json")
    assert await facts(db_session) == before


async def test_other_authorized_cashier_cannot_recover_original_cashier_refund(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    result = await completed_refund(db_session)
    context = runtime(db_session)
    before = await facts(db_session)
    await db_session.commit()
    actor_id = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, context.stock, actor_id)
    async with refund_actor(db_session) as claimed:
        claimed.user_id = actor_id
        response = await client.get(status_path(result.parent_sale_id, result.operation_id))
        assert response.status_code == 200
        assert response.json() == {
            "state": "not_found",
            "operation_id": str(result.operation_id),
            "parent_sale_id": str(result.parent_sale_id),
        }
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await facts(db_session) == before


@pytest.mark.parametrize("same_tenant", [False, True])
async def test_actual_other_pharmacy_cannot_recover_refund(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    same_tenant: bool,
) -> None:
    result = await completed_refund(db_session)
    context = runtime(db_session)
    before = await facts(db_session)
    await db_session.commit()
    async with AsyncSession(db_engine) as support, support.begin():
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        if same_tenant:
            branch = await FoundationService(FoundationRepository(support)).create_branch(
                tenant_id=context.stock.tenant_id, fields={"name": "Other refund pharmacy"}
            )
            foreign = replace(context.stock, branch_id=branch.id)
        else:
            foreign = await seed_ledger_context(support)
    actor_id = await seed_receiving_actor(
        db_engine, maintenance_engine, foreign, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, foreign, actor_id)
    async with refund_actor(db_session) as claimed:
        claimed.user_id = actor_id
        claimed.tenant_id = foreign.tenant_id
        claimed.permission_scopes = {"pos.refund": frozenset({foreign.branch_id})}
        response = await client.get(status_path(result.parent_sale_id, result.operation_id))
        assert response.status_code == (403 if same_tenant else 404)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await facts(db_session) == before
