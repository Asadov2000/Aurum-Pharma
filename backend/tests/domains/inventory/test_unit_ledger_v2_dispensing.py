"""Persistent physical dispensing preserves pack identities and atomic cart stock."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, ConflictError, PermissionDeniedError
from app.domains.inventory.unit_allocation_v2 import UnitCartLineV2
from app.domains.inventory.unit_dispensing_v2_repository import UnitDispensingV2Repository
from app.domains.inventory.unit_dispensing_v2_service import (
    PhysicalUnitLedgerV2Service,
    UnitCartDispenseResultV2,
)
from app.domains.inventory.unit_stock_v2 import SaleFormatV2, UnitPackagingV2
from tests.domains.incoming.test_unit_acceptance import (
    IncomingContext,
    ReceivingRuntime,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import (
    db_session as receiving_session_fixture,
)
from tests.domains.incoming.test_unit_acceptance_v2 import accept_v2, seed_v2_incoming

db_session = receiving_session_fixture
DISPENSING_TABLES = (
    "unit_stock_cart_command_v2",
    "unit_stock_cart_line_v2",
    "unit_stock_opened_pack_v2",
    "unit_stock_movement_v2",
)


@dataclass(frozen=True)
class PhysicalSource:
    incoming: IncomingContext
    lot_id: UUID
    packaging: UnitPackagingV2


async def receive_source(
    session: AsyncSession, *, blister: int | None = 10, step: int = 1, expires_in: int = 180
) -> PhysicalSource:
    runtime = cast(ReceivingRuntime, session.info["receiving_runtime"])
    await session.execute(
        text(
            "UPDATE tenant_catalog SET unit_sale_preparation="
            "jsonb_set(unit_sale_preparation,'{dispense_step}',CAST(:step AS jsonb)) WHERE id=:id"
        ),
        {"id": runtime.stock.catalog_id, "step": str(step)},
    )
    incoming = await seed_v2_incoming(session, blister)
    await session.execute(
        text("UPDATE incoming_item SET expires_at=:expiry WHERE id=:id"),
        {"expiry": date.today() + timedelta(days=expires_in), "id": incoming.item_ids[0]},
    )
    await accept_v2(session, incoming)
    row = (
        await session.execute(
            text("SELECT id,packaging FROM unit_stock_lot_v2 WHERE source_item_id=:id"),
            {"id": incoming.item_ids[0]},
        )
    ).one()
    return PhysicalSource(incoming, row.id, UnitPackagingV2.model_validate(row.packaging))


def cart_line(source: PhysicalSource, sale_format: SaleFormatV2, quantity: int) -> UnitCartLineV2:
    return UnitCartLineV2(
        id=uuid4(),
        catalog_id=source.incoming.stock.catalog_id,
        packaging=source.packaging,
        sale_format=sale_format,
        quantity=quantity,
    )


async def dispense(
    session: AsyncSession,
    source: PhysicalSource,
    lines: tuple[UnitCartLineV2, ...],
    *,
    command_id: UUID | None = None,
) -> UnitCartDispenseResultV2:
    context = source.incoming
    return await PhysicalUnitLedgerV2Service(UnitDispensingV2Repository(session)).dispense_cart(
        tenant_id=context.stock.tenant_id,
        branch_id=context.stock.branch_id,
        actor_id=context.actor_id,
        allowed_branch_ids={context.stock.branch_id},
        command_id=command_id or uuid4(),
        lines=lines,
    )


async def remaining(session: AsyncSession, source: PhysicalSource) -> int:
    value = await session.scalar(
        text(
            "SELECT (lot.closed_packs*(lot.packaging->>'units_per_pack')::integer "
            "+coalesce((SELECT sum(remaining_units) FROM unit_stock_opened_pack_v2 "
            "WHERE tenant_id=lot.tenant_id AND lot_id=lot.id),0))::bigint "
            "FROM unit_stock_lot_v2 lot WHERE lot.id=:id"
        ),
        {"id": source.lot_id},
    )
    assert isinstance(value, int)
    return value


async def packs(
    session: AsyncSession, source: PhysicalSource
) -> list[tuple[UUID, dict[str, object], int]]:
    records = (
        await session.execute(
            text(
                "SELECT id,state,remaining_units FROM unit_stock_opened_pack_v2 "
                "WHERE lot_id=:id ORDER BY pack_ordinal"
            ),
            {"id": source.lot_id},
        )
    ).all()
    return [(record.id, record.state, record.remaining_units) for record in records]


async def test_mixed_cart_reserves_real_packs_blisters_and_units_in_one_atomic_command(
    db_session: AsyncSession,
) -> None:
    source = await receive_source(db_session)
    lines = tuple(
        cart_line(source, kind, quantity)
        for kind, quantity in (
            ("unit", 5),
            ("blister", 1),
            ("pack", 1),
        )
    )
    result = await dispense(db_session, source, lines)
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert tuple(line.line_id for line in result.lines) == tuple(line.id for line in lines)
    assert tuple(line.base_units for line in result.lines) == (5, 10, 30)
    assert await remaining(db_session, source) == 15
    opened = await packs(db_session, source)
    assert len(opened) == 1 and opened[0][2] == 15
    assert len(opened[0][1]["intact_blisters"]) == 1
    assert opened[0][1]["loose_units"] == 0
    assert {movement.pack_id for movement in result.movements if movement.pack_id is not None} == {
        opened[0][0]
    }
    assert {movement.kind for movement in result.movements} == {
        "pack_take",
        "pack_open",
        "blister_take",
        "unit_take",
    }
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM batch WHERE tenant_id=:tenant"),
            {"tenant": source.incoming.stock.tenant_id},
        )
        == 0
    )


async def test_later_command_reuses_the_real_opened_pack_and_preserves_zero_tombstone(
    db_session: AsyncSession,
) -> None:
    source = await receive_source(db_session)
    await dispense(db_session, source, (cart_line(source, "unit", 3),))
    first_pack = (await packs(db_session, source))[0][0]
    second = await dispense(db_session, source, (cart_line(source, "unit", 4),))
    opened = await packs(db_session, source)
    assert len(opened) == 1 and opened[0][0] == first_pack and opened[0][2] == 23
    assert {movement.pack_id for movement in second.movements} == {first_pack}
    await dispense(db_session, source, (cart_line(source, "unit", 23),))
    opened = await packs(db_session, source)
    assert opened == [
        (
            first_pack,
            {
                "intact_blisters": [],
                "opened_blisters": [],
                "loose_units": 0,
            },
            0,
        )
    ]
    assert await remaining(db_session, source) == 30


async def test_fragmented_tablets_never_become_an_intact_blister(
    db_session: AsyncSession,
) -> None:
    source = await receive_source(db_session)
    await dispense(
        db_session, source, (cart_line(source, "pack", 1), cart_line(source, "unit", 25))
    )
    before = await packs(db_session, source)
    with pytest.raises(BusinessRuleError):
        await dispense(db_session, source, (cart_line(source, "blister", 1),))
    assert await packs(db_session, source) == before
    assert await remaining(db_session, source) == 5


async def test_unknown_blister_composition_keeps_loose_units_and_rejects_blister_sale(
    db_session: AsyncSession,
) -> None:
    source = await receive_source(db_session, blister=None)
    await dispense(db_session, source, (cart_line(source, "unit", 7),))
    opened = await packs(db_session, source)
    assert opened[0][1] == {"intact_blisters": [], "opened_blisters": [], "loose_units": 23}
    with pytest.raises(BusinessRuleError):
        await dispense(db_session, source, (cart_line(source, "blister", 1),))
    assert await remaining(db_session, source) == 53


async def test_large_dispense_step_can_cross_blisters_within_one_physical_pack(
    db_session: AsyncSession,
) -> None:
    source = await receive_source(db_session, step=15)
    await dispense(db_session, source, (cart_line(source, "unit", 15),))
    opened = await packs(db_session, source)
    assert opened[0][2] == 15
    assert len(opened[0][1]["intact_blisters"]) == 1
    assert opened[0][1]["opened_blisters"] == [{"number": 2, "remaining_units": 5}]
    with pytest.raises(BusinessRuleError):
        await dispense(db_session, source, (cart_line(source, "unit", 1),))
    assert await remaining(db_session, source) == 45


async def test_shortage_on_later_line_rolls_back_every_cart_component(
    db_session: AsyncSession,
) -> None:
    source = await receive_source(db_session)
    with pytest.raises(BusinessRuleError):
        await dispense(
            db_session,
            source,
            (
                cart_line(source, "blister", 1),
                cart_line(source, "unit", 51),
            ),
        )
    assert await remaining(db_session, source) == 60
    for table in DISPENSING_TABLES:
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": source.incoming.stock.tenant_id},
            )
            == 0
        )


async def test_small_fragments_from_different_physical_packs_cannot_bypass_dispense_step(
    db_session: AsyncSession,
) -> None:
    source = await receive_source(db_session, step=6)
    await dispense(db_session, source, (cart_line(source, "unit", 6),))
    await dispense(db_session, source, (cart_line(source, "blister", 3),))
    await dispense(db_session, source, (cart_line(source, "unit", 18),))
    before = await packs(db_session, source)
    assert sorted(pack[2] for pack in before) == [2, 4]
    with pytest.raises(BusinessRuleError):
        await dispense(db_session, source, (cart_line(source, "unit", 6),))
    assert await packs(db_session, source) == before
    assert await remaining(db_session, source) == 6


async def test_command_replay_keeps_real_movement_ids_and_rejects_changed_payload(
    db_session: AsyncSession,
) -> None:
    source = await receive_source(db_session)
    command_id = uuid4()
    lines = (cart_line(source, "unit", 4),)
    first = await dispense(db_session, source, lines, command_id=command_id)
    await db_session.execute(
        text("UPDATE tenant_catalog SET unit_sale_preparation=NULL WHERE id=:id"),
        {"id": source.incoming.stock.catalog_id},
    )
    repeated = await dispense(db_session, source, lines, command_id=command_id)
    assert repeated == first
    assert await remaining(db_session, source) == 56
    with pytest.raises(ConflictError):
        await dispense(
            db_session,
            source,
            (lines[0].model_copy(update={"quantity": 5}),),
            command_id=command_id,
        )


async def test_exact_replay_returns_historical_result_after_exhaustion_and_catalog_deactivation(
    db_session: AsyncSession,
) -> None:
    source = await receive_source(db_session)
    lines = (cart_line(source, "pack", 2),)
    command_id = uuid4()
    first = await dispense(db_session, source, lines, command_id=command_id)
    assert await remaining(db_session, source) == 0
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET is_active=false,unit_sale_preparation=NULL,"
            "base_price=99 WHERE id=:id"
        ),
        {"id": source.incoming.stock.catalog_id},
    )
    repeated = await dispense(db_session, source, lines, command_id=command_id)
    assert repeated == first
    assert await remaining(db_session, source) == 0
    assert await db_session.scalar(
        text("SELECT count(*) FROM unit_stock_movement_v2 WHERE command_id=:id"),
        {"id": command_id},
    ) == len(first.movements)


async def test_outer_rollback_restores_stock_and_allows_command_retry(
    db_session: AsyncSession,
) -> None:
    source = await receive_source(db_session)
    lines = (cart_line(source, "unit", 7),)
    command_id = uuid4()
    savepoint = await db_session.begin_nested()
    await dispense(db_session, source, lines, command_id=command_id)
    await savepoint.rollback()
    assert await remaining(db_session, source) == 60
    assert await packs(db_session, source) == []
    await dispense(db_session, source, lines, command_id=command_id)
    assert await remaining(db_session, source) == 53


@pytest.mark.parametrize("count", [0, 201])
async def test_cart_size_limit_rejects_invalid_requests_without_claiming_stock(
    db_session: AsyncSession,
    count: int,
) -> None:
    source = await receive_source(db_session)
    lines = tuple(cart_line(source, "unit", 1) for _ in range(count))
    with pytest.raises(BusinessRuleError):
        await dispense(db_session, source, lines)
    assert await remaining(db_session, source) == 60
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM unit_stock_cart_command_v2 WHERE tenant_id=:tenant"),
            {"tenant": source.incoming.stock.tenant_id},
        )
        == 0
    )


async def test_allowed_branch_and_actor_are_rechecked_on_successful_command_replay(
    db_session: AsyncSession,
) -> None:
    source = await receive_source(db_session)
    lines = (cart_line(source, "unit", 1),)
    result = await dispense(db_session, source, lines)
    service = PhysicalUnitLedgerV2Service(UnitDispensingV2Repository(db_session))
    for actor, scope in (
        (uuid4(), {source.incoming.stock.branch_id}),
        (source.incoming.actor_id, set()),
    ):
        with pytest.raises(PermissionDeniedError):
            await service.dispense_cart(
                tenant_id=source.incoming.stock.tenant_id,
                branch_id=source.incoming.stock.branch_id,
                command_id=result.command_id,
                actor_id=actor,
                allowed_branch_ids=scope,
                lines=lines,
            )
    assert await remaining(db_session, source) == 59


async def test_earliest_expiring_lot_is_used_before_a_later_opened_pack(
    db_session: AsyncSession,
) -> None:
    later = await receive_source(db_session, expires_in=180)
    await dispense(db_session, later, (cart_line(later, "unit", 3),))
    earlier = await receive_source(db_session, expires_in=30)
    result = await dispense(db_session, earlier, (cart_line(earlier, "unit", 4),))
    assert {movement.lot_id for movement in result.movements} == {earlier.lot_id}
    assert await remaining(db_session, later) == 57
    assert await remaining(db_session, earlier) == 56


async def test_current_catalog_composition_does_not_reinterpret_another_historical_lot(
    db_session: AsyncSession,
) -> None:
    original = await receive_source(db_session)
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET unit_sale_preparation="
            "jsonb_set(unit_sale_preparation,'{units_per_pack}','60') WHERE id=:id"
        ),
        {"id": original.incoming.stock.catalog_id},
    )
    changed = await receive_source(db_session)
    assert changed.packaging.units_per_pack == 60
    with pytest.raises(BusinessRuleError):
        await dispense(db_session, original, (cart_line(original, "pack", 3),))
    assert await remaining(db_session, original) == 60
    assert await remaining(db_session, changed) == 120


@pytest.mark.parametrize("repeat_command", [False, True])
async def test_concurrent_commands_never_double_spend_the_last_stock(
    db_session: AsyncSession,
    repeat_command: bool,
) -> None:
    source = await receive_source(db_session)
    runtime = cast(ReceivingRuntime, db_session.info["receiving_runtime"])
    lines = (cart_line(source, "pack", 2),)
    shared_id = uuid4()
    await db_session.commit()

    async def run() -> UnitCartDispenseResultV2 | None:
        async with runtime.factory.begin() as session:
            await set_receiving_context(session, source.incoming.stock, source.incoming.actor_id)
            try:
                return await dispense(
                    session, source, lines, command_id=shared_id if repeat_command else uuid4()
                )
            except BusinessRuleError:
                return None

    results = await asyncio.wait_for(asyncio.gather(*(run() for _ in range(4))), timeout=15)
    succeeded = [result for result in results if result is not None]
    assert len(succeeded) == (4 if repeat_command else 1)
    assert len({result.command_id for result in succeeded}) == 1
    await set_receiving_context(db_session, source.incoming.stock, source.incoming.actor_id)
    assert await remaining(db_session, source) == 0
    assert (
        await db_session.scalar(
            text("SELECT count(*) FROM unit_stock_cart_command_v2 WHERE tenant_id=:tenant"),
            {"tenant": source.incoming.stock.tenant_id},
        )
        == 1
    )
