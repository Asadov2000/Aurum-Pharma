"""Checkout payment journals are exact, immutable and transactionally complete."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AurumError
from app.domains.pos.mixed_cash_checkout_models import MixedCashCheckout
from app.domains.pos.mixed_cash_checkout_repository import MixedCashCheckoutRepository
from app.domains.pos.mixed_cash_checkout_status_repository import MixedCashCheckoutStatusRepository
from app.domains.pos.mixed_cash_checkout_status_service import MixedCashCheckoutStatusService
from app.domains.pos.mixed_documents import MixedCheckoutResultV4
from app.domains.pos.physical_cash_checkout_models import PhysicalCashCheckout
from app.domains.pos.physical_cash_checkout_repository import PhysicalCashCheckoutRepository
from app.domains.pos.physical_cash_checkout_status_repository import (
    PhysicalCashCheckoutStatusRepository,
)
from app.domains.pos.physical_cash_checkout_status_service import PhysicalCashCheckoutStatusService
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from app.domains.pos.unit_checkout_payment_models import UnitCheckoutPaymentComponent
from app.domains.pos.unit_checkout_payment_repository import UnitCheckoutPaymentRepository
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import set_receiving_context
from tests.domains.pos.test_mixed_cash_checkout import complete as complete_mixed
from tests.domains.pos.test_mixed_cash_checkout import facts as mixed_facts
from tests.domains.pos.test_mixed_physical_checkout_preparation import legacy_with_stock
from tests.domains.pos.test_physical_cash_checkout import complete as complete_physical
from tests.domains.pos.test_physical_cash_checkout import facts as physical_facts
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


async def request_for(session: AsyncSession, mixed: bool) -> CashConfirmationRequestV2:
    source = await checkout_source(session)
    if mixed:
        await legacy_with_stock(session, source.sale)
    await put_physical(session, source.sale.id, position=3 if mixed else 1)
    return await saved_request(session, source.sale)


async def complete(
    session: AsyncSession, request: CashConfirmationRequestV2, mixed: bool
) -> PhysicalCheckoutResultV3 | MixedCheckoutResultV4:
    if mixed:
        return await complete_mixed(session, request)
    return await complete_physical(session, request)


async def journal(session: AsyncSession) -> list[object]:
    return list(
        (
            await session.execute(
                text(
                    "SELECT to_jsonb(row) FROM pos_unit_checkout_payment_component row "
                    "WHERE tenant_id=:tenant ORDER BY id"
                ),
                {"tenant": runtime(session).stock.tenant_id},
            )
        ).scalars()
    )


async def read_status(
    session: AsyncSession, request: CashConfirmationRequestV2, mixed: bool
) -> PhysicalCheckoutResultV3 | MixedCheckoutResultV4:
    context = runtime(session)
    service = (
        MixedCashCheckoutStatusService(MixedCashCheckoutStatusRepository(session))
        if mixed
        else PhysicalCashCheckoutStatusService(PhysicalCashCheckoutStatusRepository(session))
    )
    status = await service.read(
        tenant_id=context.stock.tenant_id,
        sale_id=request.sale_id,
        operation_id=request.operation_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
    )
    assert status.state == "completed"
    return status.result


@pytest.mark.parametrize("mixed", [False, True])
async def test_cash_checkout_records_exact_payment_once(
    db_session: AsyncSession, mixed: bool
) -> None:
    request = await request_for(db_session, mixed)
    result = await complete(db_session, request, mixed)
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    row = (
        await db_session.execute(
            text("SELECT * FROM pos_unit_checkout_payment_component WHERE id=:id"),
            {"id": result.payments[0].id},
        )
    ).one()
    assert row.tenant_id == result.tenant_id
    assert row.sale_id == result.sale_id
    assert row.method == "cash" and row.currency == "TJS"
    assert row.amount == result.total_amount == result.payments[0].amount
    assert row.payment_attempt_id is None
    assert row.physical_checkout_id == (None if mixed else result.operation_id)
    assert row.mixed_checkout_id == (result.operation_id if mixed else None)
    assert row.created_by == runtime(db_session).actor_id
    assert row.created_at is not None
    table = "pos_mixed_checkout" if mixed else "pos_physical_checkout"
    version = await db_session.scalar(
        text(f"SELECT payment_journal_version FROM {table} WHERE id=:id"),
        {"id": result.operation_id},
    )
    assert version == 2
    before = await journal(db_session)
    facts = mixed_facts if mixed else physical_facts
    before_facts = await facts(db_session)
    assert await complete(db_session, request, mixed) == result
    assert await read_status(db_session, request, mixed) == result
    assert await journal(db_session) == before
    assert await facts(db_session) == before_facts
    audit_snapshots = (
        await db_session.execute(
            text(
                "SELECT new_values FROM audit_log "
                "WHERE table_name='pos_unit_checkout_payment_component' AND record_id=:id"
            ),
            {"id": result.payments[0].id},
        )
    ).scalars().all()
    assert len(audit_snapshots) == 1
    audit_snapshot = audit_snapshots[0]
    assert isinstance(audit_snapshot, dict)
    assert set(audit_snapshot) == {"created_at", "created_by"}
    assert audit_snapshot["created_by"] == str(runtime(db_session).actor_id)
    assert audit_snapshot["created_at"] is not None


@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("mutation", ["UPDATE", "DELETE"])
async def test_journal_rows_cannot_be_changed_or_deleted(
    db_session: AsyncSession, mixed: bool, mutation: str
) -> None:
    request = await request_for(db_session, mixed)
    result = await complete(db_session, request, mixed)
    before = await journal(db_session)
    statement = (
        "UPDATE pos_unit_checkout_payment_component SET amount=amount WHERE id=:id"
        if mutation == "UPDATE"
        else "DELETE FROM pos_unit_checkout_payment_component WHERE id=:id"
    )
    with pytest.raises(DBAPIError) as denied:
        async with db_session.begin_nested():
            await db_session.execute(text(statement), {"id": result.payments[0].id})
    assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    assert await journal(db_session) == before


@pytest.mark.parametrize("mixed", [False, True])
async def test_outer_rollback_removes_journal_and_all_checkout_effects(
    db_session: AsyncSession, mixed: bool
) -> None:
    request = await request_for(db_session, mixed)
    facts = mixed_facts if mixed else physical_facts
    before = await facts(db_session)
    before_journal = await journal(db_session)
    async with db_session.begin_nested() as caller:
        await complete(db_session, request, mixed)
        await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        assert len(await journal(db_session)) == len(before_journal) + 1
        await caller.rollback()
    assert await facts(db_session) == before
    assert await journal(db_session) == before_journal


@pytest.mark.parametrize("mixed", [False, True])
async def test_foreign_tenant_cannot_observe_or_insert_journal(
    db_session: AsyncSession, mixed: bool
) -> None:
    request = await request_for(db_session, mixed)
    result = await complete(db_session, request, mixed)
    context = runtime(db_session)
    before = await journal(db_session)
    await db_session.execute(
        text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(uuid4())}
    )
    assert await journal(db_session) == []
    with pytest.raises(DBAPIError) as denied:
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO pos_unit_checkout_payment_component "
                    "(tenant_id,id,physical_checkout_id,mixed_checkout_id,sale_id,"
                    "method,amount,currency,created_by) "
                    "VALUES(:tenant,:id,:physical,:mixed,:sale,'cash',:amount,'TJS',:actor)"
                ),
                {
                    "tenant": context.stock.tenant_id,
                    "id": result.payments[0].id,
                    "physical": None if mixed else result.operation_id,
                    "mixed": result.operation_id if mixed else None,
                    "sale": result.sale_id,
                    "amount": result.total_amount,
                    "actor": context.actor_id,
                },
            )
    assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await journal(db_session) == before


@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("damage", ["missing", "amount", "method", "parent", "attempt", "id"])
async def test_missing_or_corrupt_component_rolls_back_the_entire_checkout(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, mixed: bool, damage: str
) -> None:
    request = await request_for(db_session, mixed)
    facts = mixed_facts if mixed else physical_facts
    before = await facts(db_session)
    before_journal = await journal(db_session)
    insert = UnitCheckoutPaymentRepository.insert

    async def corrupt(
        repository: UnitCheckoutPaymentRepository, component: UnitCheckoutPaymentComponent
    ) -> None:
        if damage == "missing":
            return
        if damage == "amount":
            component.amount += Decimal("0.01")
        elif damage == "method":
            component.method = "card"
        elif damage == "parent":
            component.sale_id = uuid4()
        elif damage == "id":
            component.id = uuid4()
        else:
            component.payment_attempt_id = uuid4()
        await insert(repository, component)

    monkeypatch.setattr(UnitCheckoutPaymentRepository, "insert", corrupt)
    with pytest.raises(DBAPIError) as rejected:
        await complete(db_session, request, mixed)
    if damage == "id":
        assert getattr(rejected.value.orig, "sqlstate", None) == "23514"
    assert await journal(db_session) == before_journal
    assert await facts(db_session) == before


@pytest.mark.parametrize("mixed", [False, True])
async def test_existing_version_one_checkout_replays_without_journal_backfill(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, mixed: bool
) -> None:
    request = await request_for(db_session, mixed)
    physical_insert = PhysicalCashCheckoutRepository.insert
    mixed_insert = MixedCashCheckoutRepository.insert

    async def physical_v1(
        repository: PhysicalCashCheckoutRepository, marker: PhysicalCashCheckout
    ) -> None:
        marker.payment_journal_version = 1
        await physical_insert(repository, marker)

    async def mixed_v1(repository: MixedCashCheckoutRepository, marker: MixedCashCheckout) -> None:
        marker.payment_journal_version = 1
        await mixed_insert(repository, marker)

    async def no_component(
        repository: UnitCheckoutPaymentRepository, component: UnitCheckoutPaymentComponent
    ) -> None:
        assert component.id == request.operation_id

    with monkeypatch.context() as legacy:
        legacy.setattr(PhysicalCashCheckoutRepository, "insert", physical_v1)
        legacy.setattr(MixedCashCheckoutRepository, "insert", mixed_v1)
        legacy.setattr(UnitCheckoutPaymentRepository, "insert", no_component)
        result = await complete(db_session, request, mixed)
        await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert await journal(db_session) == []
    table = "pos_mixed_checkout" if mixed else "pos_physical_checkout"
    version = await db_session.scalar(
        text(f"SELECT payment_journal_version FROM {table} WHERE id=:id"),
        {"id": result.operation_id},
    )
    assert version == 1
    assert await complete(db_session, request, mixed) == result
    assert await read_status(db_session, request, mixed) == result
    assert await journal(db_session) == []


@pytest.mark.parametrize("mixed", [False, True])
async def test_deferred_constraint_rejects_missing_component_without_service_validation(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, mixed: bool
) -> None:
    request = await request_for(db_session, mixed)
    facts = mixed_facts if mixed else physical_facts
    before = await facts(db_session)

    async def no_component(
        repository: UnitCheckoutPaymentRepository, component: UnitCheckoutPaymentComponent
    ) -> None:
        assert component.id == request.operation_id

    async def no_validation(
        repository: PhysicalCashCheckoutRepository | MixedCashCheckoutRepository,
    ) -> None:
        assert repository.session is db_session

    monkeypatch.setattr(UnitCheckoutPaymentRepository, "insert", no_component)
    monkeypatch.setattr(PhysicalCashCheckoutRepository, "validate_completion", no_validation)
    monkeypatch.setattr(MixedCashCheckoutRepository, "validate_completion", no_validation)
    with pytest.raises(DBAPIError, match="unit_checkout_payment_components_invalid"):
        async with db_session.begin_nested():
            result = await complete(db_session, request, mixed)
            assert result.operation_id == request.operation_id
            assert await journal(db_session) == []
            await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert await facts(db_session) == before
    assert await journal(db_session) == []


@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("damage", ["missing", "amount"])
@pytest.mark.parametrize("channel", ["post", "get"])
async def test_replay_rejects_inconsistent_journal_without_writing(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    mixed: bool,
    damage: str,
    channel: str,
) -> None:
    request = await request_for(db_session, mixed)
    result = await complete(db_session, request, mixed)
    facts = mixed_facts if mixed else physical_facts
    before = await facts(db_session)
    before_journal = await journal(db_session)

    async def broken_read(
        repository: UnitCheckoutPaymentRepository,
        tenant_id: UUID,
        checkout_id: UUID,
        *,
        mixed: bool,
    ) -> tuple[UnitCheckoutPaymentComponent, ...]:
        assert tenant_id == result.tenant_id and checkout_id == result.operation_id
        if damage == "missing":
            return ()
        return (
            UnitCheckoutPaymentComponent(
                tenant_id=tenant_id,
                id=result.payments[0].id,
                physical_checkout_id=None if mixed else checkout_id,
                mixed_checkout_id=checkout_id if mixed else None,
                sale_id=result.sale_id,
                method="cash",
                amount=result.total_amount + Decimal("0.01"),
                currency="TJS",
                payment_attempt_id=None,
                created_by=result.cashier_id,
            ),
        )

    monkeypatch.setattr(UnitCheckoutPaymentRepository, "for_checkout", broken_read)
    with pytest.raises(AurumError, match="payment journal is inconsistent"):
        if channel == "get":
            await read_status(db_session, request, mixed)
        else:
            await complete(db_session, request, mixed)
    assert await facts(db_session) == before
    assert await journal(db_session) == before_journal
