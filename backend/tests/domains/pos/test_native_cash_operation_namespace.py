"""One native cash operation owns its linked sale and payment, never another claim."""

from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.models import POSCommand
from app.domains.pos.unit_cash_intent_service import cash_intent_request_hash
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.pos.test_mixed_cash_checkout import complete as complete_mixed
from tests.domains.pos.test_mixed_physical_checkout_preparation import legacy_with_stock
from tests.domains.pos.test_physical_cash_checkout import complete as complete_physical
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def _reject_unmarked_payment(session: AsyncSession, sale_id: UUID) -> None:
    context = runtime(session)
    operation = uuid4()
    with pytest.raises(DBAPIError) as caught:
        async with session.begin_nested():
            await session.execute(
                text(
                    "UPDATE public.sale SET operation_id=:operation,operation_hash=repeat('0',64) "
                    "WHERE id=:sale AND status='draft'"
                ),
                {"operation": operation, "sale": sale_id},
            )
            await session.execute(
                text(
                    "INSERT INTO public.sale_payment (tenant_id,sale_id,payment_method,amount,"
                    "operation_id,operation_hash,currency) "
                    "VALUES (:tenant,:sale,'cash',1,:operation,repeat('0',64),'TJS')"
                ),
                {"tenant": context.stock.tenant_id, "sale": sale_id, "operation": operation},
            )
    assert getattr(caught.value.orig, "sqlstate", None) == "23505"
    assert getattr(caught.value.orig.__cause__, "constraint_name", None) == (
        "uq_pos_operation_namespace"
    )


async def _reject_command_claim(session: AsyncSession, sale_id: UUID, operation: UUID) -> None:
    context = runtime(session)
    with pytest.raises(DBAPIError) as caught:
        async with session.begin_nested():
            session.add(
                POSCommand(
                    tenant_id=context.stock.tenant_id,
                    operation_id=operation,
                    actor_user_id=context.actor_id,
                    sale_id=sale_id,
                    command_type="sale.create",
                    request_hash="0" * 64,
                    result_payload={},
                    created_by=context.actor_id,
                )
            )
            await session.flush()
    assert getattr(caught.value.orig, "sqlstate", None) == "23505"
    cause = caught.value.orig.__cause__
    assert getattr(cause, "constraint_name", None) == "uq_pos_operation_namespace"


@pytest.mark.parametrize("mixed", [False, True], ids=["physical", "mixed"])
async def test_native_cash_sale_and_payment_share_only_their_exact_operation(
    db_session: AsyncSession, mixed: bool
) -> None:
    source = await checkout_source(db_session)
    await _reject_unmarked_payment(db_session, source.sale.id)
    if mixed:
        await legacy_with_stock(db_session, source.sale)
    await put_physical(db_session, source.sale.id)
    request = await saved_request(db_session, source.sale)
    source.sale.operation_id = request.operation_id
    source.sale.operation_hash = cash_intent_request_hash(request)
    await db_session.flush()
    await _reject_command_claim(db_session, source.sale.id, request.operation_id)
    complete = complete_mixed if mixed else complete_physical
    result = await complete(db_session, request)
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    pair = (
        await db_session.execute(
            text(
                "SELECT sale.operation_id,sale.operation_hash,payment.id,payment.operation_id,"
                "payment.operation_hash,payment.amount,payment.currency FROM public.sale sale "
                "JOIN public.sale_payment payment ON payment.sale_id=sale.id "
                "WHERE sale.id=:sale AND sale.tenant_id=:tenant"
            ),
            {"sale": result.sale_id, "tenant": result.tenant_id},
        )
    ).one()
    assert tuple(pair) == (
        request.operation_id,
        cash_intent_request_hash(request),
        request.operation_id,
        request.operation_id,
        cash_intent_request_hash(request),
        result.total_amount,
        "TJS",
    )
    assert source.sale.status == "completed"
    await _reject_command_claim(db_session, source.sale.id, request.operation_id)
