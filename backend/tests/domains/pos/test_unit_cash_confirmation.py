"""Cash confirmation checks current mixed totals but never accepts a payment."""

from decimal import Decimal, localcontext
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, ConflictError, PermissionDeniedError
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_cash_confirmation import (
    CashConfirmationRequestV2,
    UnitCashConfirmationService,
    ValidatedCashConfirmationV2,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.pos.test_unit_cart import put_cart_item, seed_cart_draft
from tests.domains.pos.test_unit_cart_quote import add_legacy, quote
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def confirmation_request(
    session: AsyncSession, *, cash_tendered: Decimal = Decimal("2.00")
) -> CashConfirmationRequestV2:
    sale = await seed_cart_draft(session)
    await add_legacy(session, sale)
    await put_cart_item(session, sale.id)
    current = await quote(session, sale.id)
    return CashConfirmationRequestV2(
        operation_id=uuid4(),
        sale_id=sale.id,
        expected_revision_hash=current.revision_hash,
        expected_total_amount=current.total_amount,
        cash_tendered=cash_tendered,
    )


async def validate(
    session: AsyncSession, request: CashConfirmationRequestV2
) -> ValidatedCashConfirmationV2:
    context = runtime(session)
    return await UnitCashConfirmationService(UnitCartRepository(session)).validate(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        request=request,
    )


@pytest.mark.parametrize("cash,change", [("1.02", "0.00"), ("2.00", "0.98")])
async def test_confirmation_preserves_sale_and_stock(
    db_session: AsyncSession, cash: str, change: str
) -> None:
    request = await confirmation_request(db_session, cash_tendered=Decimal(cash))
    context = runtime(db_session)
    counts = {}
    tables = ("unit_stock_entry", "pos_unit_sale_line", "sale_payment", "pos_payment_attempt")
    for table in tables:
        counts[table] = await db_session.scalar(
            text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
            {"tenant": context.stock.tenant_id},
        )
    result = await validate(db_session, request)
    assert result.state == "validated_draft"
    assert result.change_amount == Decimal(change)
    assert result.quote.total_amount == Decimal("1.02")
    assert ValidatedCashConfirmationV2.model_validate_json(result.model_dump_json()) == result
    assert await validate(db_session, request) == result
    for table in tables:
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": context.stock.tenant_id},
            )
            == counts[table]
        )
    assert (
        await db_session.execute(
            text("SELECT status,total_amount,receipt_number FROM sale WHERE id=:id"),
            {"id": request.sale_id},
        )
    ).one() == ("draft", Decimal("0.02"), None)


@pytest.mark.parametrize("mutation", ["price", "quantity", "legacy"])
async def test_changed_cart_requires_new_confirmation(
    db_session: AsyncSession, mutation: str
) -> None:
    request = await confirmation_request(db_session)
    context = runtime(db_session)
    if mutation == "price":
        await db_session.execute(
            text(
                "UPDATE tenant_catalog SET unit_sale_preparation="
                "jsonb_set(unit_sale_preparation,'{unit_price}','\"2.00\"') WHERE id=:id"
            ),
            {"id": context.stock.catalog_id},
        )
    elif mutation == "quantity":
        await db_session.execute(
            text("UPDATE pos_unit_cart_item SET quantity=2 WHERE sale_id=:sale"),
            {"sale": request.sale_id},
        )
    else:
        await db_session.execute(
            text("UPDATE sale_item SET qty=0.002,total_price=0.05 WHERE sale_id=:sale"),
            {"sale": request.sale_id},
        )
    with pytest.raises(ConflictError, match="Cart changed"):
        await validate(db_session, request)


async def test_wrong_expected_amount_and_short_cash_are_rejected(
    db_session: AsyncSession,
) -> None:
    request = await confirmation_request(db_session)
    wrong_total = CashConfirmationRequestV2.model_validate(
        {**request.model_dump(), "expected_total_amount": "1.03"}
    )
    with pytest.raises(ConflictError, match="Confirmed amount"):
        await validate(db_session, wrong_total)
    short = CashConfirmationRequestV2.model_validate(
        {**request.model_dump(), "cash_tendered": "1.01"}
    )
    with pytest.raises(BusinessRuleError, match="Cash tendered"):
        await validate(db_session, short)


async def test_confirmation_still_requires_cashier_and_branch_access(
    db_session: AsyncSession,
) -> None:
    request = await confirmation_request(db_session)
    context = runtime(db_session)
    service = UnitCashConfirmationService(UnitCartRepository(db_session))
    with pytest.raises(PermissionDeniedError):
        await service.validate(
            tenant_id=context.stock.tenant_id,
            actor_id=uuid4(),
            allowed_branch_ids={context.stock.branch_id},
            request=request,
        )
    with pytest.raises(PermissionDeniedError):
        await service.validate(
            tenant_id=context.stock.tenant_id,
            actor_id=context.actor_id,
            allowed_branch_ids={uuid4()},
            request=request,
        )


async def test_low_decimal_precision_does_not_change_cash_change(
    db_session: AsyncSession,
) -> None:
    request = await confirmation_request(db_session, cash_tendered=Decimal("1000.00"))
    with localcontext() as context:
        context.prec = 2
        result = await validate(db_session, request)
    assert result.change_amount == Decimal("998.98")


@pytest.mark.parametrize("amount", [True, 1.02, "NaN", "Infinity", "-1", "0", "1.001"])
async def test_invalid_cash_is_rejected_even_for_unvalidated_model(
    db_session: AsyncSession, amount: object
) -> None:
    request = await confirmation_request(db_session)
    forged = request.model_copy(update={"cash_tendered": amount})
    with pytest.raises(ValidationError):
        await validate(db_session, forged)


async def test_validation_snapshot_cannot_have_forged_change(db_session: AsyncSession) -> None:
    request = await confirmation_request(db_session)
    result = await validate(db_session, request)
    with pytest.raises(ValidationError, match="must balance"):
        ValidatedCashConfirmationV2.model_validate({**result.model_dump(), "change_amount": "1.00"})
