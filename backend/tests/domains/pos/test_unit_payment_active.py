"""Discover active preparation without revalidating prices or writing any facts."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import AurumError, NotFoundError, PermissionDeniedError
from app.domains.pos.models import Sale
from app.domains.pos.unit_payment_active_repository import (
    ActivePaymentCandidate,
    UnitPaymentActiveRepository,
)
from app.domains.pos.unit_payment_active_schemas import UnitPaymentActiveRead
from app.domains.pos.unit_payment_active_service import UnitPaymentActiveService
from app.domains.pos.unit_payment_intent_schemas import SaveUnitPaymentIntentRequest
from tests.domains.customer_returns.test_physical_queue import cash_source
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.pos.test_physical_cart_api import cart_actor
from tests.domains.pos.test_physical_cash_refund import close_shift
from tests.domains.pos.test_unit_payment_binding import binding
from tests.domains.pos.test_unit_payment_intent import intent_request, save
from tests.domains.pos.test_unit_payment_plan import snapshot
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def active(session: AsyncSession, sale_id: UUID) -> UnitPaymentActiveRead:
    context = runtime(session)
    return await UnitPaymentActiveService(UnitPaymentActiveRepository(session)).read(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        sale_id=sale_id,
    )


@pytest.mark.parametrize("mixed", [False, True])
async def test_none_saved_bound_cancelled_and_replacement_are_single_coherent_states(
    db_session: AsyncSession, mixed: bool
) -> None:
    request = await intent_request(db_session, mixed=mixed)
    sale_id = request.request.sale_id
    empty = await active(db_session, sale_id)
    assert empty.intent is None and empty.binding is None
    saved = await save(db_session, request)
    assert await active(db_session, sale_id) == empty
    second = SaveUnitPaymentIntentRequest(operation_id=uuid4(), request=request.request)
    await save(db_session, second)
    bound = await binding(db_session, request, "bind")
    before = await snapshot(db_session)
    result = await active(db_session, sale_id)
    assert result.intent == saved and result.binding == bound
    assert await active(db_session, sale_id) == result
    assert await snapshot(db_session) == before
    await binding(db_session, request, "cancel")
    assert await active(db_session, sale_id) == empty
    replacement = await binding(db_session, second, "bind")
    result = await active(db_session, sale_id)
    assert result.binding == replacement and result.intent is not None
    assert result.intent.operation_id == second.operation_id


async def test_active_read_does_not_flush_or_overwrite_pending_sale_edits(
    db_session: AsyncSession,
) -> None:
    request = await intent_request(db_session)
    await save(db_session, request)
    await binding(db_session, request, "bind")
    expected = await active(db_session, request.request.sale_id)
    sale = await db_session.get(Sale, request.request.sale_id)
    assert sale is not None
    sale.total_amount = Decimal("999")
    assert await active(db_session, sale.id) == expected
    assert sale in db_session.dirty and sale.total_amount == Decimal("999")
    with db_session.no_autoflush:
        stored = await db_session.scalar(
            text("SELECT total_amount FROM sale WHERE id=:id"), {"id": sale.id}
        )
    assert stored == Decimal("0")


async def test_active_read_enforces_real_tenant_cashier_and_branch(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    context = runtime(db_session)
    other = await seed_receiving_actor(db_engine, maintenance_engine, context.stock)
    request = await intent_request(db_session)
    await save(db_session, request)
    await binding(db_session, request, "bind")
    service = UnitPaymentActiveService(UnitPaymentActiveRepository(db_session))
    with pytest.raises(PermissionDeniedError):
        await service.read(
            tenant_id=context.stock.tenant_id,
            actor_id=context.actor_id,
            allowed_branch_ids={uuid4()},
            sale_id=request.request.sale_id,
        )
    with pytest.raises(PermissionDeniedError):
        await service.read(
            tenant_id=uuid4(),
            actor_id=context.actor_id,
            allowed_branch_ids=None,
            sale_id=request.request.sale_id,
        )
    with pytest.raises(NotFoundError):
        await active(db_session, uuid4())
    await set_receiving_context(db_session, context.stock, other)
    with pytest.raises(PermissionDeniedError):
        await service.read(
            tenant_id=context.stock.tenant_id,
            actor_id=other,
            allowed_branch_ids=None,
            sale_id=request.request.sale_id,
        )


async def test_http_discovery_is_uncached_and_available_in_readonly(
    db_session: AsyncSession, client: AsyncClient, db_engine: AsyncEngine
) -> None:
    request = await intent_request(db_session)
    await save(db_session, request)
    await binding(db_session, request, "bind")
    expected = await active(db_session, request.request.sale_id)
    assert expected.intent is not None
    context = runtime(db_session)
    await db_session.commit()
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:id"),
            {"id": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await snapshot(db_session)
    path = f"/api/v1/sales/{request.request.sale_id}/unit-payment-plan/active-binding"
    async with cart_actor(db_session) as actor:
        response = await client.get(path)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        assert UnitPaymentActiveRead.model_validate(response.json()) == expected
        actor.permission_scopes["pos.sell"] = frozenset({uuid4()})
        assert (await client.get(path)).status_code == 403
        actor.permissions.clear()
        actor.permission_scopes.clear()
        assert (await client.get(path)).status_code == 403
    assert await snapshot(db_session) == before


async def test_completed_sale_in_closed_shift_remains_readable(db_session: AsyncSession) -> None:
    original, _ = await cash_source(db_session)
    await close_shift(db_session, original.shift_id)
    before = await snapshot(db_session)
    result = await active(db_session, original.sale_id)
    assert result.sale_id == original.sale_id and result.intent is None and result.binding is None
    assert await snapshot(db_session) == before


async def test_multiple_active_snapshot_fails_closed(db_session: AsyncSession) -> None:
    request = await intent_request(db_session)
    await save(db_session, request)
    await binding(db_session, request, "bind")

    class InconsistentRepository(UnitPaymentActiveRepository):
        async def snapshot(self, tenant_id: UUID, sale_id: UUID) -> list[ActivePaymentCandidate]:
            return (await super().snapshot(tenant_id, sale_id)) * 2

    context = runtime(db_session)
    with pytest.raises(AurumError, match="inconsistent active"):
        await UnitPaymentActiveService(InconsistentRepository(db_session)).read(
            tenant_id=context.stock.tenant_id,
            actor_id=context.actor_id,
            allowed_branch_ids=None,
            sale_id=request.request.sale_id,
        )
