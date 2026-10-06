"""Physical v2 receiving is atomic, scoped and bound to a saved incoming source."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import BusinessRuleError, ConflictError, PermissionDeniedError
from app.domains.incoming.models import IncomingDocument
from app.domains.incoming.repository import IncomingRepository
from app.domains.incoming.service import IncomingService
from app.domains.inventory.unit_ledger_v2_models import (
    UnitStockEntryV2,
    UnitStockLotV2,
    UnitStockOperationV2,
)
from app.domains.inventory.unit_ledger_v2_repository import UnitLedgerV2Repository
from app.domains.inventory.unit_ledger_v2_service import PreparedUnitLedgerV2Service
from tests.domains.incoming.test_unit_acceptance import (
    IncomingContext,
    ReceivingRuntime,
    accept_prepared,
    seed_prepared_incoming,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import (
    db_session as receiving_session_fixture,
)
from tests.domains.inventory.test_unit_ledger import PACKAGING

db_session = receiving_session_fixture
TABLES_V2 = ("unit_stock_operation_v2", "unit_stock_lot_v2", "unit_stock_entry_v2")


async def accept_v2(session: AsyncSession, incoming: IncomingContext) -> IncomingDocument:
    return await IncomingService(IncomingRepository(session)).accept_prepared_v2(
        incoming.document_id,
        actor_id=incoming.actor_id,
        allowed_branch_ids={incoming.stock.branch_id},
    )


async def seed_v2_incoming(session: AsyncSession, blister: int | None = 10) -> IncomingContext:
    runtime = cast(ReceivingRuntime, session.info["receiving_runtime"])
    composition = (
        "unit_sale_preparation - 'units_per_blister'"
        if blister is None
        else "jsonb_set(unit_sale_preparation,'{units_per_blister}',CAST(:blister AS jsonb))"
    )
    await session.execute(
        text(f"UPDATE tenant_catalog SET unit_sale_preparation={composition} WHERE id=:id"),
        {"id": runtime.stock.catalog_id, "blister": str(blister)},
    )
    return await seed_prepared_incoming(session)


async def assert_no_v2_stock(session: AsyncSession, incoming: IncomingContext) -> None:
    for table in TABLES_V2:
        assert (
            await session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": incoming.stock.tenant_id},
            )
            == 0
        )
    assert (
        await session.scalar(
            text("SELECT status FROM incoming_document WHERE id=:id"),
            {"id": incoming.document_id},
        )
        == "draft"
    )
    assert (
        await session.scalar(
            text("SELECT created_unit_lot_v2_id FROM incoming_item WHERE id=:id"),
            {"id": incoming.item_ids[0]},
        )
        is None
    )


@pytest.mark.parametrize("blister", [None, 10])
async def test_v2_receiving_initializes_only_real_closed_packs_from_historical_source(
    db_session: AsyncSession, blister: int | None
) -> None:
    incoming = await seed_v2_incoming(db_session, blister)
    await db_session.execute(
        text("UPDATE tenant_catalog SET unit_sale_preparation=NULL,base_price=99 WHERE id=:id"),
        {"id": incoming.stock.catalog_id},
    )
    await db_session.execute(text("SET LOCAL DateStyle='SQL, DMY'"))
    document = await accept_v2(db_session, incoming)
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    assert document.status == "accepted"
    source = (
        await db_session.execute(
            text(
                "SELECT created_batch_id,created_unit_lot_id,created_unit_lot_v2_id,"
                "unit_packaging_snapshot_v2 FROM incoming_item WHERE id=:id"
            ),
            {"id": incoming.item_ids[0]},
        )
    ).one()
    assert source.created_batch_id is None and source.created_unit_lot_id is None
    assert isinstance(source.created_unit_lot_v2_id, UUID)
    snapshot = {**PACKAGING.model_dump(), "version": 2, "units_per_blister": blister}
    lot = (
        await db_session.execute(
            text("SELECT * FROM unit_stock_lot_v2 WHERE id=:id"),
            {"id": source.created_unit_lot_v2_id},
        )
    ).one()
    assert lot.packaging == source.unit_packaging_snapshot_v2 == snapshot
    assert lot.source_item_id == incoming.item_ids[0]
    assert lot.closed_packs == lot.initial_closed_packs == 2
    assert lot.purchase_cost_per_pack == Decimal("10.00")
    assert (lot.tenant_id, lot.branch_id, lot.catalog_id) == (
        incoming.stock.tenant_id,
        incoming.stock.branch_id,
        incoming.stock.catalog_id,
    )
    for table in TABLES_V2:
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": incoming.stock.tenant_id},
            )
            == 1
        )
    entry = (
        await db_session.execute(
            text("SELECT closed_delta,base_units FROM unit_stock_entry_v2 WHERE lot_id=:id"),
            {"id": lot.id},
        )
    ).one()
    assert (entry.closed_delta, entry.base_units) == (2, 60)
    for table in (
        "batch",
        "batch_movement",
        "unit_stock_lot",
        "unit_stock_operation",
        "unit_stock_entry",
    ):
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": incoming.stock.tenant_id},
            )
            == 0
        )
    accepted_at = document.accepted_at
    assert (await accept_v2(db_session, incoming)).accepted_at == accepted_at
    assert (
        await db_session.scalar(
            text("SELECT created_unit_lot_v2_id FROM incoming_item WHERE id=:id"),
            {"id": incoming.item_ids[0]},
        )
        == lot.id
    )


@pytest.mark.parametrize("first_mode", ["legacy", "v1", "v2"])
async def test_receiving_modes_cannot_adopt_the_same_source_twice(
    db_session: AsyncSession, first_mode: str
) -> None:
    incoming = await seed_v2_incoming(db_session)
    service = IncomingService(IncomingRepository(db_session))
    if first_mode == "legacy":
        await service.accept(incoming.document_id)
    elif first_mode == "v1":
        await accept_prepared(db_session, incoming)
    else:
        await accept_v2(db_session, incoming)
    if first_mode != "v2":
        with pytest.raises(ConflictError):
            await accept_v2(db_session, incoming)
    else:
        with pytest.raises(ConflictError):
            await service.accept(incoming.document_id)
        with pytest.raises(ConflictError):
            await accept_prepared(db_session, incoming)


async def test_v2_receiving_rejects_v1_only_history_without_guessing_blister_composition(
    db_session: AsyncSession,
) -> None:
    incoming = await seed_v2_incoming(db_session)
    await db_session.execute(
        text("UPDATE incoming_item SET unit_packaging_snapshot_v2=NULL WHERE id=:id"),
        {"id": incoming.item_ids[0]},
    )
    with pytest.raises(BusinessRuleError):
        await accept_v2(db_session, incoming)
    await assert_no_v2_stock(db_session, incoming)


async def test_v2_receiving_rolls_back_all_lines_on_an_invalid_second_source(
    db_session: AsyncSession,
) -> None:
    runtime = cast(ReceivingRuntime, db_session.info["receiving_runtime"])
    incoming = await seed_prepared_incoming(
        db_session, catalogs=(runtime.stock.catalog_id, runtime.stock.catalog_id)
    )
    await db_session.execute(
        text("UPDATE incoming_item SET unit_packaging_snapshot_v2=NULL WHERE id=:id"),
        {"id": incoming.item_ids[1]},
    )
    with pytest.raises(BusinessRuleError):
        await accept_v2(db_session, incoming)
    await assert_no_v2_stock(db_session, incoming)


async def test_v2_receiving_outer_rollback_allows_clean_retry(db_session: AsyncSession) -> None:
    incoming = await seed_v2_incoming(db_session)
    async with db_session.begin_nested() as outer:
        await accept_v2(db_session, incoming)
        await outer.rollback()
    await assert_no_v2_stock(db_session, incoming)
    assert (await accept_v2(db_session, incoming)).status == "accepted"


async def test_v2_receiving_rechecks_actor_and_allowed_branch_on_replay(
    db_session: AsyncSession,
) -> None:
    incoming = await seed_v2_incoming(db_session)
    service = IncomingService(IncomingRepository(db_session))
    for actor, branches in ((uuid4(), {incoming.stock.branch_id}), (incoming.actor_id, set())):
        with pytest.raises(PermissionDeniedError):
            await service.accept_prepared_v2(
                incoming.document_id, actor_id=actor, allowed_branch_ids=branches
            )
    await assert_no_v2_stock(db_session, incoming)
    await accept_v2(db_session, incoming)
    with pytest.raises(PermissionDeniedError):
        await service.accept_prepared_v2(
            incoming.document_id, actor_id=incoming.actor_id, allowed_branch_ids=set()
        )


async def test_v2_accepted_source_and_every_journal_component_are_immutable(
    db_session: AsyncSession,
) -> None:
    incoming = await seed_v2_incoming(db_session)
    await accept_v2(db_session, incoming)
    for statement in (
        "UPDATE incoming_item SET created_unit_lot_v2_id=NULL WHERE id=:id",
        "UPDATE incoming_item SET created_unit_lot_id=:foreign WHERE id=:id",
        "UPDATE incoming_item SET created_batch_id=:foreign WHERE id=:id",
        "UPDATE incoming_item SET unit_packaging_snapshot_v2=NULL WHERE id=:id",
        "UPDATE incoming_item SET qty=3 WHERE id=:id",
        "UPDATE incoming_item SET purchase_price=11 WHERE id=:id",
        "DELETE FROM incoming_item WHERE id=:id",
    ):
        with pytest.raises(DBAPIError):
            async with db_session.begin_nested():
                await db_session.execute(
                    text(statement), {"id": incoming.item_ids[0], "foreign": uuid4()}
                )
    for table, assignment in (
        ("unit_stock_operation_v2", "request_hash=repeat('0',64)"),
        ("unit_stock_lot_v2", "closed_packs=3"),
        ("unit_stock_lot_v2", "purchase_cost_per_pack=11"),
        ("unit_stock_lot_v2", "packaging=jsonb_set(packaging,'{units_per_blister}','15')"),
        ("unit_stock_entry_v2", "closed_delta=3"),
    ):
        for statement in (
            f"UPDATE {table} SET {assignment} WHERE tenant_id=:tenant",
            f"DELETE FROM {table} WHERE tenant_id=:tenant",
        ):
            with pytest.raises(DBAPIError):
                async with db_session.begin_nested():
                    await db_session.execute(text(statement), {"tenant": incoming.stock.tenant_id})


async def test_v2_simultaneous_retries_create_exactly_one_initialization(
    db_session: AsyncSession,
) -> None:
    runtime = cast(ReceivingRuntime, db_session.info["receiving_runtime"])
    incoming = await seed_v2_incoming(db_session)
    await db_session.commit()

    async def receive(factory: async_sessionmaker[AsyncSession]) -> UUID:
        async with factory.begin() as session:
            await set_receiving_context(session, incoming.stock, incoming.actor_id)
            await accept_v2(session, incoming)
            lot_id = await session.scalar(
                text("SELECT created_unit_lot_v2_id FROM incoming_item WHERE id=:id"),
                {"id": incoming.item_ids[0]},
            )
            assert isinstance(lot_id, UUID)
            return lot_id

    results = await asyncio.wait_for(
        asyncio.gather(*(receive(runtime.factory) for _ in range(4))), timeout=15
    )
    assert len(set(results)) == 1
    await set_receiving_context(db_session, incoming.stock, incoming.actor_id)
    for table in TABLES_V2:
        assert (
            await db_session.scalar(
                text(f"SELECT count(*) FROM {table} WHERE tenant_id=:tenant"),
                {"tenant": incoming.stock.tenant_id},
            )
            == 1
        )


@pytest.mark.parametrize("finish_initialization", [False, True])
async def test_private_initialization_cannot_commit_an_orphan_or_unaccepted_source(
    db_session: AsyncSession, finish_initialization: bool
) -> None:
    incoming = await seed_v2_incoming(db_session)
    ledger = PreparedUnitLedgerV2Service(UnitLedgerV2Repository(db_session))
    scope = {incoming.stock.branch_id}
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await ledger._claim_incoming_lot(
                incoming.item_ids[0], actor_id=incoming.actor_id, allowed_branch_ids=scope
            )
            if finish_initialization:
                await ledger._prepare_incoming_lot(
                    incoming.item_ids[0], actor_id=incoming.actor_id, allowed_branch_ids=scope
                )
            await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await assert_no_v2_stock(db_session, incoming)


@pytest.mark.parametrize(
    "tamper", ["closed_packs", "initial_closed_packs", "purchase_cost_per_pack", "packaging"]
)
async def test_sql_cannot_initialize_nonzero_or_divergent_source_stock(
    db_session: AsyncSession, tamper: str
) -> None:
    incoming = await seed_v2_incoming(db_session)
    repo = UnitLedgerV2Repository(db_session)
    ledger = PreparedUnitLedgerV2Service(repo)
    source = (
        await db_session.execute(
            text("SELECT expires_at,unit_packaging_snapshot_v2 FROM incoming_item WHERE id=:id"),
            {"id": incoming.item_ids[0]},
        )
    ).one()
    packaging = source.unit_packaging_snapshot_v2
    if tamper == "packaging":
        packaging = {**packaging, "units_per_blister": 15}
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            operation_id = await ledger._claim_incoming_lot(
                incoming.item_ids[0],
                actor_id=incoming.actor_id,
                allowed_branch_ids={incoming.stock.branch_id},
            )
            await repo.insert_lot_v2(
                UnitStockLotV2(
                    id=uuid5(
                        NAMESPACE_URL,
                        f"aurum-unit-lot-v2:{incoming.stock.tenant_id}:{operation_id}",
                    ),
                    tenant_id=incoming.stock.tenant_id,
                    branch_id=incoming.stock.branch_id,
                    catalog_id=incoming.stock.catalog_id,
                    source_item_id=incoming.item_ids[0],
                    packaging=packaging,
                    expires_at=source.expires_at,
                    initial_closed_packs=3 if tamper == "initial_closed_packs" else 2,
                    closed_packs=1 if tamper == "closed_packs" else 0,
                    purchase_cost_per_pack=(
                        Decimal("11.00") if tamper == "purchase_cost_per_pack" else Decimal("10.00")
                    ),
                    cost_version=1,
                    created_by=incoming.actor_id,
                )
            )
    await assert_no_v2_stock(db_session, incoming)


async def test_sql_cannot_append_a_second_initialization_entry(db_session: AsyncSession) -> None:
    incoming = await seed_v2_incoming(db_session)
    await accept_v2(db_session, incoming)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO unit_stock_entry_v2 "
                    "(id,tenant_id,branch_id,catalog_id,source_item_id,"
                    "operation_id,lot_id,kind,closed_delta,base_units,created_by) "
                    "SELECT gen_random_uuid(),tenant_id,branch_id,catalog_id,source_item_id,"
                    "operation_id,lot_id,kind,closed_delta,base_units,created_by "
                    "FROM unit_stock_entry_v2 WHERE tenant_id=:tenant"
                ),
                {"tenant": incoming.stock.tenant_id},
            )
    assert (
        await db_session.scalar(
            text("SELECT closed_packs FROM unit_stock_lot_v2 WHERE tenant_id=:tenant"),
            {"tenant": incoming.stock.tenant_id},
        )
        == 2
    )


async def test_direct_sql_command_rejects_a_valid_shape_with_forged_fingerprint(
    db_session: AsyncSession,
) -> None:
    incoming = await seed_v2_incoming(db_session)
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            await db_session.execute(
                text(
                    "INSERT INTO unit_stock_operation_v2 "
                    "(id,tenant_id,branch_id,catalog_id,source_item_id,"
                    "packaging,kind,sale_format,quantity,base_units,request_hash,created_by) "
                    "VALUES (:id,:tenant,:branch,:catalog,:source,CAST(:packaging AS jsonb),"
                    "'initialize','pack',2,60,:hash,:actor)"
                ),
                {
                    "id": uuid5(
                        NAMESPACE_URL,
                        f"aurum-unit-incoming-v2:{incoming.stock.tenant_id}:{incoming.item_ids[0]}",
                    ),
                    "tenant": incoming.stock.tenant_id,
                    "branch": incoming.stock.branch_id,
                    "catalog": incoming.stock.catalog_id,
                    "source": incoming.item_ids[0],
                    "packaging": json.dumps(
                        {**PACKAGING.model_dump(), "version": 2, "units_per_blister": 10}
                    ),
                    "hash": "a" * 64,
                    "actor": incoming.actor_id,
                },
            )
    await assert_no_v2_stock(db_session, incoming)


async def test_database_normalizes_forged_insert_audit_fields(
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incoming = await seed_v2_incoming(db_session)
    old_time = datetime(2000, 1, 1, tzinfo=UTC)
    foreign_actor = uuid4()
    insert_lot = UnitLedgerV2Repository.insert_lot_v2
    insert_entry = UnitLedgerV2Repository.insert_entry_v2

    async def claim_forged_operation(
        repo: UnitLedgerV2Repository, operation: UnitStockOperationV2
    ) -> tuple[UnitStockOperationV2, bool]:
        await repo.session.execute(
            insert(UnitStockOperationV2).values(
                id=operation.id,
                tenant_id=operation.tenant_id,
                branch_id=operation.branch_id,
                catalog_id=operation.catalog_id,
                source_item_id=operation.source_item_id,
                packaging=operation.packaging,
                kind=operation.kind,
                sale_format=operation.sale_format,
                quantity=operation.quantity,
                base_units=operation.base_units,
                request_hash=operation.request_hash,
                created_by=operation.created_by,
                created_at=old_time,
                updated_at=old_time,
                updated_by=foreign_actor,
            )
        )
        result = await repo.get_operation_v2(operation.tenant_id, operation.id)
        assert result is not None
        return result, True

    async def insert_forged_lot(repo: UnitLedgerV2Repository, lot: UnitStockLotV2) -> None:
        lot.created_at = old_time
        lot.updated_at = old_time
        lot.updated_by = foreign_actor
        await insert_lot(repo, lot)

    async def insert_forged_entry(repo: UnitLedgerV2Repository, entry: UnitStockEntryV2) -> None:
        entry.created_at = old_time
        entry.updated_at = old_time
        entry.updated_by = foreign_actor
        await insert_entry(repo, entry)

    monkeypatch.setattr(UnitLedgerV2Repository, "insert_lot_v2", insert_forged_lot)
    monkeypatch.setattr(UnitLedgerV2Repository, "insert_entry_v2", insert_forged_entry)
    monkeypatch.setattr(UnitLedgerV2Repository, "claim_operation_v2", claim_forged_operation)
    await accept_v2(db_session, incoming)
    for table in TABLES_V2:
        row = (
            await db_session.execute(
                text(
                    f"SELECT created_at,updated_at,created_by,updated_by FROM {table} "
                    "WHERE source_item_id=:id"
                ),
                {"id": incoming.item_ids[0]},
            )
        ).one()
        assert row.created_at > old_time and row.updated_at >= row.created_at
        assert row.created_by == row.updated_by == incoming.actor_id
