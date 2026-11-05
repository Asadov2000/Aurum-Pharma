"""Internal transition planning requires actual accepted history and current access."""

from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.incoming.repository import IncomingRepository
from app.domains.incoming.service import IncomingService
from app.domains.inventory.repository import InventoryRepository
from app.domains.inventory.unit_legacy_reconciliation_record_repository import (
    LegacyReconciliationRecordRepository,
)
from app.domains.inventory.unit_legacy_reconciliation_record_service import (
    LegacyReconciliationRecordService,
)
from app.domains.inventory.unit_legacy_reconciliation_schemas import LegacyReconciliationSaveRequest
from app.domains.inventory.unit_legacy_transition_repository import (
    LegacyTransitionSourceRepository,
)
from app.domains.inventory.unit_legacy_transition_service import (
    LegacyTransitionSourcePlan,
    LegacyTransitionSourceService,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.incoming.test_unit_acceptance import (
    seed_prepared_incoming,
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.inventory.test_unit_legacy_reconciliation_api import facts, legacy_batch
from tests.domains.inventory.test_unit_reconciliation_records_api import (
    increase_quantity,
    request_body,
)
from tests.domains.pos.test_unit_return import runtime

db_session = receiving_session_fixture
PERMISSIONS = ("batches.view", "batches.view_costs", "batches.update", "incoming.view")


async def accepted_batch(session: AsyncSession, cost: Decimal = Decimal("10.00")) -> UUID:
    incoming = await seed_prepared_incoming(session, cost=cost)
    service = IncomingService(IncomingRepository(session))
    await service.update_item(
        incoming.document_id,
        incoming.item_ids[0],
        fields={"prepare_unit_sale": False},
        allowed_branch_ids={incoming.stock.branch_id},
    )
    document = await service.accept(
        incoming.document_id,
        actor_id=incoming.actor_id,
        allowed_branch_ids={incoming.stock.branch_id},
    )
    assert document.status == "accepted"
    item = await IncomingRepository(session).get_item(incoming.item_ids[0])
    assert item is not None and item.created_batch_id is not None
    return item.created_batch_id


async def record_count(
    session: AsyncSession,
    batch_id: UUID,
    quantity: str = "2.000",
    closed: int = 2,
    opened_packs: list[dict[str, object]] | None = None,
) -> UUID:
    context = runtime(session)
    request = LegacyReconciliationSaveRequest.model_validate(
        {
            **request_body(session),
            "expected_legacy_quantity": quantity,
            "closed_packs": closed,
            "opened_packs": opened_packs or [],
        }
    )
    saved = await LegacyReconciliationRecordService(
        LegacyReconciliationRecordRepository(session)
    ).save(
        tenant_id=context.stock.tenant_id,
        actor_id=context.actor_id,
        batch_id=batch_id,
        request=request,
        view_branch_ids={context.stock.branch_id},
        update_branch_ids={context.stock.branch_id},
    )
    return saved.id


async def build(
    session: AsyncSession, batch_id: UUID, record_id: UUID
) -> LegacyTransitionSourcePlan:
    context = runtime(session)
    return await LegacyTransitionSourceService(LegacyTransitionSourceRepository(session)).build(
        tenant_id=context.stock.tenant_id,
        branch_id=context.stock.branch_id,
        batch_id=batch_id,
        reconciliation_id=record_id,
        actor_id=context.actor_id,
    )


async def all_facts(session: AsyncSession) -> dict[str, list[object]]:
    snapshot = await facts(session)
    for table in ("incoming_document", "incoming_item", "unit_legacy_reconciliation_record"):
        snapshot[table] = list(
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
    return snapshot


@pytest.mark.parametrize("cost", [Decimal("0.00"), Decimal("0.01"), Decimal("10.00")])
async def test_accepted_history_plans_exact_value_without_writes(
    db_session: AsyncSession, cost: Decimal
) -> None:
    batch_id = await accepted_batch(db_session, cost)
    record_id = await record_count(db_session, batch_id)
    before = await all_facts(db_session)
    result = await build(db_session, batch_id, record_id)
    assert result.plan.historical_pack_cost == cost
    assert result.plan.total_cost == cost * 2
    assert result.plan.total_base_units == 60
    assert result.plan.legacy_quantity_delta == Decimal("-2.000")
    assert result.plan.reconciliation_id == record_id
    provenance = (
        await db_session.execute(
            text(
                "SELECT item.document_id,item.id,movement.id FROM incoming_item item "
                "JOIN batch_movement movement ON movement.source_id=item.id "
                "AND movement.source_table='incoming_item' AND movement.movement_type='incoming' "
                "WHERE item.created_batch_id=:batch"
            ),
            {"batch": batch_id},
        )
    ).one()
    assert tuple(provenance) == (
        result.incoming_document_id,
        result.incoming_item_id,
        result.incoming_movement_id,
    )
    assert await build(db_session, batch_id, record_id) == result
    assert await all_facts(db_session) == before


async def test_fresh_count_cannot_attribute_excess_stock_to_original_incoming(
    db_session: AsyncSession,
) -> None:
    batch_id = await accepted_batch(db_session)
    await InventoryRepository(db_session).insert_movement(
        tenant_id=runtime(db_session).stock.tenant_id,
        batch_id=batch_id,
        movement_type="correction",
        qty_delta=Decimal("1.000"),
    )
    record_id = await record_count(db_session, batch_id, quantity="3.000", closed=3)
    before = await all_facts(db_session)
    with pytest.raises(ConflictError):
        await build(db_session, batch_id, record_id)
    assert await all_facts(db_session) == before


async def test_readonly_tenant_can_inspect_source_plan_without_writes(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    context = runtime(db_session)
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    expected = await build(db_session, batch_id, record_id)
    await db_session.commit()
    async with maintenance_engine.begin() as owner:
        await owner.execute(
            text("UPDATE tenant SET status='readonly' WHERE id=:tenant"),
            {"tenant": context.stock.tenant_id},
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    before = await all_facts(db_session)
    assert await build(db_session, batch_id, record_id) == expected
    assert await all_facts(db_session) == before


async def test_server_derived_subcent_value_is_rejected_without_rounding(
    db_session: AsyncSession,
) -> None:
    batch_id = await accepted_batch(db_session, Decimal("7.31"))
    await InventoryRepository(db_session).insert_movement(
        tenant_id=runtime(db_session).stock.tenant_id,
        batch_id=batch_id,
        movement_type="correction",
        qty_delta=Decimal("-1.500"),
    )
    record_id = await record_count(
        db_session,
        batch_id,
        quantity="0.500",
        closed=0,
        opened_packs=[
            {"id": str(uuid4()), "opened_at": "2026-10-15T12:00:00Z", "loose_units": 15},
        ],
    )
    before = await all_facts(db_session)
    with pytest.raises(BusinessRuleError, match="cannot preserve exact stock and cost"):
        await build(db_session, batch_id, record_id)
    assert await all_facts(db_session) == before


@pytest.mark.parametrize("change", ["quantity", "price"])
async def test_changed_current_stock_or_purchase_price_cannot_reuse_saved_count(
    db_session: AsyncSession, change: str
) -> None:
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    if change == "quantity":
        await increase_quantity(db_session, batch_id)
    else:
        await db_session.execute(
            text("UPDATE batch SET purchase_price=11.00 WHERE id=:id"), {"id": batch_id}
        )
    before = await all_facts(db_session)
    with pytest.raises(ConflictError):
        await build(db_session, batch_id, record_id)
    assert await all_facts(db_session) == before


async def test_batch_without_accepted_incoming_history_is_not_a_trusted_source(
    db_session: AsyncSession,
) -> None:
    batch = await legacy_batch(db_session, quantity="1.000")
    record_id = await record_count(db_session, batch.id, quantity="1.000", closed=1)
    before = await all_facts(db_session)
    with pytest.raises(ConflictError):
        await build(db_session, batch.id, record_id)
    assert await all_facts(db_session) == before


@pytest.mark.parametrize("identity", ["record", "batch"])
async def test_saved_record_must_match_requested_identity(
    db_session: AsyncSession, identity: str
) -> None:
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    before = await all_facts(db_session)
    with pytest.raises(NotFoundError):
        await build(
            db_session,
            uuid4() if identity == "batch" else batch_id,
            uuid4() if identity == "record" else record_id,
        )
    assert await all_facts(db_session) == before


@pytest.mark.parametrize("missing", PERMISSIONS)
async def test_each_current_database_permission_is_required(
    db_session: AsyncSession,
    db_engine: AsyncEngine,
    maintenance_engine: AsyncEngine,
    missing: str,
) -> None:
    context = runtime(db_session)
    viewer = await seed_receiving_actor(
        db_engine,
        maintenance_engine,
        context.stock,
        permissions=tuple(permission for permission in PERMISSIONS if permission != missing),
    )
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    before = await all_facts(db_session)
    await set_receiving_context(db_session, context.stock, viewer)
    with pytest.raises(PermissionDeniedError):
        await LegacyTransitionSourceService(LegacyTransitionSourceRepository(db_session)).build(
            tenant_id=context.stock.tenant_id,
            branch_id=context.stock.branch_id,
            batch_id=batch_id,
            reconciliation_id=record_id,
            actor_id=viewer,
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await all_facts(db_session) == before


@pytest.mark.parametrize("identity", ["tenant", "branch", "actor", "db_tenant"])
async def test_scope_and_database_identity_cannot_be_forged(
    db_session: AsyncSession, identity: str
) -> None:
    context = runtime(db_session)
    batch_id = await accepted_batch(db_session)
    record_id = await record_count(db_session, batch_id)
    before = await all_facts(db_session)
    if identity == "db_tenant":
        await db_session.execute(
            text("SELECT set_config('app.tenant_id',:tenant,true)"), {"tenant": str(uuid4())}
        )
    with pytest.raises(PermissionDeniedError):
        await LegacyTransitionSourceService(LegacyTransitionSourceRepository(db_session)).build(
            tenant_id=uuid4() if identity == "tenant" else context.stock.tenant_id,
            branch_id=uuid4() if identity == "branch" else context.stock.branch_id,
            batch_id=batch_id,
            reconciliation_id=record_id,
            actor_id=uuid4() if identity == "actor" else context.actor_id,
        )
    await set_receiving_context(db_session, context.stock, context.actor_id)
    assert await all_facts(db_session) == before
