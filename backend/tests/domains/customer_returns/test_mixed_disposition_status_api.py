"""Recovery is a read-only, resolve-scoped HTTP operation under actual app RLS."""

from typing import Literal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.domains.customer_returns.mixed_disposition_repository import MixedDispositionRepository
from app.domains.customer_returns.mixed_disposition_service import MixedDispositionService
from tests.domains.customer_returns.test_mixed_disposition import request
from tests.domains.customer_returns.test_mixed_disposition_api import PATH, decision_actor
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.inventory.test_unit_ledger import LedgerContext, seed_ledger_context
from tests.domains.pos.test_mixed_zero_return import facts
from tests.domains.pos.test_physical_cash_refund_status import assert_no_cost_fields
from tests.domains.pos.test_unit_return import runtime
from tests.isolation.test_mixed_disposition_migration import confirmed

db_session = receiving_session_fixture


async def read(
    client: AsyncClient, session: AsyncSession, decision: UUID, branch: UUID
) -> Response:
    savepoint = await session.begin_nested()
    try:
        return await client.get(f"{PATH}/operations/{decision}", params={"branch_id": str(branch)})
    finally:
        await savepoint.rollback()


@pytest.mark.parametrize("kind", ["legacy_pack", "physical_unit"])
@pytest.mark.parametrize("intake_kind", ["cash_refund", "zero_return"])
async def test_saved_and_unknown_recovery_preserve_original_author_and_all_source_facts(
    client: AsyncClient,
    db_session: AsyncSession,
    kind: Literal["legacy_pack", "physical_unit"],
    intake_kind: Literal["cash_refund", "zero_return"],
) -> None:
    row = next(
        row
        for row in await confirmed(db_session, intake_kind=intake_kind)
        if row.inventory_kind == kind
    )
    context = runtime(db_session)
    chosen = request(row, decision_operation_id=uuid4())
    saved = await MixedDispositionService(MixedDispositionRepository(db_session)).resolve(
        tenant_id=context.stock.tenant_id, request=chosen
    )
    before = await facts(db_session)
    async with decision_actor(db_session):
        found = await read(client, db_session, chosen.decision_operation_id, row.branch_id)
        assert found.status_code == 200, found.text
        assert found.json() == saved.model_dump(mode="json")
        unknown = uuid4()
        absent = await read(client, db_session, unknown, row.branch_id)
        assert absent.status_code == 200 and absent.json() == {
            "state": "not_found",
            "decision_operation_id": str(unknown),
            "branch_id": str(row.branch_id),
        }
        assert found.headers["cache-control"] == "private, no-store"
        assert absent.headers["cache-control"] == "private, no-store"
        assert_no_cost_fields(found.json())
    assert await facts(db_session) == before


async def test_resolve_only_actor_can_recover_another_original_author_without_view_or_refund(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    row = (await confirmed(db_session))[0]
    context = runtime(db_session)
    chosen = request(row, decision_operation_id=uuid4())
    saved = await MixedDispositionService(MixedDispositionRepository(db_session)).resolve(
        tenant_id=context.stock.tenant_id, request=chosen
    )
    await db_session.commit()
    resolver = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("customer_returns.resolve",)
    )
    await set_receiving_context(db_session, context.stock, resolver)
    assert await db_session.scalar(text("SELECT session_user")) == "aurum_app"
    assert await db_session.scalar(text("SELECT count(*) FROM pos_mixed_return_command")) == 0
    async with decision_actor(db_session) as actor:
        actor.user_id = resolver
        actor.permissions = {"customer_returns.resolve"}
        response = await read(client, db_session, chosen.decision_operation_id, row.branch_id)
        assert response.status_code == 200 and response.json() == saved.model_dump(mode="json")
        absent = await read(client, db_session, uuid4(), row.branch_id)
        assert absent.status_code == 200 and absent.json()["state"] == "not_found"


@pytest.mark.parametrize("scope", ["revoked", "branch", "tenant", "inactive", "view_only"])
async def test_real_database_scope_denies_known_and_unknown_before_lookup(
    client: AsyncClient,
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    scope: str,
) -> None:
    row = (await confirmed(db_session))[0]
    context = runtime(db_session)
    chosen = request(row, decision_operation_id=uuid4())
    await MixedDispositionService(MixedDispositionRepository(db_session)).resolve(
        tenant_id=context.stock.tenant_id, request=chosen
    )
    await db_session.commit()
    other = context.stock
    if scope in {"branch", "tenant"}:
        async with AsyncSession(db_engine) as support:
            async with support.begin():
                await support.execute(text("SELECT set_config('app.support_session','true',true)"))
                if scope == "tenant":
                    other = await seed_ledger_context(support)
                else:
                    branch = await support.scalar(
                        text(
                            "INSERT INTO branch(tenant_id,name) "
                            "VALUES(:t,'Recovery scope') RETURNING id"
                        ),
                        {"t": context.stock.tenant_id},
                    )
                    assert isinstance(branch, UUID)
                    other = LedgerContext(context.stock.tenant_id, branch, context.stock.catalog_id)
    actor_id = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        other,
        permissions=(
            ("customer_returns.view",) if scope == "view_only" else ("customer_returns.resolve",)
        ),
    )
    if scope == "revoked":
        async with db_engine.begin() as support:
            await support.execute(text("SELECT set_config('app.support_session','true',true)"))
            await support.execute(
                text(
                    "UPDATE user_assignment SET is_active=false "
                    "WHERE tenant_id=:t AND user_id=:u"
                ),
                {"t": other.tenant_id, "u": actor_id},
            )
    elif scope == "inactive":
        async with maintenance_engine.begin() as owner:
            await owner.execute(
                text("UPDATE app_user SET status='blocked' WHERE id=:u"), {"u": actor_id}
            )
    await set_receiving_context(db_session, other, actor_id)
    async with decision_actor(db_session) as actor:
        actor.user_id = actor_id
        actor.tenant_id = other.tenant_id
        actor.permission_scopes["customer_returns.resolve"] = None
        # Broad HTTP claims cannot expand actual DB assignments or actor status.
        for operation_id in (chosen.decision_operation_id, uuid4()):
            denied = await read(client, db_session, operation_id, row.branch_id)
            assert denied.status_code == 403, denied.text


@pytest.mark.parametrize("status", ["readonly", "archived"])
async def test_recovery_remains_readable_without_writable_tenant_dependency(
    client: AsyncClient, db_session: AsyncSession, maintenance_engine: AsyncEngine, status: str
) -> None:
    row = (await confirmed(db_session))[0]
    context = runtime(db_session)
    chosen = request(row, decision_operation_id=uuid4())
    saved = await MixedDispositionService(MixedDispositionRepository(db_session)).resolve(
        tenant_id=context.stock.tenant_id, request=chosen
    )
    await db_session.commit()
    async with maintenance_engine.begin() as owner:
        await owner.execute(
            text("UPDATE tenant SET status=:s WHERE id=:t"),
            {"s": status, "t": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await facts(db_session)
    async with decision_actor(db_session):
        response = await read(client, db_session, chosen.decision_operation_id, row.branch_id)
        assert response.status_code == 200 and response.json() == saved.model_dump(mode="json")
    assert await facts(db_session) == before
    # The fresh disposable tenant stays readonly/archived; no unchecked activation.


async def test_http_requires_branch_and_enforces_its_resolve_scope(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    async with decision_actor(db_session) as actor:
        unknown = uuid4()
        missing = await client.get(f"{PATH}/operations/{unknown}")
        assert missing.status_code == 422
        wrong = await read(client, db_session, unknown, uuid4())
        assert wrong.status_code == 403
        actor.permissions = {"customer_returns.view"}
        denied = await read(client, db_session, unknown, runtime(db_session).stock.branch_id)
        assert denied.status_code == 403
