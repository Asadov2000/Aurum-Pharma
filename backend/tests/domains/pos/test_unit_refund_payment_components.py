"""Refund payment journals are exact, immutable and transactionally complete."""

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import AurumError, BusinessRuleError
from app.domains.pos.mixed_cash_refund_models import MixedCashRefund
from app.domains.pos.mixed_cash_refund_repository import MixedCashRefundRepository
from app.domains.pos.mixed_cash_refund_schemas import MixedCashRefundResultV4
from app.domains.pos.mixed_cash_refund_status_repository import MixedCashRefundStatusRepository
from app.domains.pos.mixed_cash_refund_status_schemas import MixedRefundOperationCompleted
from app.domains.pos.mixed_cash_refund_status_service import MixedCashRefundStatusService
from app.domains.pos.physical_cash_refund_models import PhysicalCashRefund
from app.domains.pos.physical_cash_refund_repository import PhysicalCashRefundRepository
from app.domains.pos.physical_cash_refund_schemas import PhysicalCashRefundResultV3
from app.domains.pos.physical_cash_refund_status_repository import (
    PhysicalCashRefundStatusRepository,
)
from app.domains.pos.physical_cash_refund_status_schemas import PhysicalRefundOperationCompleted
from app.domains.pos.physical_cash_refund_status_service import PhysicalCashRefundStatusService
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.pos.unit_refund_payment_models import UnitRefundPaymentComponent
from app.domains.pos.unit_refund_payment_repository import UnitRefundPaymentRepository
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.pos.test_mixed_cash_refund import facts as mixed_facts
from tests.domains.pos.test_mixed_cash_refund import refund as refund_mixed
from tests.domains.pos.test_mixed_return_journal import MixedSource, mixed_source
from tests.domains.pos.test_physical_cash_checkout import complete as checkout_physical
from tests.domains.pos.test_physical_cash_refund import close_shift
from tests.domains.pos.test_physical_cash_refund import facts as physical_facts
from tests.domains.pos.test_physical_cash_refund import refund as refund_physical
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture


@dataclass(frozen=True)
class RefundRequest:
    operation_id: UUID
    parent_sale_id: UUID
    shift_id: UUID
    line_id: UUID
    mixed_source: MixedSource | None


async def request_for(session: AsyncSession, mixed: bool) -> RefundRequest:
    if mixed:
        source = await mixed_source(session)
        return RefundRequest(
            uuid4(), source.original.sale_id, source.original.shift_id, source.physical_id, source
        )
    physical = await checkout_source(session)
    item = await put_physical(session, physical.sale.id, sale_format="pack", quantity=2)
    await checkout_physical(session, await saved_request(session, physical.sale))
    return RefundRequest(uuid4(), physical.sale.id, physical.sale.shift_id, item.id, None)


async def complete(
    session: AsyncSession, request: RefundRequest, mixed: bool
) -> PhysicalCashRefundResultV3 | MixedCashRefundResultV4:
    if mixed:
        assert request.mixed_source is not None
        return await refund_mixed(session, request.mixed_source, operation_id=request.operation_id)
    return await refund_physical(
        session,
        request.parent_sale_id,
        request.line_id,
        request.shift_id,
        operation_id=request.operation_id,
    )


async def journal(session: AsyncSession) -> list[object]:
    return list(
        (
            await session.execute(
                text(
                    "SELECT to_jsonb(row) FROM pos_unit_refund_payment_component row "
                    "WHERE tenant_id=:tenant ORDER BY id"
                ),
                {"tenant": runtime(session).stock.tenant_id},
            )
        ).scalars()
    )


async def read_status(
    session: AsyncSession, request: RefundRequest, mixed: bool
) -> PhysicalRefundOperationCompleted | MixedRefundOperationCompleted:
    context = runtime(session)
    service = (
        MixedCashRefundStatusService(MixedCashRefundStatusRepository(session))
        if mixed
        else PhysicalCashRefundStatusService(PhysicalCashRefundStatusRepository(session))
    )
    status = await service.read(
        tenant_id=context.stock.tenant_id,
        parent_sale_id=request.parent_sale_id,
        operation_id=request.operation_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
    )
    assert isinstance(status, (PhysicalRefundOperationCompleted, MixedRefundOperationCompleted))
    return status


@pytest.mark.parametrize("mixed", [False, True])
async def test_cash_refund_records_exact_payment_once(
    db_session: AsyncSession, mixed: bool
) -> None:
    request = await request_for(db_session, mixed)
    result = await complete(db_session, request, mixed)
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    row = (
        await db_session.execute(
            text("SELECT * FROM pos_unit_refund_payment_component WHERE id=:id"),
            {"id": result.payment_id},
        )
    ).one()
    assert row.tenant_id == result.tenant_id
    assert row.sale_id == result.sale_id
    assert row.parent_sale_id == result.parent_sale_id
    assert row.original_payment_id == await db_session.scalar(
        text("SELECT id FROM sale_payment WHERE sale_id=:id"), {"id": result.parent_sale_id}
    )
    assert row.method == "cash" and row.currency == "TJS"
    assert row.amount == result.total_amount
    assert row.physical_refund_id == (None if mixed else result.operation_id)
    assert row.mixed_refund_id == (result.operation_id if mixed else None)
    assert row.created_by == runtime(db_session).actor_id
    assert row.created_at is not None
    table = "pos_mixed_cash_refund" if mixed else "pos_physical_cash_refund"
    version = await db_session.scalar(
        text(f"SELECT payment_journal_version FROM {table} WHERE id=:id"),
        {"id": result.operation_id},
    )
    assert version == 2
    before = await journal(db_session)
    facts = mixed_facts if mixed else physical_facts
    before_facts = await facts(db_session)
    assert await complete(db_session, request, mixed) == result
    recovered = await read_status(db_session, request, mixed)
    assert recovered.sale_id == result.sale_id and recovered.receipt == result.receipt
    assert await journal(db_session) == before
    assert await facts(db_session) == before_facts
    audit_rows = await db_session.execute(
        text(
            "SELECT new_values FROM audit_log "
            "WHERE table_name='pos_unit_refund_payment_component' AND record_id=:id"
        ),
        {"id": result.payment_id},
    )
    audit_snapshots = audit_rows.scalars().all()
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
        "UPDATE pos_unit_refund_payment_component SET amount=amount WHERE id=:id"
        if mutation == "UPDATE"
        else "DELETE FROM pos_unit_refund_payment_component WHERE id=:id"
    )
    with pytest.raises(DBAPIError) as denied:
        async with db_session.begin_nested():
            await db_session.execute(text(statement), {"id": result.payment_id})
    assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    assert await journal(db_session) == before


@pytest.mark.parametrize("mixed", [False, True])
async def test_outer_rollback_removes_journal_and_all_refund_effects(
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
                    "INSERT INTO pos_unit_refund_payment_component "
                    "(tenant_id,id,physical_refund_id,mixed_refund_id,sale_id,"
                    "parent_sale_id,original_payment_id,method,amount,currency,created_by) "
                    "VALUES(:tenant,:id,:physical,:mixed,:sale,:parent,:original,"
                    "'cash',:amount,'TJS',:actor)"
                ),
                {
                    "tenant": context.stock.tenant_id,
                    "id": result.payment_id,
                    "physical": None if mixed else result.operation_id,
                    "mixed": result.operation_id if mixed else None,
                    "sale": result.sale_id,
                    "parent": result.parent_sale_id,
                    "original": uuid4(),
                    "amount": result.total_amount,
                    "actor": context.actor_id,
                },
            )
    assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await journal(db_session) == before


@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("damage", ["missing", "amount", "method", "parent", "original", "id"])
async def test_missing_or_corrupt_component_rolls_back_the_entire_refund(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, mixed: bool, damage: str
) -> None:
    request = await request_for(db_session, mixed)
    facts = mixed_facts if mixed else physical_facts
    before = await facts(db_session)
    before_journal = await journal(db_session)
    insert = UnitRefundPaymentRepository.insert

    async def corrupt(
        repository: UnitRefundPaymentRepository, component: UnitRefundPaymentComponent
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
            component.original_payment_id = uuid4()
        await insert(repository, component)

    monkeypatch.setattr(UnitRefundPaymentRepository, "insert", corrupt)
    with pytest.raises(DBAPIError) as rejected:
        await complete(db_session, request, mixed)
    if damage in {"id", "original"}:
        assert getattr(rejected.value.orig, "sqlstate", None) == "23514"
    assert await journal(db_session) == before_journal
    assert await facts(db_session) == before


@pytest.mark.parametrize("mixed", [False, True])
async def test_existing_version_one_refund_replays_without_journal_backfill(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, mixed: bool
) -> None:
    request = await request_for(db_session, mixed)
    physical_insert = PhysicalCashRefundRepository.insert
    mixed_insert = MixedCashRefundRepository.insert

    async def physical_v1(
        repository: PhysicalCashRefundRepository, marker: PhysicalCashRefund
    ) -> None:
        marker.payment_journal_version = 1
        await physical_insert(repository, marker)

    async def mixed_v1(repository: MixedCashRefundRepository, marker: MixedCashRefund) -> None:
        marker.payment_journal_version = 1
        await mixed_insert(repository, marker)

    async def no_component(
        repository: UnitRefundPaymentRepository, component: UnitRefundPaymentComponent
    ) -> None:
        assert component.id == request.operation_id

    with monkeypatch.context() as legacy:
        legacy.setattr(PhysicalCashRefundRepository, "insert", physical_v1)
        legacy.setattr(MixedCashRefundRepository, "insert", mixed_v1)
        legacy.setattr(UnitRefundPaymentRepository, "insert", no_component)
        result = await complete(db_session, request, mixed)
        await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert await journal(db_session) == []
    table = "pos_mixed_cash_refund" if mixed else "pos_physical_cash_refund"
    version = await db_session.scalar(
        text(f"SELECT payment_journal_version FROM {table} WHERE id=:id"),
        {"id": result.operation_id},
    )
    assert version == 1
    assert await complete(db_session, request, mixed) == result
    recovered = await read_status(db_session, request, mixed)
    assert recovered.sale_id == result.sale_id and recovered.receipt == result.receipt
    assert await journal(db_session) == []


@pytest.mark.parametrize("mixed", [False, True])
async def test_deferred_constraint_rejects_missing_component_without_service_validation(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, mixed: bool
) -> None:
    request = await request_for(db_session, mixed)
    facts = mixed_facts if mixed else physical_facts
    before = await facts(db_session)

    async def no_component(
        repository: UnitRefundPaymentRepository, component: UnitRefundPaymentComponent
    ) -> None:
        assert component.id == request.operation_id

    async def no_validation(
        repository: PhysicalCashRefundRepository | MixedCashRefundRepository,
    ) -> None:
        assert repository.session is db_session

    monkeypatch.setattr(UnitRefundPaymentRepository, "insert", no_component)
    monkeypatch.setattr(PhysicalCashRefundRepository, "validate_completion", no_validation)
    monkeypatch.setattr(MixedCashRefundRepository, "validate_completion", no_validation)
    with pytest.raises(DBAPIError, match="unit_refund_payment_components_invalid"):
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
        repository: UnitRefundPaymentRepository,
        tenant_id: UUID,
        refund_id: UUID,
        *,
        mixed: bool,
    ) -> tuple[UnitRefundPaymentComponent, ...]:
        assert tenant_id == result.tenant_id and refund_id == result.operation_id
        if damage == "missing":
            return ()
        return (
            UnitRefundPaymentComponent(
                tenant_id=tenant_id,
                id=result.payment_id,
                physical_refund_id=None if mixed else refund_id,
                mixed_refund_id=refund_id if mixed else None,
                sale_id=result.sale_id,
                parent_sale_id=result.parent_sale_id,
                original_payment_id=uuid4(),
                method="cash",
                amount=result.total_amount + Decimal("0.01"),
                currency="TJS",
                created_by=result.actor_id,
            ),
        )

    monkeypatch.setattr(UnitRefundPaymentRepository, "for_refund", broken_read)
    with pytest.raises(AurumError, match="payment journal is inconsistent"):
        if channel == "get":
            await read_status(db_session, request, mixed)
        else:
            await complete(db_session, request, mixed)
    assert await facts(db_session) == before
    assert await journal(db_session) == before_journal


@pytest.mark.parametrize("mixed", [False, True])
@pytest.mark.parametrize("legacy_first", [False, True])
async def test_partial_payouts_include_legacy_history_in_original_payment_limit(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch, mixed: bool, legacy_first: bool
) -> None:
    request = await request_for(db_session, mixed)
    physical_insert = PhysicalCashRefundRepository.insert
    mixed_insert = MixedCashRefundRepository.insert

    async def physical_v1(
        repository: PhysicalCashRefundRepository, marker: PhysicalCashRefund
    ) -> None:
        marker.payment_journal_version = 1
        await physical_insert(repository, marker)

    async def mixed_v1(repository: MixedCashRefundRepository, marker: MixedCashRefund) -> None:
        marker.payment_journal_version = 1
        await mixed_insert(repository, marker)

    async def no_component(
        repository: UnitRefundPaymentRepository, component: UnitRefundPaymentComponent
    ) -> None:
        assert component.id == request.operation_id

    async def partial(
        quantity: int, operation_id: UUID
    ) -> PhysicalCashRefundResultV3 | MixedCashRefundResultV4:
        if mixed:
            assert request.mixed_source is not None
            return await refund_mixed(
                db_session,
                request.mixed_source,
                (PhysicalReturnSelection(request.line_id, "unit", quantity),),
                operation_id=operation_id,
            )
        return await refund_physical(
            db_session,
            request.parent_sale_id,
            request.line_id,
            request.shift_id,
            quantity,
            operation_id=operation_id,
        )

    with monkeypatch.context() as legacy:
        if legacy_first:
            legacy.setattr(PhysicalCashRefundRepository, "insert", physical_v1)
            legacy.setattr(MixedCashRefundRepository, "insert", mixed_v1)
            legacy.setattr(UnitRefundPaymentRepository, "insert", no_component)
        first = await partial(1, request.operation_id)
        await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    second = await partial(59, uuid4())
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    original_amount = await db_session.scalar(
        text("SELECT amount FROM sale_payment WHERE sale_id=:id"),
        {"id": request.parent_sale_id},
    )
    assert first.total_amount + second.total_amount <= original_amount
    if not mixed:
        assert first.total_amount + second.total_amount == original_amount
    assert len(await journal(db_session)) == (1 if legacy_first else 2)
    before = await journal(db_session)
    with pytest.raises(BusinessRuleError):
        await partial(1, uuid4())
    assert await journal(db_session) == before
    if not mixed:
        assert await complete(db_session, request, mixed) == first


@pytest.mark.parametrize("mixed", [False, True])
async def test_refund_only_actor_journals_and_recovers_another_cashiers_original_payment(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    mixed: bool,
) -> None:
    request = await request_for(db_session, mixed)
    context = runtime(db_session)
    register_id = await db_session.scalar(
        text("SELECT register_id FROM sale WHERE id=:id"), {"id": request.parent_sale_id}
    )
    original_payment = await db_session.scalar(
        text("SELECT id FROM sale_payment WHERE sale_id=:id"), {"id": request.parent_sale_id}
    )
    await close_shift(db_session, request.shift_id)
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, context.stock, actor)
    shift = await POSService(POSRepository(db_session)).open_shift(
        tenant_id=context.stock.tenant_id,
        register_id=register_id,
        opened_by_user_id=actor,
        opening_cash=Decimal("100.00"),
        allowed_branch_ids={context.stock.branch_id},
    )
    result: PhysicalCashRefundResultV3 | MixedCashRefundResultV4
    if mixed:
        assert request.mixed_source is not None
        result = await refund_mixed(
            db_session,
            request.mixed_source,
            actor_id=actor,
            shift_id=shift.id,
            operation_id=request.operation_id,
        )
    else:
        result = await refund_physical(
            db_session,
            request.parent_sale_id,
            request.line_id,
            shift.id,
            actor_id=actor,
            operation_id=request.operation_id,
        )
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    components = await UnitRefundPaymentRepository(db_session).for_refund(
        context.stock.tenant_id, result.operation_id, mixed=mixed
    )
    assert len(components) == 1
    assert components[0].original_payment_id == original_payment
    assert components[0].created_by == actor != context.actor_id
    before = await journal(db_session)
    service = (
        MixedCashRefundStatusService(MixedCashRefundStatusRepository(db_session))
        if mixed
        else PhysicalCashRefundStatusService(PhysicalCashRefundStatusRepository(db_session))
    )
    recovered = await service.read(
        tenant_id=context.stock.tenant_id,
        parent_sale_id=request.parent_sale_id,
        operation_id=request.operation_id,
        actor_id=actor,
        allowed_branch_ids={context.stock.branch_id},
    )
    assert isinstance(recovered, (PhysicalRefundOperationCompleted, MixedRefundOperationCompleted))
    assert recovered.receipt == result.receipt
    assert await journal(db_session) == before
