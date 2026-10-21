"""Mixed electronic journals retain every allocation and its exact parent/attempt."""

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AurumError
from app.domains.pos.mixed_documents import MixedCheckoutResultV4
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.unit_checkout_payment_models import UnitCheckoutPaymentComponent
from app.domains.pos.unit_checkout_payment_repository import UnitCheckoutPaymentRepository
from app.domains.pos.unit_checkout_payment_service import Checkout, UnitCheckoutPaymentService
from tests.domains.pos.test_mixed_electronic_preparation_contract import quote


def checkout(*, electronic: bool = True) -> MixedCheckoutResultV4:
    saved = quote()
    return MixedCheckoutResultV4.model_validate(
        {
            "schema_version": 4,
            "document_kind": "mixed_checkout",
            **saved.model_dump(exclude={"schema_version", "state", "lines"}),
            "operation_id": uuid4(),
            "stock_command_id": uuid4(),
            "event_id": uuid4(),
            "created_at": datetime(2026, 10, 20, 10, tzinfo=UTC),
            "completed_at": datetime(2026, 10, 20, 11, tzinfo=UTC),
            "receipt_number": "ISOLATED-1",
            "receipt_sequence": 1,
            "is_test": False,
            "lines": [{**line.model_dump(), "name": "Historical snapshot"} for line in saved.lines],
            "payments": [
                {"id": uuid4(), "method": method, "amount": amount}
                for method, amount in (
                    (("cash", "3.52"), ("card", "10.00"), ("qr", "10.00"))
                    if electronic
                    else (("cash", "23.52"),)
                )
            ],
        }
    )


def physical_checkout() -> PhysicalCheckoutResultV3:
    mixed = checkout()
    line = mixed.lines[1].model_copy(update={"position": 1})
    return PhysicalCheckoutResultV3.model_validate(
        {
            **mixed.model_dump(),
            "schema_version": 3,
            "document_kind": "physical_checkout",
            "lines": [line],
            "total_amount": line.net_amount,
            "payments": [
                {"id": uuid4(), "method": method, "amount": amount}
                for method, amount in (("cash", "3.50"), ("card", "10.00"), ("qr", "10.00"))
            ],
        }
    )


def components(result: Checkout) -> tuple[UnitCheckoutPaymentComponent, ...]:
    mixed = isinstance(result, MixedCheckoutResultV4)
    return tuple(
        UnitCheckoutPaymentComponent(
            tenant_id=result.tenant_id,
            id=payment.id,
            physical_checkout_id=None if mixed else result.operation_id,
            mixed_checkout_id=result.operation_id if mixed else None,
            sale_id=result.sale_id,
            method=payment.method,
            amount=payment.amount,
            currency="TJS",
            payment_attempt_id=None if payment.method == "cash" else payment.id,
            created_by=result.cashier_id,
        )
        for payment in result.payments
    )


@pytest.mark.parametrize("mixed", [False, True])
async def test_journal_records_cash_card_qr_with_exact_attempt_ids(
    monkeypatch: pytest.MonkeyPatch,
    mixed: bool,
) -> None:
    result = checkout() if mixed else physical_checkout()
    insert = AsyncMock()
    async with AsyncSession() as session:
        repository = UnitCheckoutPaymentRepository(session)
        monkeypatch.setattr(repository, "insert", insert)
        await UnitCheckoutPaymentService(repository).record_electronic(result)
    assert insert.await_count == 3
    rows = tuple(call.args[0] for call in insert.await_args_list)
    assert {row.method for row in rows} == {"cash", "card", "qr"}
    assert sum((row.amount for row in rows), Decimal("0.00")) == result.total_amount
    for row, payment in zip(rows, result.payments, strict=True):
        assert row.id == payment.id and row.amount == payment.amount
        assert row.tenant_id == result.tenant_id and row.sale_id == result.sale_id
        assert row.physical_checkout_id == (None if mixed else result.operation_id)
        assert row.mixed_checkout_id == (result.operation_id if mixed else None)
        assert row.payment_attempt_id == (None if payment.method == "cash" else payment.id)
        assert row.created_by == result.cashier_id and row.currency == "TJS"


@pytest.mark.parametrize("mixed", [False, True])
async def test_saved_journal_v3_reads_every_component_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    mixed: bool,
) -> None:
    result = checkout() if mixed else physical_checkout()
    read = AsyncMock(return_value=components(result))
    insert = AsyncMock(side_effect=AssertionError("Recovery must not write"))
    async with AsyncSession() as session:
        repository = UnitCheckoutPaymentRepository(session)
        monkeypatch.setattr(repository, "for_checkout", read)
        monkeypatch.setattr(repository, "insert", insert)
        await UnitCheckoutPaymentService(repository).validate_saved(3, result)
    read.assert_awaited_once_with(result.tenant_id, result.operation_id, mixed=mixed)
    insert.assert_not_awaited()


@pytest.mark.parametrize(
    "field,value",
    [
        ("tenant_id", uuid4()),
        ("sale_id", uuid4()),
        ("id", uuid4()),
        ("physical_checkout_id", uuid4()),
        ("mixed_checkout_id", uuid4()),
        ("method", "cash"),
        ("amount", Decimal("9.99")),
        ("currency", "USD"),
        ("payment_attempt_id", None),
        ("payment_attempt_id", uuid4()),
        ("created_by", uuid4()),
    ],
)
async def test_mixed_saved_journal_rejects_foreign_or_changed_electronic_component(
    monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    result = checkout()
    rows = components(result)
    setattr(rows[1], field, value)
    async with AsyncSession() as session:
        repository = UnitCheckoutPaymentRepository(session)
        monkeypatch.setattr(repository, "for_checkout", AsyncMock(return_value=rows))
        with pytest.raises(AurumError, match="journal is inconsistent"):
            await UnitCheckoutPaymentService(repository).validate_saved(3, result)


@pytest.mark.parametrize("version", [1, 2])
async def test_historical_mixed_cash_journal_remains_read_only(
    monkeypatch: pytest.MonkeyPatch, version: int
) -> None:
    result = checkout(electronic=False)
    read = AsyncMock(return_value=components(result))
    async with AsyncSession() as session:
        repository = UnitCheckoutPaymentRepository(session)
        monkeypatch.setattr(repository, "for_checkout", read)
        monkeypatch.setattr(
            repository, "insert", AsyncMock(side_effect=AssertionError("No backfill"))
        )
        await UnitCheckoutPaymentService(repository).validate_saved(version, result)
    if version == 1:
        read.assert_not_awaited()
    else:
        read.assert_awaited_once_with(result.tenant_id, result.operation_id, mixed=True)


async def test_mixed_electronic_journal_rejects_missing_component(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = checkout()
    async with AsyncSession() as session:
        repository = UnitCheckoutPaymentRepository(session)
        monkeypatch.setattr(
            repository, "for_checkout", AsyncMock(return_value=components(result)[:-1])
        )
        with pytest.raises(AurumError, match="journal is inconsistent"):
            await UnitCheckoutPaymentService(repository).validate_saved(3, result)


async def test_cash_only_mixed_result_cannot_be_recorded_as_electronic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with AsyncSession() as session:
        repository = UnitCheckoutPaymentRepository(session)
        insert = AsyncMock()
        monkeypatch.setattr(repository, "insert", insert)
        with pytest.raises(AurumError, match="requires an electronic"):
            await UnitCheckoutPaymentService(repository).record_electronic(
                checkout(electronic=False)
            )
        insert.assert_not_awaited()
