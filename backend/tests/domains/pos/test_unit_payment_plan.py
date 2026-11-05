"""Read-only electronic payment planning against real scoped draft quotes."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.deps import CurrentUser, current_user
from app.core.errors import BusinessRuleError, ConflictError, PermissionDeniedError
from app.domains.pos.models import Sale
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_payment_plan import (
    UnitPaymentPlanRequest,
    UnitPaymentPlanService,
    ValidatedUnitPaymentPlan,
)
from app.main import app
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.pos.test_payment_configuration import _set_payment_settings
from tests.domains.pos.test_physical_cart_api import cart_actor
from tests.domains.pos.test_unit_cart import put_cart_item, seed_cart_draft
from tests.domains.pos.test_unit_cart_quote import add_legacy, quote
from tests.domains.pos.test_unit_physical_cart import physical_draft, put_physical
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def plan_request(session: AsyncSession, *, mixed: bool = False) -> UnitPaymentPlanRequest:
    sale = await physical_draft(session)
    if mixed:
        await add_legacy(session, sale)
    await put_physical(session, sale.id, quantity=2, discount=Decimal("0.01"))
    current = await quote(session, sale.id)
    await session.flush()
    return UnitPaymentPlanRequest.model_validate(
        {
            "sale_id": sale.id,
            "expected_revision_hash": current.revision_hash,
            "expected_total_amount": current.total_amount,
            "payments": [{"method": "card", "amount": current.total_amount}],
        }
    )


async def validate(
    session: AsyncSession, request: UnitPaymentPlanRequest
) -> ValidatedUnitPaymentPlan:
    context = runtime(session)
    return await UnitPaymentPlanService(UnitCartRepository(session)).validate(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        request=request,
    )


async def snapshot(session: AsyncSession) -> dict[str, list[object]]:
    tenant = runtime(session).stock.tenant_id
    tables = (
        "sale",
        "sale_item",
        "sale_payment",
        "pos_payment_attempt",
        "unit_stock_lot",
        "unit_stock_lot_v2",
        "unit_stock_entry_v2",
        "unit_stock_entry",
        "unit_stock_movement_v2",
        "unit_stock_cart_command_v2",
    )
    result: dict[str, list[object]] = {}
    for table in tables:
        rows = await session.execute(
            text(f"SELECT to_jsonb(row) FROM {table} row WHERE tenant_id=:tenant ORDER BY id"),
            {"tenant": tenant},
        )
        result[table] = list(rows.scalars())
    return result


@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("method", ["card", "qr"])
async def test_preview_uses_live_quote_without_any_financial_or_stock_write(
    db_session: AsyncSession, mixed: bool, method: str
) -> None:
    request = await plan_request(db_session, mixed=mixed)
    request = UnitPaymentPlanRequest.model_validate(
        {
            **request.model_dump(),
            "payments": [{"method": method, "amount": request.expected_total_amount}],
        }
    )
    before = await snapshot(db_session)
    result = await validate(db_session, request)
    assert result.state == "validated_draft"
    assert result.quote.total_amount == Decimal("2.01" if mixed else "1.99")
    assert result.change_amount == Decimal("0")
    assert await validate(db_session, request) == result
    assert len(result.plan_hash) == 64
    assert "purchase" not in result.model_dump_json() and "cost" not in result.model_dump_json()
    assert await snapshot(db_session) == before


async def test_split_cash_change_and_current_payment_settings(db_session: AsyncSession) -> None:
    request = await plan_request(db_session)
    request = UnitPaymentPlanRequest.model_validate(
        {
            **request.model_dump(),
            "payments": [
                {"method": "cash", "amount": "0.99"},
                {"method": "card", "amount": "0.50"},
                {"method": "qr", "amount": "0.50"},
            ],
            "cash_tendered": "5.00",
        }
    )
    result = await validate(db_session, request)
    assert result.change_amount == Decimal("4.01")
    reordered = UnitPaymentPlanRequest.model_validate(
        {**request.model_dump(), "payments": tuple(reversed(request.payments))}
    )
    assert (await validate(db_session, reordered)).plan_hash == result.plan_hash
    more_cash = request.model_copy(update={"cash_tendered": Decimal("6.00")})
    assert (await validate(db_session, more_cash)).plan_hash != result.plan_hash
    context = runtime(db_session)
    await _set_payment_settings(
        db_session,
        tenant_id=context.stock.tenant_id,
        methods=["cash", "card", "qr"],
        mixed=False,
    )
    with pytest.raises(BusinessRuleError):
        await validate(db_session, request)
    await _set_payment_settings(
        db_session, tenant_id=context.stock.tenant_id, methods=["cash", "card"], mixed=True
    )
    with pytest.raises(BusinessRuleError):
        await validate(db_session, request)


async def test_preview_rejects_changed_quote(db_session: AsyncSession) -> None:
    request = await plan_request(db_session)
    await put_physical(db_session, request.sale_id, position=2)
    with pytest.raises(ConflictError):
        await validate(db_session, request)


async def test_service_revalidates_constructed_request(db_session: AsyncSession) -> None:
    request = await plan_request(db_session)
    tampered = UnitPaymentPlanRequest.model_construct(
        **{**request.model_dump(), "expected_total_amount": Decimal("200.00")}
    )
    with pytest.raises(ValidationError):
        await validate(db_session, tampered)
    copied = request.model_copy(update={"cash_tendered": Decimal("1.00")})
    with pytest.raises(ValidationError):
        await validate(db_session, copied)


@pytest.mark.parametrize("prepared", [False, True])
async def test_preview_requires_physical_items(db_session: AsyncSession, prepared: bool) -> None:
    sale = await seed_cart_draft(db_session)
    if prepared:
        await put_cart_item(db_session, sale.id)
    else:
        await add_legacy(db_session, sale)
    current = await quote(db_session, sale.id)
    request = UnitPaymentPlanRequest.model_validate(
        {
            "sale_id": sale.id,
            "expected_revision_hash": current.revision_hash,
            "expected_total_amount": current.total_amount,
            "payments": [{"method": "card", "amount": current.total_amount}],
        }
    )
    with pytest.raises(BusinessRuleError):
        await validate(db_session, request)


async def test_preview_rejects_other_cashier_and_branch(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    context = runtime(db_session)
    other = await seed_receiving_actor(db_engine, maintenance_engine, context.stock)
    request = await plan_request(db_session)
    service = UnitPaymentPlanService(UnitCartRepository(db_session))
    with pytest.raises(PermissionDeniedError):
        await service.validate(
            tenant_id=context.stock.tenant_id,
            actor_id=context.actor_id,
            allowed_branch_ids={uuid4()},
            request=request,
        )
    await set_receiving_context(db_session, context.stock, other)
    with pytest.raises(PermissionDeniedError):
        await service.validate(
            tenant_id=context.stock.tenant_id,
            actor_id=other,
            allowed_branch_ids={context.stock.branch_id},
            request=request,
        )


async def test_preview_api_contract_permissions_and_path_binding(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    request = await plan_request(db_session)
    path = f"/api/v1/sales/{request.sale_id}/unit-payment-plan/preview"
    before = await snapshot(db_session)
    async with cart_actor(db_session) as actor:
        response = await client.post(path, json=request.model_dump(mode="json"))
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        assert response.json()["quote"]["total_amount"] == "1.99"
        assert response.json()["state"] == "validated_draft"
        mismatch = {**request.model_dump(mode="json"), "sale_id": str(uuid4())}
        assert (await client.post(path, json=mismatch)).status_code == 422
        forbidden = replace(actor, permissions=set(), permission_scopes={})

        async def override_user() -> CurrentUser:
            return forbidden

        app.dependency_overrides[current_user] = override_user
        assert (await client.post(path, json=request.model_dump(mode="json"))).status_code == 403
    assert await snapshot(db_session) == before


async def test_preview_rejects_mismatched_tenant_context(db_session: AsyncSession) -> None:
    request = await plan_request(db_session)
    context = runtime(db_session)
    with pytest.raises(PermissionDeniedError):
        await UnitPaymentPlanService(UnitCartRepository(db_session)).validate(
            tenant_id=uuid4(),
            actor_id=context.actor_id,
            allowed_branch_ids={context.stock.branch_id},
            request=request,
        )


async def test_preview_does_not_autoflush_dirty_sale(db_session: AsyncSession) -> None:
    request = await plan_request(db_session)
    sale = await db_session.get(Sale, request.sale_id)
    assert sale is not None
    sale.total_amount = Decimal("123.00")
    with pytest.raises(ConflictError, match="Persist pending draft changes"):
        await validate(db_session, request)
    assert sale in db_session.dirty
    with db_session.no_autoflush:
        saved_total = await db_session.scalar(
            text("SELECT total_amount FROM sale WHERE id=:sale"), {"sale": request.sale_id}
        )
    assert saved_total == Decimal("0.00")
