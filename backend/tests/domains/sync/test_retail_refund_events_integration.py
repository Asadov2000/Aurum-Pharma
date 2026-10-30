"""Committed refunds publish exact history without repeating payout or stock effects."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.domains.pos.retail_refund_events import (
    RetailRefundResult,
    RetailRefundSourceKind,
    retail_refund_event_fingerprint,
    validate_retail_refund_event,
)
from app.domains.sync.schemas import SyncEventEnvelope
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import seed_receiving_actor, set_receiving_context
from tests.domains.pos import test_mixed_cash_refund as mixed_cash
from tests.domains.pos import test_mixed_electronic_refund_posting as mixed_electronic
from tests.domains.pos import test_physical_cash_refund as physical_cash
from tests.domains.pos import test_unit_electronic_refund_posting as physical_electronic
from tests.domains.pos.test_mixed_return_journal import mixed_source
from tests.domains.pos.test_physical_cash_checkout import complete as complete_cash
from tests.domains.pos.test_retail_checkout_event_producer_integration import enable_gate
from tests.domains.pos.test_unit_physical_cart import put_physical
from tests.domains.pos.test_unit_physical_checkout_preparation import checkout_source, saved_request
from tests.domains.pos.test_unit_return import runtime
from tests.domains.sync.test_physical_stock_events_integration import app_context, finish_sources

db_session = receiving_session_fixture
KINDS: tuple[RetailRefundSourceKind, ...] = (
    "physical_cash",
    "mixed_cash",
    "physical_electronic",
    "mixed_electronic",
)


@dataclass(frozen=True)
class RefundCase:
    kind: RetailRefundSourceKind
    parent_sale_id: UUID
    complete: Callable[[], Awaitable[RetailRefundResult]]


async def prepare_refund(
    session: AsyncSession, owner: AsyncEngine, kind: RetailRefundSourceKind
) -> RefundCase:
    await enable_gate(session, owner)
    await app_context(session)
    if kind == "physical_cash":
        source = await checkout_source(session)
        line = await put_physical(session, source.sale.id, sale_format="pack", quantity=1)
        original = await complete_cash(session, await saved_request(session, source.sale))

        async def physical() -> RetailRefundResult:
            return await physical_cash.refund(session, original.sale_id, line.id, original.shift_id)

        case = RefundCase(kind, original.sale_id, physical)
    elif kind == "mixed_cash":
        source_mixed = await mixed_source(session)
        # Freeze one operation identity so recovery is tested against the same command.
        operation = uuid4()

        async def mixed() -> RetailRefundResult:
            return await mixed_cash.refund(session, source_mixed, operation_id=operation)

        case = RefundCase(kind, source_mixed.original.sale_id, mixed)
    elif kind == "physical_electronic":
        physical_request, physical_shift = await physical_electronic.reconciled(session)

        async def electronic() -> RetailRefundResult:
            return await physical_electronic.post(session, physical_request, physical_shift)

        case = RefundCase(kind, physical_request.request.parent_sale_id, electronic)
    else:
        mixed_request, mixed_shift = await mixed_electronic.reconciled(session)

        async def combined() -> RetailRefundResult:
            return await mixed_electronic.post(session, mixed_request, mixed_shift)

        case = RefundCase(kind, mixed_request.request.parent_sale_id, combined)
    await session.commit()
    await app_context(session)
    return case


async def enable_refund_gate(session: AsyncSession, owner: AsyncEngine) -> None:
    context = runtime(session)
    async with owner.begin() as connection:
        assert str(await connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert await connection.scalar(text("SELECT current_user")) == "aurum_schema_owner"
        await connection.execute(
            text(
                "INSERT INTO sync_retail_refund_producer_gate(tenant_id,id,enabled,created_by) "
                "VALUES(:tenant,:branch,true,:actor)"
            ),
            {
                "tenant": context.stock.tenant_id,
                "branch": context.stock.branch_id,
                "actor": context.actor_id,
            },
        )


async def event_rows(session: AsyncSession) -> list[dict[str, object]]:
    rows = await session.execute(
        text(
            "SELECT envelope,previous_source_checksum,previous_projection_checksum,"
            "event_fingerprint FROM sync_retail_refund_event "
            "WHERE tenant_id=:tenant ORDER BY sequence"
        ).columns(envelope=JSONB()),
        {"tenant": runtime(session).stock.tenant_id},
    )
    return [dict(row) for row in rows.mappings()]


async def refund_facts(session: AsyncSession) -> dict[str, list[object]]:
    result = await mixed_cash.facts(session)
    tenant = runtime(session).stock.tenant_id
    for table in (
        *physical_cash.REFUND_TABLES,
        "audit_log",
        "sync_stream",
        "sync_outbox",
        "sync_retail_refund_source",
        "sync_retail_refund_event",
        "pos_unit_electronic_refund_posting",
        "pos_mixed_electronic_refund_posting",
    ):
        result[table] = list(
            (
                await session.scalars(
                    text(
                        f"SELECT to_jsonb(row) FROM {table} row WHERE tenant_id=:tenant "
                        "ORDER BY to_jsonb(row)::text"
                    ),
                    {"tenant": tenant},
                )
            ).all()
        )
    return result


async def recover_refund(
    session: AsyncSession, case: RefundCase, result: RetailRefundResult
) -> RetailRefundResult:
    if case.kind == "physical_cash":
        line = result.receipt.lines[0]
        return await physical_cash.refund(
            session,
            result.parent_sale_id,
            line.original_line_id,
            result.shift_id,
            operation_id=result.operation_id,
        )
    return await case.complete()


@pytest.mark.parametrize("kind", KINDS)
async def test_each_real_refund_is_atomic_canonical_and_replayed_without_another_effect(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, kind: RetailRefundSourceKind
) -> None:
    case = await prepare_refund(db_session, maintenance_engine, kind)
    await enable_refund_gate(db_session, maintenance_engine)
    result = await case.complete()
    await finish_sources(db_session)
    rows = await event_rows(db_session)
    assert len(rows) == 1
    row = rows[0]
    event = SyncEventEnvelope.model_validate(row["envelope"])
    payload = validate_retail_refund_event(
        event,
        previous_source_checksum=str(row["previous_source_checksum"]),
        previous_projection_checksum=str(row["previous_projection_checksum"]),
    )
    assert payload.source_kind == kind and payload.result == result
    assert event.event_id == event.operation_id == result.operation_id
    assert event.aggregate_id == result.sale_id
    assert event.event_type == "pos.sale.refunded.v6" and event.schema_version == 6
    assert payload.result.parent_sale_id == case.parent_sale_id
    assert retail_refund_event_fingerprint(event) == row["event_fingerprint"]
    before = await refund_facts(db_session)
    replay = await recover_refund(db_session, case, result)
    assert replay == result
    await finish_sources(db_session)
    assert await refund_facts(db_session) == before
    assert await event_rows(db_session) == rows


@pytest.mark.parametrize("kind", KINDS)
async def test_disabled_gate_never_adds_a_refund_event_or_retroactive_capture(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, kind: RetailRefundSourceKind
) -> None:
    case = await prepare_refund(db_session, maintenance_engine, kind)
    result = await case.complete()
    await finish_sources(db_session)
    assert await event_rows(db_session) == []
    await db_session.commit()
    await enable_refund_gate(db_session, maintenance_engine)
    await app_context(db_session)
    assert await recover_refund(db_session, case, result) == result
    await finish_sources(db_session)
    assert await event_rows(db_session) == []


@pytest.mark.parametrize("kind", KINDS)
async def test_outer_rollback_removes_refund_source_event_outbox_and_financial_effects(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, kind: RetailRefundSourceKind
) -> None:
    case = await prepare_refund(db_session, maintenance_engine, kind)
    await enable_refund_gate(db_session, maintenance_engine)
    before = await refund_facts(db_session)
    async with db_session.begin_nested() as transaction:
        await case.complete()
        await finish_sources(db_session)
        assert len(await event_rows(db_session)) == 1
        await transaction.rollback()
    assert await refund_facts(db_session) == before
    assert await event_rows(db_session) == []


async def test_refund_permission_alone_cannot_read_historical_cost_event_or_another_tenant(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
) -> None:
    case = await prepare_refund(db_session, maintenance_engine, "physical_cash")
    await enable_refund_gate(db_session, maintenance_engine)
    await case.complete()
    await finish_sources(db_session)
    assert len(await event_rows(db_session)) == 1
    await db_session.commit()
    context = runtime(db_session)
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, context.stock, permissions=("pos.refund", "batches.view")
    )
    await set_receiving_context(db_session, context.stock, actor)
    assert await event_rows(db_session) == []
    assert await db_session.scalar(text("SELECT count(*) FROM sync_retail_refund_source")) == 1
    await db_session.execute(
        text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(uuid4())}
    )
    assert await db_session.scalar(text("SELECT count(*) FROM sync_retail_refund_source")) == 0
    assert await db_session.scalar(text("SELECT count(*) FROM sync_retail_refund_event")) == 0


@pytest.mark.parametrize(
    "table",
    [
        "sync_retail_refund_producer_gate",
        "sync_retail_refund_source",
        "sync_retail_refund_event",
        "sync_retail_refund_anchor",
        "sync_retail_refund_projection",
    ],
)
async def test_runtime_cannot_mutate_private_refund_history(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    table: str,
) -> None:
    case = await prepare_refund(db_session, maintenance_engine, "physical_cash")
    await enable_refund_gate(db_session, maintenance_engine)
    await case.complete()
    await finish_sources(db_session)
    before = await refund_facts(db_session)
    with pytest.raises(DBAPIError) as denied:
        async with db_session.begin_nested():
            await db_session.execute(
                text(f"DELETE FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": runtime(db_session).stock.tenant_id},
            )
    assert getattr(denied.value.orig, "sqlstate", None) in {"42501", "55000"}
    assert await refund_facts(db_session) == before
