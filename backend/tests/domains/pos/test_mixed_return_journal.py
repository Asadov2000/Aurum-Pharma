"""Mixed quarantine receipts are exact, atomic and independent of cash and stock."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.inventory.repository import InventoryRepository
from app.domains.inventory.unit_stock_v2 import SaleFormatV2
from app.domains.pos.mixed_documents import MixedCheckoutResultV4
from app.domains.pos.mixed_return_allocation import LegacyReturnSelection, MixedReturnSelection
from app.domains.pos.mixed_return_journal_models import (
    MixedReturnCommand,
    MixedReturnLine,
    MixedReturnSlice,
)
from app.domains.pos.mixed_return_journal_repository import MixedReturnJournalRepository
from app.domains.pos.mixed_return_journal_schemas import MixedReturnJournalResult
from app.domains.pos.mixed_return_journal_service import MixedReturnJournalService
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.physical_return_journal_schemas import PhysicalReturnJournalSlice
from app.domains.pos.physical_return_journal_service import PhysicalReturnJournalService
from app.domains.pos.repository import POSRepository
from tests.domains.incoming.test_unit_acceptance import (
    ReceivingRuntime,
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.inventory.test_unit_ledger import seed_ledger_context
from tests.domains.pos.test_mixed_cash_checkout import complete, facts
from tests.domains.pos.test_mixed_physical_checkout_preparation import legacy_with_stock
from tests.domains.pos.test_physical_cash_checkout import complete as complete_physical
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture
TABLES = ("pos_mixed_return_command", "pos_mixed_return_line", "pos_mixed_return_slice")


@dataclass(frozen=True)
class MixedSource:
    original: MixedCheckoutResultV4
    legacy_id: UUID
    physical_id: UUID
    batch_id: UUID

    def selections(
        self, legacy_quantity: str = "0.001", physical_quantity: int = 1
    ) -> tuple[MixedReturnSelection, ...]:
        return (
            LegacyReturnSelection(self.legacy_id, Decimal(legacy_quantity)),
            PhysicalReturnSelection(self.physical_id, "unit", physical_quantity),
        )


async def mixed_source(
    session: AsyncSession,
    *,
    sale_format: SaleFormatV2 = "pack",
    physical_quantity: int = 2,
    fractional_legacy: bool = False,
) -> MixedSource:
    source = await checkout_source(session)
    legacy = await legacy_with_stock(session, source.sale)
    if fractional_legacy:
        await InventoryRepository(session).insert_movement(
            tenant_id=source.sale.tenant_id,
            batch_id=legacy.batch_id,
            movement_type="incoming",
            qty_delta=Decimal("0.002"),
        )
        await POSRepository(session).update_item(
            legacy,
            qty=Decimal("0.003"),
            discount_amount=Decimal("0.07"),
            total_price=Decimal("0.01"),
        )
        await POSRepository(session).update_sale(source.sale, total_amount=Decimal("0.01"))
    physical = await put_physical(
        session,
        source.sale.id,
        position=2,
        sale_format=sale_format,
        quantity=physical_quantity,
        discount=Decimal("0.01"),
    )
    original = await complete(session, await saved_request(session, source.sale))
    return MixedSource(original, legacy.id, physical.id, legacy.batch_id)


async def receive(
    session: AsyncSession,
    source: MixedSource,
    selections: tuple[MixedReturnSelection, ...] | None = None,
    *,
    operation_id: UUID | None = None,
    actor_id: UUID | None = None,
    tenant_id: UUID | None = None,
    parent_sale_id: UUID | None = None,
) -> MixedReturnJournalResult:
    context = runtime(session)
    return await MixedReturnJournalService(MixedReturnJournalRepository(session)).receive(
        tenant_id=tenant_id or context.stock.tenant_id,
        actor_id=actor_id or context.actor_id,
        operation_id=operation_id or uuid4(),
        parent_sale_id=parent_sale_id or source.original.sale_id,
        selections=source.selections() if selections is None else selections,
    )


async def journal_facts(session: AsyncSession) -> dict[str, list[object]]:
    return {
        table: list(
            (
                await session.execute(
                    text(
                        f"SELECT to_jsonb(row) FROM {table} row WHERE tenant_id=:tenant "
                        "ORDER BY to_jsonb(row)::text"
                    ),
                    {"tenant": runtime(session).stock.tenant_id},
                )
            ).scalars()
        )
        for table in TABLES
    }


async def sale_stock_facts(session: AsyncSession) -> dict[str, list[object]]:
    result = await facts(session)
    del result["audit_log"]
    return result


@pytest.mark.parametrize(
    "sale_format,quantity,units", [("unit", 2, 2), ("blister", 1, 10), ("pack", 1, 30)]
)
async def test_both_kinds_are_received_in_original_order_without_cash_or_stock_effects(
    db_session: AsyncSession, sale_format: SaleFormatV2, quantity: int, units: int
) -> None:
    source = await mixed_source(db_session)
    before = await sale_stock_facts(db_session)
    result = await receive(
        db_session,
        source,
        (
            PhysicalReturnSelection(source.physical_id, sale_format, quantity),
            LegacyReturnSelection(source.legacy_id, Decimal("0.001")),
        ),
    )
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert result.schema_version == 1
    assert result.document_kind == "mixed_return_quarantine"
    assert result.state == "prepared_quarantine"
    assert result.currency == "TJS"
    assert result.parent_sale_id == source.original.sale_id
    assert result.tenant_id == source.original.tenant_id
    assert result.branch_id == source.original.branch_id
    assert result.actor_id == runtime(db_session).actor_id
    assert [(row.inventory_kind, row.line_id, row.position) for row in result.lines] == [
        ("legacy_pack", source.legacy_id, 1),
        ("physical_unit", source.physical_id, 2),
    ]
    legacy, physical = result.lines
    assert legacy.inventory_kind == "legacy_pack"
    assert legacy.batch_id == source.batch_id
    assert legacy.quantity == Decimal("0.001")
    assert legacy.offset == Decimal("0.000")
    assert physical.inventory_kind == "physical_unit"
    assert physical.sale_format == sale_format
    assert physical.quantity == quantity
    assert physical.base_units == units
    assert physical.offset == 0
    assert sum(row.base_units for row in physical.slices) == units
    assert sum((row.cost_amount for row in physical.slices), Decimal()) == physical.cost_amount
    assert result.physical_cost_amount == physical.cost_amount
    assert sum((row.net_amount for row in result.lines), Decimal()) == result.total_amount
    assert MixedReturnJournalResult.model_validate_json(result.model_dump_json()) == result
    assert await sale_stock_facts(db_session) == before
    persisted = await journal_facts(db_session)
    assert len(persisted[TABLES[0]]) == 1
    assert len(persisted[TABLES[1]]) == 2
    assert len(persisted[TABLES[2]]) == len(physical.slices)


@pytest.mark.parametrize("kind", ["legacy_pack", "physical_unit"])
async def test_one_inventory_kind_can_be_returned_from_a_mixed_original(
    db_session: AsyncSession, kind: str
) -> None:
    source = await mixed_source(db_session)
    chosen = source.selections()[0 if kind == "legacy_pack" else 1]
    result = await receive(db_session, source, (chosen,))
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert len(result.lines) == 1
    assert result.lines[0].inventory_kind == kind
    assert result.lines[0].position == (1 if kind == "legacy_pack" else 2)
    if kind == "legacy_pack":
        assert result.physical_cost_amount == Decimal("0.00")
        assert not (await journal_facts(db_session))[TABLES[2]]


async def test_partial_both_kinds_conserve_original_cents_and_cost_with_zero_amount_parts(
    db_session: AsyncSession,
) -> None:
    source = await mixed_source(db_session, fractional_legacy=True)
    before = await sale_stock_facts(db_session)
    first = await receive(db_session, source, source.selections("0.001", 1))
    second = await receive(db_session, source, source.selections("0.001", 29))
    third = await receive(db_session, source, source.selections("0.001", 30))
    results = (first, second, third)
    assert [row.lines[0].offset for row in results] == [
        Decimal("0.000"),
        Decimal("0.001"),
        Decimal("0.002"),
    ]
    assert [row.lines[1].offset for row in results] == [0, 1, 30]
    assert first.lines[0].net_amount == second.lines[0].net_amount == Decimal("0.00")
    assert third.lines[0].net_amount == Decimal("0.01")
    assert sum((row.total_amount for row in results), Decimal()) == source.original.total_amount
    for index in (0, 1):
        for field in ("net_amount", "discount_amount", "gross_amount"):
            assert sum(
                (getattr(row.lines[index], field) for row in results), Decimal()
            ) == getattr(source.original.lines[index], field)
    historical_cost = await db_session.scalar(
        text(
            "SELECT sum(cost_amount) FROM unit_stock_movement_v2 "
            "WHERE command_id=:id AND units_taken>0"
        ),
        {"id": source.original.stock_command_id},
    )
    assert sum((row.physical_cost_amount for row in results), Decimal()) == historical_cost
    assert await sale_stock_facts(db_session) == before
    journal = await journal_facts(db_session)
    with pytest.raises(BusinessRuleError):
        await receive(db_session, source)
    assert await journal_facts(db_session) == journal


async def test_zero_net_legacy_return_still_claims_its_quantity(db_session: AsyncSession) -> None:
    source = await mixed_source(db_session, fractional_legacy=True)
    result = await receive(
        db_session, source, (LegacyReturnSelection(source.legacy_id, Decimal("0.001")),)
    )
    assert result.total_amount == result.physical_cost_amount == Decimal("0.00")
    assert result.lines[0].quantity == Decimal("0.001")
    next_result = await receive(
        db_session, source, (LegacyReturnSelection(source.legacy_id, Decimal("0.002")),)
    )
    assert next_result.lines[0].offset == Decimal("0.001")
    assert next_result.total_amount == Decimal("0.01")


async def test_replay_is_exact_for_reordered_selections_and_equivalent_decimals(
    db_session: AsyncSession,
) -> None:
    source = await mixed_source(db_session)
    operation_id = uuid4()
    original = await receive(db_session, source, operation_id=operation_id)
    before = await journal_facts(db_session)
    equivalent = (
        PhysicalReturnSelection(source.physical_id, "unit", 1),
        LegacyReturnSelection(source.legacy_id, Decimal("0.001000")),
    )
    assert await receive(db_session, source, equivalent, operation_id=operation_id) == original
    assert await journal_facts(db_session) == before


@pytest.mark.parametrize("change", ["quantity", "format", "parent", "line"])
async def test_reused_operation_rejects_a_changed_request_without_additional_rows(
    db_session: AsyncSession, change: str
) -> None:
    source = await mixed_source(db_session)
    operation_id = uuid4()
    await receive(db_session, source, operation_id=operation_id)
    before = await journal_facts(db_session)
    selections = source.selections()
    parent = source.original.sale_id
    if change == "quantity":
        selections = source.selections(physical_quantity=2)
    elif change == "format":
        selections = (selections[0], PhysicalReturnSelection(source.physical_id, "blister", 1))
    elif change == "parent":
        parent = uuid4()
    else:
        selections = (LegacyReturnSelection(uuid4(), Decimal("0.001")), selections[1])
    with pytest.raises(ConflictError):
        await receive(
            db_session, source, selections, operation_id=operation_id, parent_sale_id=parent
        )
    assert await journal_facts(db_session) == before


async def test_replay_and_new_return_use_history_after_shift_closure_and_catalog_changes(
    db_session: AsyncSession,
) -> None:
    source = await mixed_source(db_session, fractional_legacy=True)
    first = await receive(db_session, source)
    await db_session.execute(
        text("UPDATE shift SET status='closed',closed_at=now() WHERE id=:id"),
        {"id": source.original.shift_id},
    )
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET brand_name='Later name',base_price=999.00,"
            "is_active=false WHERE id=:id"
        ),
        {"id": source.original.lines[1].catalog_id},
    )
    await db_session.execute(
        text("UPDATE batch SET is_blocked=true WHERE id=:id"), {"id": source.batch_id}
    )
    before = await sale_stock_facts(db_session)
    journal = await journal_facts(db_session)
    assert await receive(db_session, source, operation_id=first.operation_id) == first
    assert await journal_facts(db_session) == journal
    later = await receive(db_session, source)
    assert later.lines[0].offset == Decimal("0.001")
    assert later.lines[1].offset == 1
    assert await sale_stock_facts(db_session) == before


@pytest.mark.parametrize("kind", ["legacy_pack", "physical_unit"])
async def test_overreturn_of_either_kind_rejects_the_whole_command(
    db_session: AsyncSession, kind: str
) -> None:
    source = await mixed_source(db_session)
    selections = source.selections(
        "0.002" if kind == "legacy_pack" else "0.001", 61 if kind == "physical_unit" else 1
    )
    before = await journal_facts(db_session)
    stocks = await sale_stock_facts(db_session)
    with pytest.raises(BusinessRuleError):
        await receive(db_session, source, selections)
    assert await journal_facts(db_session) == before
    assert await sale_stock_facts(db_session) == stocks
    result = await receive(db_session, source)
    assert result.lines[0].offset == 0
    assert result.lines[1].offset == 0


async def test_failure_after_journal_flush_rolls_back_both_kinds_and_source_cursors(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = await mixed_source(db_session)
    before = await journal_facts(db_session)
    stocks = await sale_stock_facts(db_session)
    original_insert = MixedReturnJournalRepository.insert

    async def insert_then_fail(
        self: MixedReturnJournalRepository,
        command: MixedReturnCommand,
        lines: tuple[MixedReturnLine, ...],
        slices: tuple[MixedReturnSlice, ...],
    ) -> None:
        await original_insert(self, command, lines, slices)
        raise RuntimeError("Injected failure after quarantine persistence")

    with monkeypatch.context() as patch:
        patch.setattr(MixedReturnJournalRepository, "insert", insert_then_fail)
        with pytest.raises(RuntimeError, match="Injected failure"):
            await receive(db_session, source)
    assert await journal_facts(db_session) == before
    assert await sale_stock_facts(db_session) == stocks
    retry = await receive(db_session, source)
    assert [line.offset for line in retry.lines] == [Decimal("0.000"), 0]


async def test_late_physical_provenance_failure_does_not_claim_the_legacy_return(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = await mixed_source(db_session)
    before = await journal_facts(db_session)
    stocks = await sale_stock_facts(db_session)

    def fail_slices(*args: object, **kwargs: object) -> tuple[PhysicalReturnJournalSlice, ...]:
        raise BusinessRuleError("Injected invalid historical physical source")

    with monkeypatch.context() as patch:
        patch.setattr(PhysicalReturnJournalService, "_slices", staticmethod(fail_slices))
        with pytest.raises(BusinessRuleError, match="Injected invalid historical"):
            await receive(db_session, source)
    assert await journal_facts(db_session) == before
    assert await sale_stock_facts(db_session) == stocks
    retry = await receive(db_session, source)
    assert [line.offset for line in retry.lines] == [Decimal("0.000"), 0]


@pytest.mark.parametrize(
    "case",
    ["empty", "duplicate", "unknown", "wrong_kind", "zero", "sub_thousandth", "nonfinite"],
)
async def test_invalid_selections_never_create_quarantine_history(
    db_session: AsyncSession, case: str
) -> None:
    source = await mixed_source(db_session)
    selections = source.selections()
    if case == "empty":
        selections = ()
    elif case == "duplicate":
        selections = (selections[0], selections[0])
    elif case == "unknown":
        selections = (LegacyReturnSelection(uuid4(), Decimal("0.001")),)
    elif case == "wrong_kind":
        selections = (LegacyReturnSelection(source.physical_id, Decimal("0.001")),)
    else:
        quantity = {"zero": "0.000", "sub_thousandth": "0.0001", "nonfinite": "NaN"}[case]
        selections = (LegacyReturnSelection(source.legacy_id, Decimal(quantity)),)
    before = await journal_facts(db_session)
    with pytest.raises(BusinessRuleError):
        await receive(db_session, source, selections)
    assert await journal_facts(db_session) == before


async def test_outer_rollback_removes_whole_command_without_advancing_either_cursor(
    db_session: AsyncSession,
) -> None:
    source = await mixed_source(db_session)
    context = runtime(db_session)
    await db_session.commit()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    operation_id = uuid4()
    await receive(db_session, source, operation_id=operation_id)
    await db_session.rollback()
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert all(not rows for rows in (await journal_facts(db_session)).values())
    retry = await receive(db_session, source, operation_id=operation_id)
    assert [line.offset for line in retry.lines] == [Decimal("0.000"), 0]


async def test_refund_only_actor_can_receive_and_replay_historical_sale(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    source = await mixed_source(db_session)
    context = runtime(db_session)
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, context.stock, actor)
    assert (
        await db_session.scalar(
            text(
                "SELECT branch_id FROM user_assignment "
                "WHERE user_id=:actor AND tenant_id=:tenant"
            ),
            {"actor": actor, "tenant": context.stock.tenant_id},
        )
        == context.stock.branch_id
    )
    original = await receive(db_session, source, actor_id=actor)
    assert original.actor_id == actor
    assert (
        await receive(db_session, source, actor_id=actor, operation_id=original.operation_id)
        == original
    )


@pytest.mark.parametrize("replay", [False, True])
async def test_actual_actor_and_refund_permission_are_checked_for_initial_and_replay(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine, replay: bool
) -> None:
    source = await mixed_source(db_session)
    operation_id = uuid4()
    if replay:
        await receive(db_session, source, operation_id=operation_id)
    before = await journal_facts(db_session)
    context = runtime(db_session)
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.sell",)
    )
    await set_receiving_context(db_session, context.stock, actor)
    for claimed_actor in (actor, context.actor_id):
        with pytest.raises((PermissionDeniedError, NotFoundError)):
            await receive(db_session, source, actor_id=claimed_actor, operation_id=operation_id)
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await journal_facts(db_session) == before


async def test_different_authorized_actor_cannot_replay_another_actors_operation(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    source = await mixed_source(db_session)
    result = await receive(db_session, source)
    context = runtime(db_session)
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund",)
    )
    await set_receiving_context(db_session, context.stock, actor)
    with pytest.raises(ConflictError):
        await receive(db_session, source, actor_id=actor, operation_id=result.operation_id)


async def test_revoked_branch_assignment_blocks_initial_and_replay(
    db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    source = await mixed_source(db_session)
    original = await receive(db_session, source)
    context = runtime(db_session)
    await db_session.commit()
    async with db_engine.begin() as support:
        await support.execute(text("SELECT set_config('app.support_session','true',true)"))
        await support.execute(
            text(
                "UPDATE user_assignment SET is_active=false "
                "WHERE user_id=:actor AND tenant_id=:tenant"
            ),
            {"actor": context.actor_id, "tenant": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    for operation_id in (original.operation_id, uuid4()):
        with pytest.raises((PermissionDeniedError, NotFoundError)):
            await receive(db_session, source, operation_id=operation_id)


@pytest.mark.parametrize("status", ["blocked", "invited"])
async def test_inactive_account_cannot_receive_or_replay_quarantine_receipt(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, status: str
) -> None:
    source = await mixed_source(db_session)
    original = await receive(db_session, source)
    context = runtime(db_session)
    before = await journal_facts(db_session)
    await db_session.commit()
    async with maintenance_engine.begin() as owner:
        await owner.execute(
            text("UPDATE app_user SET status=:status WHERE id=:id"),
            {"status": status, "id": context.actor_id},
        )
    try:
        await set_receiving_context(db_session, context.stock, context.actor_id)
        for operation_id in (original.operation_id, uuid4()):
            with pytest.raises((PermissionDeniedError, NotFoundError)):
                await receive(db_session, source, operation_id=operation_id)
        await db_session.rollback()
    finally:
        async with maintenance_engine.begin() as owner:
            await owner.execute(
                text("UPDATE app_user SET status='active' WHERE id=:id"),
                {"id": context.actor_id},
            )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await journal_facts(db_session) == before


async def test_real_foreign_tenant_cannot_read_or_replay_journal(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    source = await mixed_source(db_session)
    original = await receive(db_session, source)
    context = runtime(db_session)
    async with AsyncSession(db_engine) as support:
        async with support.begin():
            await support.execute(text("SELECT set_config('app.support_session','true',true)"))
            foreign = await seed_ledger_context(support)
    actor = await seed_receiving_actor(db_engine, maintenance_engine, foreign)
    await set_receiving_context(db_session, foreign, actor)
    for table in TABLES:
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": context.stock.tenant_id},
            )
            == 0
        )
    for tenant in (foreign.tenant_id, context.stock.tenant_id):
        with pytest.raises((PermissionDeniedError, NotFoundError)):
            await receive(
                db_session,
                source,
                tenant_id=tenant,
                actor_id=actor,
                operation_id=original.operation_id,
            )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await receive(db_session, source, operation_id=original.operation_id) == original


async def test_support_session_cannot_impersonate_a_refund_actor(
    db_session: AsyncSession, db_engine: AsyncEngine
) -> None:
    source = await mixed_source(db_session)
    context = runtime(db_session)
    await db_session.commit()
    async with AsyncSession(db_engine) as support:
        async with support.begin():
            support.info["receiving_runtime"] = context
            await support.execute(
                text(
                    "SELECT set_config('app.support_session','true',true),"
                    "set_config('app.tenant_id',:tenant,true),"
                    "set_config('app.user_id',:actor,true)"
                ),
                {"tenant": str(context.stock.tenant_id), "actor": str(context.actor_id)},
            )
            with pytest.raises(PermissionDeniedError):
                await receive(support, source)


@pytest.mark.parametrize("kind", ["draft", "physical_only"])
async def test_draft_and_physical_only_sources_are_rejected(
    db_session: AsyncSession, kind: str
) -> None:
    source = await checkout_source(db_session)
    physical = await put_physical(db_session, source.sale.id)
    if kind == "draft":
        legacy = await legacy_with_stock(db_session, source.sale)
        selections: tuple[MixedReturnSelection, ...] = (
            LegacyReturnSelection(legacy.id, Decimal("0.001")),
            PhysicalReturnSelection(physical.id, "unit", 1),
        )
    else:
        await complete_physical(db_session, await saved_request(db_session, source.sale))
        selections = (PhysicalReturnSelection(physical.id, "unit", 1),)
    context = runtime(db_session)
    service = MixedReturnJournalService(MixedReturnJournalRepository(db_session))
    with pytest.raises((BusinessRuleError, NotFoundError)):
        await service.receive(
            tenant_id=context.stock.tenant_id,
            actor_id=context.actor_id,
            operation_id=uuid4(),
            parent_sale_id=source.sale.id,
            selections=selections,
        )
    assert all(not rows for rows in (await journal_facts(db_session)).values())


async def concurrent_receive(
    factory: async_sessionmaker[AsyncSession],
    context: ReceivingRuntime,
    source: MixedSource,
    operation_id: UUID,
) -> MixedReturnJournalResult | BusinessRuleError:
    try:
        async with factory.begin() as session:
            session.info["receiving_runtime"] = context
            await set_receiving_context(session, context.stock, context.actor_id)
            return await receive(session, source, operation_id=operation_id)
    except BusinessRuleError as error:
        return error


@pytest.mark.parametrize("same_operation", [False, True])
async def test_concurrent_commands_allocate_the_last_both_kind_quantities_once(
    db_session: AsyncSession, same_operation: bool
) -> None:
    source = await mixed_source(db_session, sale_format="unit", physical_quantity=1)
    context = runtime(db_session)
    await db_session.commit()
    operation_id = uuid4()
    results = await asyncio.wait_for(
        asyncio.gather(
            *(
                concurrent_receive(
                    context.factory,
                    context,
                    source,
                    operation_id if same_operation else uuid4(),
                )
                for _ in range(3)
            )
        ),
        timeout=15,
    )
    winners = [result for result in results if isinstance(result, MixedReturnJournalResult)]
    assert len(winners) == (3 if same_operation else 1)
    assert all(result == winners[0] for result in winners)
    assert sum(isinstance(result, BusinessRuleError) for result in results) == (
        0 if same_operation else 2
    )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    rows = await journal_facts(db_session)
    assert len(rows[TABLES[0]]) == 1
    assert len(rows[TABLES[1]]) == 2
