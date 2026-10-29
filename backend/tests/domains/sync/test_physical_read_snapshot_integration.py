"""Physical bootstrap reads only fresh synthetic facts on disposable PostgreSQL CI."""

from copy import deepcopy
from dataclasses import dataclass, replace
from decimal import Decimal
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.errors import PermissionDeniedError, ServiceUnavailableError
from app.domains.inventory.physical_repository import PhysicalInventoryRepository
from app.domains.sync.credentials import EdgeCredential, parse_edge_credential
from app.domains.sync.physical_read_snapshot import PhysicalCatalogConfigRecord, PhysicalLotRecord
from app.domains.sync.physical_read_snapshot_repository import read_physical_source_facts
from app.domains.sync.physical_read_snapshot_service import (
    build_physical_source_records,
    install_physical_read_snapshot,
    read_physical_snapshot,
)
from app.domains.sync.read_snapshot import (
    ChunkRead,
    ManifestRead,
    ReadSnapshotScope,
    ReadSnapshotValidationError,
    build_manifest,
)
from app.domains.sync.read_snapshot_projection import IndexedReadProjection
from app.domains.sync.repository import SyncCloudRepository, SyncEdgeRepository
from app.domains.sync.schemas import SyncNodeCreate
from app.domains.sync.service import SyncAdminService, SyncCloudService
from tests.domains.incoming.test_unit_acceptance import (
    ReceivingRuntime,
    seed_receiving_actor,
    set_receiving_context,
)
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.inventory.test_physical_writeoff_posting import post as post_writeoff
from tests.domains.inventory.test_physical_writeoff_posting import selection as writeoff_selection
from tests.domains.inventory.test_unit_cost_ledger_v2 import priced_source
from tests.domains.inventory.test_unit_ledger_v2_dispensing import (
    PhysicalSource,
    cart_line,
    dispense,
    packs,
    receive_source,
)
from tests.domains.pos.test_unit_physical_checkout_preparation import facts

db_session = receiving_session_fixture


@dataclass(frozen=True)
class SnapshotCase:
    envelope: ManifestRead
    chunks: tuple[ChunkRead, ...]
    credential: EdgeCredential
    runtime: ReceivingRuntime
    source: PhysicalSource


async def prepare_snapshot_case(
    session: AsyncSession, maintenance_engine: AsyncEngine
) -> SnapshotCase:
    source = await receive_source(session)
    await dispense(session, source, (cart_line(source, "unit", 3),))
    runtime = cast(ReceivingRuntime, session.info["receiving_runtime"])
    await session.commit()
    async with AsyncSession(maintenance_engine, expire_on_commit=False) as owner:
        async with owner.begin():
            assert str(await owner.scalar(text("SELECT current_database()"))).endswith("_test")
            assert await owner.scalar(text("SELECT current_user")) == "aurum_schema_owner"
            await owner.execute(
                text(
                    "SELECT set_config('app.tenant_id',:tenant,true),"
                    "set_config('app.branch_id',:branch,true)"
                ),
                {"tenant": str(runtime.stock.tenant_id), "branch": str(runtime.stock.branch_id)},
            )
            node = await SyncAdminService(SyncCloudRepository(owner)).create_node(
                SyncNodeCreate(
                    tenant_id=runtime.stock.tenant_id,
                    branch_id=runtime.stock.branch_id,
                    display_name="Physical snapshot test",
                )
            )
    credential = parse_edge_credential(node.credential)
    await repeatable_context(session)
    await session.execute(
        text(
            "SELECT set_config('app.branch_id',:branch,true),"
            "set_config('app.edge_node_id',:node,true)"
        ),
        {"branch": str(runtime.stock.branch_id), "node": str(node.id)},
    )
    repo = SyncCloudRepository(session)
    savepoint = await session.begin_nested()
    envelope = await SyncCloudService(repo).publish_read_snapshot(
        edge_node_id=node.id,
        expected_tenant_id=runtime.stock.tenant_id,
        expected_branch_id=runtime.stock.branch_id,
        expected_credential_kid=credential.kid,
        credential_digest=credential.digest,
        schema_version=2,
    )
    rows = await repo.list_read_snapshot_chunks(
        snapshot_id=envelope.manifest.snapshot_id,
        tenant_id=runtime.stock.tenant_id,
        branch_id=runtime.stock.branch_id,
        edge_node_id=node.id,
    )
    chunks = tuple(
        ChunkRead(
            schema_version=row.schema_version,
            snapshot_id=row.snapshot_id,
            edge_node_id=row.node_id,
            tenant_id=row.tenant_id,
            branch_id=row.branch_id,
            source_revision=row.source_revision,
            component=row.component,
            chunk_index=row.chunk_index,
            item_count=row.item_count,
            payload_hash=row.payload_hash,
            payload=tuple(row.payload["items"]),
        )
        for row in rows
    )
    await savepoint.rollback()
    return SnapshotCase(envelope, chunks, credential, runtime, source)


async def snapshot_rows(session: AsyncSession, tenant_id: UUID) -> dict[str, list[object]]:
    tables = (
        "sync_read_snapshot",
        "sync_read_snapshot_component",
        "sync_read_snapshot_chunk",
        "sync_read_projection",
        "sync_read_catalog_projection",
        "sync_read_inventory_projection",
        "sync_read_snapshot_cursor",
        "audit_log",
    )
    return {
        table: list(
            (
                await session.scalars(
                    text(
                        f"SELECT to_jsonb(row) FROM {table} row WHERE tenant_id=:tenant "
                        "ORDER BY to_jsonb(row)::text"
                    ),
                    {"tenant": tenant_id},
                )
            ).all()
        )
        for table in tables
    }


async def repeatable_context(session: AsyncSession) -> ReceivingRuntime:
    runtime = cast(ReceivingRuntime, session.info["receiving_runtime"])
    await session.commit()
    await session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    await set_receiving_context(session, runtime.stock, runtime.actor_id)
    assert str(await session.scalar(text("SELECT current_database()"))).endswith("_test")
    assert await session.scalar(text("SHOW transaction_isolation")) == "repeatable read"
    return runtime


async def test_physical_export_refuses_read_committed_before_any_snapshot_write(
    db_session: AsyncSession,
) -> None:
    runtime = cast(ReceivingRuntime, db_session.info["receiving_runtime"])
    before = await facts(db_session)
    assert await db_session.scalar(text("SHOW transaction_isolation")) == "read committed"
    with pytest.raises(ValueError, match="repeatable"):
        await read_physical_source_facts(
            db_session, tenant_id=runtime.stock.tenant_id, branch_id=runtime.stock.branch_id
        )
    assert await facts(db_session) == before


async def test_physical_export_keeps_initial_history_pack_ordinals_and_exact_cost(
    db_session: AsyncSession,
) -> None:
    source = await receive_source(db_session)
    await dispense(db_session, source, (cart_line(source, "unit", 3),))
    await dispense(db_session, source, (cart_line(source, "unit", 27),))
    await dispense(db_session, source, (cart_line(source, "unit", 2),))
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET base_price=99,unit_sale_preparation="
            "jsonb_set(unit_sale_preparation,'{units_per_pack}','60'::jsonb) WHERE id=:id"
        ),
        {"id": source.incoming.stock.catalog_id},
    )
    runtime = await repeatable_context(db_session)
    before = await facts(db_session)
    original = (
        (
            await db_session.execute(
                text("SELECT * FROM unit_stock_lot_v2 WHERE id=:id"), {"id": source.lot_id}
            )
        )
        .mappings()
        .one()
    )
    stored_packs = list(
        (
            await db_session.execute(
                text(
                    "SELECT id,pack_ordinal,remaining_units FROM unit_stock_opened_pack_v2 "
                    "WHERE lot_id=:id ORDER BY pack_ordinal"
                ),
                {"id": source.lot_id},
            )
        ).mappings()
    )
    saved_cost = await db_session.scalar(
        text(
            "SELECT sum(cost_amount) FROM unit_stock_movement_v2 "
            "WHERE lot_id=:id AND units_taken>0"
        ),
        {"id": source.lot_id},
    )
    exported = await read_physical_source_facts(
        db_session, tenant_id=runtime.stock.tenant_id, branch_id=runtime.stock.branch_id
    )
    records = build_physical_source_records(
        exported, tenant_id=runtime.stock.tenant_id, branch_id=runtime.stock.branch_id
    )
    lot = next(row for row in records if isinstance(row, PhysicalLotRecord))
    config = next(
        row
        for row in records
        if isinstance(row, PhysicalCatalogConfigRecord) and row.id == lot.catalog_id
    )
    assert lot.id == source.lot_id
    assert lot.packaging == source.packaging
    assert config.preparation is not None and config.preparation.units_per_pack == 60
    assert lot.initial_closed_packs == original["initial_closed_packs"]
    assert lot.initial_opened_packs == original["initial_opened_packs"]
    assert lot.initial_opened_units == original["initial_opened_units"]
    assert lot.initial_opened_cost == original["initial_opened_cost"]
    assert [(row.id, row.pack_ordinal, row.remaining_units) for row in lot.opened_packs] == [
        (row["id"], row["pack_ordinal"], row["remaining_units"]) for row in stored_packs
    ]
    assert [row.remaining_units for row in lot.opened_packs] == [0, 28]
    assert lot.cost.status == "known"
    initial = (
        Decimal(original["initial_closed_packs"]) * original["purchase_cost_per_pack"]
        + original["initial_opened_cost"]
    )
    assert lot.cost.initial_cost == initial
    assert lot.cost.remaining_cost == initial - saved_cost
    assert await facts(db_session) == before


@pytest.mark.parametrize("blister", [None, 10])
@pytest.mark.parametrize(
    ("price", "expected_remaining"),
    [
        (Decimal("0.00"), Decimal("0.00")),
        (Decimal("0.01"), Decimal("0.01")),
        (Decimal("10.00"), Decimal("9.34")),
    ],
)
async def test_export_subtracts_posted_writeoff_once_from_original_cost_basis(
    db_session: AsyncSession,
    blister: int | None,
    price: Decimal,
    expected_remaining: Decimal,
) -> None:
    source = await priced_source(db_session, quantity=2, price=price, blister=blister)
    await dispense(db_session, source, (cart_line(source, "unit", 1),))
    pack_id = (await packs(db_session, source))[0][0]
    selected = (
        writeoff_selection(closed=1, pack_id=pack_id, loose=1)
        if blister is None
        else writeoff_selection(closed=1, pack_id=pack_id, blisters=((1, 1),))
    )
    operation_id = uuid4()
    result = await post_writeoff(db_session, source, selected, operation_id=operation_id)
    before_replay = await facts(db_session)
    assert await post_writeoff(db_session, source, selected, operation_id=operation_id) == result
    assert await facts(db_session) == before_replay
    await db_session.execute(
        text(
            "UPDATE tenant_catalog SET base_price=99,unit_sale_preparation="
            "jsonb_set(unit_sale_preparation,'{units_per_pack}','60'::jsonb) WHERE id=:id"
        ),
        {"id": source.incoming.stock.catalog_id},
    )
    runtime = await repeatable_context(db_session)
    before_export = await facts(db_session)
    exported = await read_physical_source_facts(
        db_session, tenant_id=runtime.stock.tenant_id, branch_id=runtime.stock.branch_id
    )
    records = build_physical_source_records(
        exported, tenant_id=runtime.stock.tenant_id, branch_id=runtime.stock.branch_id
    )
    lot = next(row for row in records if isinstance(row, PhysicalLotRecord))
    assert lot.id == source.lot_id and lot.packaging == source.packaging
    assert lot.closed_packs == 0
    assert [(pack.id, pack.pack_ordinal, pack.remaining_units) for pack in lot.opened_packs] == [
        (pack_id, 1, 28)
    ]
    assert lot.cost.status == "known"
    assert lot.cost.initial_cost == price * 2
    assert lot.cost.remaining_cost == expected_remaining
    assert await facts(db_session) == before_export


@pytest.mark.parametrize("missing", ["cost_version", "cost_amount"])
async def test_incomplete_writeoff_money_stays_unknown_without_reconstructing_basis(
    db_session: AsyncSession, missing: str
) -> None:
    source = await priced_source(db_session, quantity=2)
    await post_writeoff(db_session, source, writeoff_selection(closed=1))
    runtime = await repeatable_context(db_session)
    exported = await read_physical_source_facts(
        db_session, tenant_id=runtime.stock.tenant_id, branch_id=runtime.stock.branch_id
    )
    assert len(exported.writeoff_costs) == 1
    saved = exported.writeoff_costs[0]
    incomplete = saved.model_copy(update={missing: None})
    records = build_physical_source_records(
        replace(exported, writeoff_costs=(incomplete,)),
        tenant_id=runtime.stock.tenant_id,
        branch_id=runtime.stock.branch_id,
    )
    lot = next(row for row in records if isinstance(row, PhysicalLotRecord))
    assert lot.cost.status == "unknown"
    assert lot.cost.initial_cost is None and lot.cost.remaining_cost is None
    assert lot.purchase_cost_per_pack == Decimal("10.00")
    assert lot.initial_closed_packs == 2 and lot.closed_packs == 1
    assert exported.writeoff_costs == (saved,)


async def test_signed_physical_install_restores_facts_and_redacts_both_audit_images(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    case = await prepare_snapshot_case(db_session, maintenance_engine)
    before = await facts(db_session)
    repo = SyncEdgeRepository(db_session)
    await install_physical_read_snapshot(
        repo, envelope=case.envelope, chunks=case.chunks, credential=case.credential.token
    )
    restored = await read_physical_snapshot(
        repo,
        tenant_id=case.runtime.stock.tenant_id,
        branch_id=case.runtime.stock.branch_id,
        actor_id=case.runtime.actor_id,
        credential=case.credential.token,
    )
    assert any(isinstance(row, PhysicalLotRecord) for row in restored)
    assert all(row.tenant_id == case.runtime.stock.tenant_id for row in restored)
    assert all(row.branch_id == case.runtime.stock.branch_id for row in restored)
    audit = list(
        (
            await db_session.execute(
                text(
                    "SELECT action,old_values,new_values FROM audit_log WHERE tenant_id=:tenant "
                    "AND table_name='sync_read_projection' AND record_id=:snapshot"
                ),
                {
                    "tenant": case.runtime.stock.tenant_id,
                    "snapshot": case.envelope.manifest.snapshot_id,
                },
            )
        ).mappings()
    )
    assert {row["action"] for row in audit} == {"INSERT", "UPDATE"}
    for row in audit:
        for key in ("old_values", "new_values"):
            image = row[key]
            if image is not None:
                assert "payload" not in image
                assert "physical_payload" not in image
                assert "initial_cost" not in image and "remaining_cost" not in image
                assert "physical_count" in image
    after = await facts(db_session)
    assert {key: value for key, value in after.items() if key != "audit_log"} == {
        key: value for key, value in before.items() if key != "audit_log"
    }


class LateFailureRepository(SyncEdgeRepository):
    async def install_read_snapshot(
        self, *, envelope: ManifestRead, chunks: list[ChunkRead], projection: IndexedReadProjection
    ) -> None:
        await super().install_read_snapshot(envelope=envelope, chunks=chunks, projection=projection)
        raise RuntimeError("synthetic failure after active pointer write")


async def test_late_physical_install_failure_restores_old_pointer_and_all_rows(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    case = await prepare_snapshot_case(db_session, maintenance_engine)
    repo = SyncEdgeRepository(db_session)
    await install_physical_read_snapshot(
        repo, envelope=case.envelope, chunks=case.chunks, credential=case.credential.token
    )
    components = {
        component.component: tuple(
            chunk.payload for chunk in case.chunks if chunk.component == component.component
        )
        for component in case.envelope.manifest.components
    }
    next_envelope, next_chunks = build_manifest(
        ReadSnapshotScope(
            edge_node_id=case.envelope.manifest.edge_node_id,
            tenant_id=case.runtime.stock.tenant_id,
            branch_id=case.runtime.stock.branch_id,
            source_revision=case.envelope.manifest.source_revision + 1,
        ),
        components,
        credential_kid=case.credential.kid,
        credential_digest=case.credential.digest,
        source_generated_at=case.envelope.manifest.source_generated_at,
        schema_version=2,
    )
    before = await snapshot_rows(db_session, case.runtime.stock.tenant_id)
    with pytest.raises(RuntimeError, match="after active pointer"):
        await install_physical_read_snapshot(
            LateFailureRepository(db_session),
            envelope=next_envelope,
            chunks=next_chunks,
            credential=case.credential.token,
        )
    assert await snapshot_rows(db_session, case.runtime.stock.tenant_id) == before
    await install_physical_read_snapshot(
        repo, envelope=next_envelope, chunks=next_chunks, credential=case.credential.token
    )
    active = await repo.get_active_read_projection(
        tenant_id=case.runtime.stock.tenant_id,
        branch_id=case.runtime.stock.branch_id,
        credential_kid=case.credential.kid,
    )
    assert active is not None and active[0].snapshot_id == next_envelope.manifest.snapshot_id


@pytest.mark.parametrize("tamper", ["same-kid-secret", "coherent-physical-payload"])
async def test_warm_physical_reader_revalidates_credentials_and_signed_bytes(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, tamper: str
) -> None:
    case = await prepare_snapshot_case(db_session, maintenance_engine)
    repo = SyncEdgeRepository(db_session)
    await install_physical_read_snapshot(
        repo, envelope=case.envelope, chunks=case.chunks, credential=case.credential.token
    )
    scope = {
        "tenant_id": case.runtime.stock.tenant_id,
        "branch_id": case.runtime.stock.branch_id,
        "actor_id": case.runtime.actor_id,
    }
    assert await read_physical_snapshot(repo, **scope, credential=case.credential.token)
    if tamper == "same-kid-secret":
        prefix = "0" if case.credential.secret[0] != "0" else "1"
        wrong = EdgeCredential(kid=case.credential.kid, secret=prefix + case.credential.secret[1:])
        with pytest.raises((ReadSnapshotValidationError, ServiceUnavailableError)):
            await read_physical_snapshot(repo, **scope, credential=wrong.token)
        return
    active = await repo.get_active_read_projection(
        tenant_id=case.runtime.stock.tenant_id,
        branch_id=case.runtime.stock.branch_id,
        credential_kid=case.credential.kid,
    )
    assert active is not None
    _components, chunks = await repo.get_read_snapshot_verification_bundle(
        snapshot_id=case.envelope.manifest.snapshot_id,
        tenant_id=case.runtime.stock.tenant_id,
        branch_id=case.runtime.stock.branch_id,
        edge_node_id=case.envelope.manifest.edge_node_id,
    )
    chunk = next(row for row in chunks if row.component == "inventory")
    changed_payload = deepcopy(chunk.payload)
    item = next(row for row in changed_payload["items"] if row["record_type"] == "physical_lot")
    item["is_blocked"] = not item["is_blocked"]
    chunk.payload = changed_payload
    changed_header = deepcopy(active[1].physical_payload)
    header_item = next(row for row in changed_header if row["record_type"] == "physical_lot")
    header_item["is_blocked"] = item["is_blocked"]
    active[1].physical_payload = changed_header
    with (
        db_session.no_autoflush,
        pytest.raises((ReadSnapshotValidationError, ServiceUnavailableError)),
    ):
        await read_physical_snapshot(repo, **scope, credential=case.credential.token)


async def test_physical_history_requires_current_cost_permission(
    db_session: AsyncSession, db_engine: AsyncEngine, maintenance_engine: AsyncEngine
) -> None:
    case = await prepare_snapshot_case(db_session, maintenance_engine)
    repo = SyncEdgeRepository(db_session)
    await install_physical_read_snapshot(
        repo, envelope=case.envelope, chunks=case.chunks, credential=case.credential.token
    )
    await db_session.commit()
    actor = await seed_receiving_actor(
        db_engine, maintenance_engine, case.runtime.stock, permissions=("batches.view",)
    )
    await set_receiving_context(db_session, case.runtime.stock, actor)
    await db_session.execute(
        text(
            "SELECT set_config('app.branch_id',:branch,true),"
            "set_config('app.edge_node_id',:node,true)"
        ),
        {
            "branch": str(case.runtime.stock.branch_id),
            "node": str(case.envelope.manifest.edge_node_id),
        },
    )
    before = await snapshot_rows(db_session, case.runtime.stock.tenant_id)
    with pytest.raises(PermissionDeniedError, match="cost permission"):
        await read_physical_snapshot(
            repo,
            tenant_id=case.runtime.stock.tenant_id,
            branch_id=case.runtime.stock.branch_id,
            actor_id=actor,
            credential=case.credential.token,
        )
    assert await snapshot_rows(db_session, case.runtime.stock.tenant_id) == before
    visible = await PhysicalInventoryRepository(db_session).list_lots(
        tenant_id=case.runtime.stock.tenant_id,
        branch_id=case.runtime.stock.branch_id,
        actor_id=actor,
        catalog_id=None,
        is_blocked=None,
        show_empty=True,
        q="",
        page=1,
        page_size=25,
    )
    assert visible.total > 0
    for row in visible.model_dump(mode="json")["items"]:
        assert not {
            "purchase_price",
            "purchase_cost_per_pack",
            "initial_cost",
            "remaining_cost",
            "cost_amount",
            "initial_opened_cost",
        }.intersection(row)


async def test_only_physical_stock_change_advances_snapshot_revision(
    db_session: AsyncSession, maintenance_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = await prepare_snapshot_case(db_session, maintenance_engine)
    monkeypatch.setattr(
        "app.domains.sync.service.get_settings",
        lambda: SimpleNamespace(
            EDGE_READ_SNAPSHOT_CHUNK_SIZE=500, EDGE_READ_SNAPSHOT_REFRESH_SECONDS=0
        ),
    )
    service = SyncCloudService(SyncCloudRepository(db_session))
    first = await service.publish_read_snapshot(
        edge_node_id=case.envelope.manifest.edge_node_id,
        expected_tenant_id=case.runtime.stock.tenant_id,
        expected_branch_id=case.runtime.stock.branch_id,
        expected_credential_kid=case.credential.kid,
        credential_digest=case.credential.digest,
        schema_version=2,
    )
    catalog_time = await db_session.scalar(
        text("SELECT updated_at FROM tenant_catalog WHERE id=:id"),
        {"id": case.runtime.stock.catalog_id},
    )
    await db_session.execute(text("SELECT set_config('app.edge_node_id','',true)"))
    await dispense(db_session, case.source, (cart_line(case.source, "unit", 1),))
    await db_session.execute(
        text("SELECT set_config('app.edge_node_id',:node,true)"),
        {"node": str(case.envelope.manifest.edge_node_id)},
    )
    second = await service.publish_read_snapshot(
        edge_node_id=case.envelope.manifest.edge_node_id,
        expected_tenant_id=case.runtime.stock.tenant_id,
        expected_branch_id=case.runtime.stock.branch_id,
        expected_credential_kid=case.credential.kid,
        credential_digest=case.credential.digest,
        schema_version=2,
    )
    assert (
        await db_session.scalar(
            text("SELECT updated_at FROM tenant_catalog WHERE id=:id"),
            {"id": case.runtime.stock.catalog_id},
        )
        == catalog_time
    )
    assert second.manifest.source_revision == first.manifest.source_revision + 1
    assert second.manifest.snapshot_hash != first.manifest.snapshot_hash


async def test_exact_physical_replay_has_no_effects_and_changed_signed_replay_is_refused(
    db_session: AsyncSession, maintenance_engine: AsyncEngine
) -> None:
    case = await prepare_snapshot_case(db_session, maintenance_engine)
    repo = SyncEdgeRepository(db_session)
    await install_physical_read_snapshot(
        repo, envelope=case.envelope, chunks=case.chunks, credential=case.credential.token
    )
    before = await snapshot_rows(db_session, case.runtime.stock.tenant_id)
    business_before = await facts(db_session)
    await install_physical_read_snapshot(
        repo, envelope=case.envelope, chunks=case.chunks, credential=case.credential.token
    )
    assert await snapshot_rows(db_session, case.runtime.stock.tenant_id) == before
    assert await facts(db_session) == business_before
    components = {
        component.component: tuple(
            tuple(deepcopy(item) for item in chunk.payload)
            for chunk in case.chunks
            if chunk.component == component.component
        )
        for component in case.envelope.manifest.components
    }
    lot = next(
        item
        for payload in components["inventory"]
        for item in payload
        if item["record_type"] == "physical_lot"
    )
    lot["is_blocked"] = not lot["is_blocked"]
    changed, changed_chunks = build_manifest(
        ReadSnapshotScope(
            edge_node_id=case.envelope.manifest.edge_node_id,
            tenant_id=case.runtime.stock.tenant_id,
            branch_id=case.runtime.stock.branch_id,
            source_revision=case.envelope.manifest.source_revision,
        ),
        components,
        snapshot_id=case.envelope.manifest.snapshot_id,
        credential_kid=case.credential.kid,
        credential_digest=case.credential.digest,
        source_generated_at=case.envelope.manifest.source_generated_at,
        schema_version=2,
    )
    with pytest.raises((ValueError, ServiceUnavailableError)):
        await install_physical_read_snapshot(
            repo, envelope=changed, chunks=changed_chunks, credential=case.credential.token
        )
    assert await snapshot_rows(db_session, case.runtime.stock.tenant_id) == before
    assert await facts(db_session) == business_before
