"""A real payment-insert failure rolls back and recovers the same public operation."""

from collections.abc import AsyncIterator
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.domains.pos.physical_cash_checkout_repository import PhysicalCashCheckoutRepository
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from app.main import app
from tests.domains.incoming.test_unit_acceptance import ReceivingRuntime, set_receiving_context
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.inventory.test_unit_ledger_v2_dispensing import PhysicalSource, remaining
from tests.domains.pos.test_physical_cart_api import cart_actor
from tests.domains.pos.test_physical_cash_checkout import facts
from tests.domains.pos.test_physical_cash_checkout_api import cash_path, quoted_request
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def fresh_facts(context: ReceivingRuntime) -> dict[str, list[object]]:
    async with context.factory() as session:
        session.info["receiving_runtime"] = context
        await set_receiving_context(session, context.stock, context.actor_id)
        result = await facts(session)
        result["register_receipt_counter"] = list(
            (
                await session.execute(
                    text(
                        "SELECT to_jsonb(r) FROM register_receipt_counter r "
                        "WHERE tenant_id=:tenant ORDER BY register_id"
                    ),
                    {"tenant": context.stock.tenant_id},
                )
            ).scalars()
        )
        return result


def install_payment_failure(monkeypatch: pytest.MonkeyPatch, source: PhysicalSource) -> list[bool]:
    original = PhysicalCashCheckoutRepository.insert_payment
    observed = [False]

    async def insert_then_fail(
        repository: PhysicalCashCheckoutRepository,
        *,
        tenant_id: UUID,
        sale_id: UUID,
        operation_id: UUID,
        request_hash: str,
        amount: Decimal,
    ) -> None:
        await original(
            repository,
            tenant_id=tenant_id,
            sale_id=sale_id,
            operation_id=operation_id,
            request_hash=request_hash,
            amount=amount,
        )
        assert (
            await repository.session.scalar(
                text("SELECT count(*) FROM sale_payment WHERE sale_id=:sale"), {"sale": sale_id}
            )
            == 1
        )
        assert (
            await repository.session.scalar(
                text("SELECT count(*) FROM pos_physical_checkout WHERE sale_id=:sale"),
                {"sale": sale_id},
            )
            == 1
        )
        assert await remaining(repository.session, source) == 25
        assert (
            await repository.session.scalar(
                text(
                    "SELECT last_receipt_seq FROM register_receipt_counter WHERE register_id="
                    "(SELECT register_id FROM sale WHERE id=:sale)"
                ),
                {"sale": sale_id},
            )
            == 1
        )
        observed[0] = True
        # Abort PostgreSQL only after the real payment, stock and manifest were flushed.
        await repository.session.execute(text("SELECT 1/0"))

    monkeypatch.setattr(PhysicalCashCheckoutRepository, "insert_payment", insert_then_fail)
    return observed


async def assert_committed_once(
    context: ReceivingRuntime,
    request: CashConfirmationRequestV2,
    result: PhysicalCheckoutResultV3,
    source: PhysicalSource,
) -> None:
    async with context.factory() as session:
        session.info["receiving_runtime"] = context
        await set_receiving_context(session, context.stock, context.actor_id)
        payments = (
            await session.execute(
                text("SELECT id,payment_method,amount FROM sale_payment WHERE sale_id=:sale"),
                {"sale": request.sale_id},
            )
        ).all()
        expected_payment = (request.operation_id, "cash", Decimal("10.00"))
        assert [tuple(row) for row in payments] == [expected_payment]
        assert (
            await session.scalar(
                text("SELECT count(*) FROM pos_physical_checkout WHERE sale_id=:sale"),
                {"sale": request.sale_id},
            )
            == 1
        )
        movement = (
            await session.execute(
                text(
                    "SELECT sum(units_taken),sum(cost_amount) FROM unit_stock_movement_v2 "
                    "WHERE command_id=:command"
                ),
                {"command": request.operation_id},
            )
        ).one()
        assert tuple(movement) == (5, Decimal("1.66"))
        assert await remaining(session, source) == 25
        assert result.receipt_sequence == 1 and result.receipt_number == "000001"
        assert (
            await session.scalar(
                text("SELECT last_receipt_seq FROM register_receipt_counter WHERE register_id=:id"),
                {"id": result.register_id},
            )
            == 1
        )
        receipt = await session.scalar(
            text("SELECT receipt_snapshot FROM sale WHERE id=:sale"), {"sale": request.sale_id}
        )
        assert receipt["schema_version"] == 3
        assert receipt["receipt_number"] == result.receipt_number


async def test_failed_payment_insert_recovers_same_uuid_without_receipt_gap(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = await checkout_source(db_session, quantity=1)
    sale_id = source.sale.id
    context = runtime(db_session)
    await db_session.commit()

    async def request_database() -> AsyncIterator[AsyncSession]:
        async with context.factory() as session:
            session.info["receiving_runtime"] = context
            await set_receiving_context(session, context.stock, context.actor_id)
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    async with cart_actor(db_session):
        app.dependency_overrides[get_db] = request_database
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test"
        ) as client:
            response = await client.put(
                f"/api/v1/sales/{sale_id}/physical-cart/items/{uuid4()}",
                json={
                    "catalog_id": str(context.stock.catalog_id),
                    "position": 1,
                    "sale_format": "unit",
                    "quantity": 5,
                    "discount_amount": "0.00",
                },
            )
            assert response.status_code == 200
            request = await quoted_request(client, sale_id)
            body = request.model_dump(mode="json")
            path = cash_path(sale_id)
            intent = await client.post(f"{path}/intent", json=body)
            assert intent.status_code == 200
            before = await fresh_facts(context)
            assert before["register_receipt_counter"] == []
            with monkeypatch.context() as patch:
                observed = install_payment_failure(patch, source.stock)
                response = await client.post(f"{path}/complete", json=body)
                assert response.status_code == 500
                assert observed == [True]
            assert await fresh_facts(context) == before
            status_path = f"{path}/operations/{request.operation_id}"
            saved = await client.get(status_path)
            assert saved.status_code == 200 and saved.json()["state"] == "saved_draft"
            assert saved.json()["saved_intent"] == intent.json()
            assert await fresh_facts(context) == before
            completed = await client.post(f"{path}/complete", json=body)
            assert completed.status_code == 200
            result = PhysicalCheckoutResultV3.model_validate(completed.json())
            assert result.operation_id == request.operation_id
            assert result.sale_id == sale_id
            after = await fresh_facts(context)
            status = await client.get(status_path)
            assert status.status_code == 200 and status.json()["state"] == "completed"
            assert status.json()["result"] == completed.json()
            replay = await client.post(f"{path}/complete", json=body)
            assert replay.status_code == 200 and replay.json() == completed.json()
            assert await fresh_facts(context) == after
            await assert_committed_once(context, request, result, source.stock)
