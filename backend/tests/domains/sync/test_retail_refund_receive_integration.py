"""Real refund reception restores history without a second payout or quarantine entry."""

import json
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.domains.pos.retail_refund_events import RetailRefundSourceKind
from app.domains.sync.models import SyncRetailRefundProjection
from app.domains.sync.repository import SyncEdgeRepository
from app.domains.sync.schemas import SyncEventEnvelope, SyncPullResponse
from app.domains.sync.service import SyncEdgeApplyService
from tests.domains.incoming.test_unit_acceptance import db_session as receiving_session_fixture
from tests.domains.pos.test_unit_return import runtime
from tests.domains.sync.test_physical_stock_events_integration import app_context, finish_sources
from tests.domains.sync.test_retail_edge_apply_integration import enroll_target
from tests.domains.sync.test_retail_refund_events_integration import (
    KINDS,
    enable_refund_gate,
    event_rows,
    prepare_refund,
    refund_facts,
)

db_session = receiving_session_fixture


@dataclass(frozen=True)
class ReceivedRefund:
    parent: SyncEventEnvelope
    refund: SyncEventEnvelope
    node_id: UUID
    previous_source: str
    previous_projection: str

    def pull(self, *, refund_only: bool = False) -> SyncPullResponse:
        return SyncPullResponse(
            edge_node_id=self.node_id,
            tenant_id=self.refund.tenant_id,
            branch_id=self.refund.branch_id,
            origin_node_id=self.refund.origin_node_id,
            writer_epoch=self.refund.writer_epoch,
            effective_after_sequence=(
                self.parent.sequence if refund_only else self.parent.sequence - 1
            ),
            after_source_checksum=(
                self.parent.stream_checksum if refund_only else self.previous_source
            ),
            after_projection_checksum=(
                self.parent.projection_checksum if refund_only else self.previous_projection
            ),
            cloud_last_sequence=self.refund.sequence,
            events=[self.refund] if refund_only else [self.parent, self.refund],
            has_more=False,
        )


async def edge_context(session: AsyncSession, case: ReceivedRefund) -> None:
    await app_context(session)
    await session.execute(
        text("SELECT set_config('app.edge_node_id',:node,true)"), {"node": str(case.node_id)}
    )


async def receive_case(
    session: AsyncSession,
    owner: AsyncEngine,
    kind: RetailRefundSourceKind = "physical_cash",
    *,
    trust: bool = True,
) -> ReceivedRefund:
    source = await prepare_refund(session, owner, kind)
    await enable_refund_gate(session, owner)
    await source.complete()
    await finish_sources(session)
    refund_row = (await event_rows(session))[0]
    refund = SyncEventEnvelope.model_validate(refund_row["envelope"])
    parent_row = (
        (
            await session.execute(
                text(
                    "SELECT record,previous_source_checksum,previous_projection_checksum,"
                    "event_fingerprint "
                    "FROM pos_retail_checkout_event WHERE tenant_id=:tenant AND sale_id=:sale"
                ),
                {"tenant": refund.tenant_id, "sale": source.parent_sale_id},
            )
        )
        .mappings()
        .one()
    )
    parent = SyncEventEnvelope.model_validate(json.loads(bytes(parent_row["record"]))["envelope"])
    assert refund.sequence == parent.sequence + 1
    await session.commit()
    node = await enroll_target(owner, tenant_id=refund.tenant_id, branch_id=refund.branch_id)
    async with owner.begin() as connection:
        assert str(await connection.scalar(text("SELECT current_database()"))).endswith("_test")
        assert await connection.scalar(text("SELECT current_user")) == "aurum_schema_owner"
        for table, event, row in (
            ("sync_retail_event_anchor", parent, parent_row),
            ("sync_retail_refund_anchor", refund, refund_row),
        ):
            if table == "sync_retail_refund_anchor" and not trust:
                continue
            await connection.execute(
                text(
                    f"INSERT INTO {table}(event_id,tenant_id,branch_id,target_edge_node_id,"
                    "origin_node_id,writer_epoch,sequence,previous_source_checksum,"
                    "previous_projection_checksum,event_fingerprint) "
                    "VALUES(:event,:tenant,:branch,:node,:origin,:epoch,:sequence,:source,:projection,:hash)"
                ),
                {
                    "event": event.event_id,
                    "tenant": event.tenant_id,
                    "branch": event.branch_id,
                    "node": node,
                    "origin": event.origin_node_id,
                    "epoch": event.writer_epoch,
                    "sequence": event.sequence,
                    "source": row["previous_source_checksum"],
                    "projection": row["previous_projection_checksum"],
                    "hash": row["event_fingerprint"],
                },
            )
    case = ReceivedRefund(
        parent,
        refund,
        node,
        str(parent_row["previous_source_checksum"]),
        str(parent_row["previous_projection_checksum"]),
    )
    await edge_context(session, case)
    return case


def assert_only_refund_projection_audit_added(
    before: dict[str, list[object]],
    after: dict[str, list[object]],
    event: SyncEventEnvelope,
) -> None:
    assert {k: rows for k, rows in after.items() if k != "audit_log"} == {
        k: rows for k, rows in before.items() if k != "audit_log"
    }
    old, current = before["audit_log"], after["audit_log"]
    assert len(current) == len(old) + 1 and all(row in current for row in old)
    added = [row for row in current if row not in old]
    assert len(added) == 1
    audit = added[0]
    assert isinstance(audit, dict)
    assert audit["action"] == "INSERT"
    assert audit["table_name"] == "sync_retail_refund_projection"
    assert audit["record_id"] == str(event.event_id)
    assert audit["tenant_id"] == str(event.tenant_id)
    assert audit["user_id"] == event.payload["result"]["actor_id"]
    assert audit["old_values"] is None and audit["changed_fields"] is None
    assert audit["new_values"] == {
        "source_event_id": str(event.event_id),
        "tenant_id": str(event.tenant_id),
        "branch_id": str(event.branch_id),
        "parent_sale_id": event.payload["result"]["parent_sale_id"],
        "origin_node_id": str(event.origin_node_id),
        "writer_epoch": event.writer_epoch,
        "sequence": event.sequence,
        "operation_id": str(event.operation_id),
        "source_id": str(event.aggregate_id),
        "source_payload_hash": event.payload_hash,
        "projection_hash": event.projection_hash,
    }


@pytest.mark.parametrize("kind", KINDS)
async def test_all_four_refund_paths_receive_verify_and_replay_without_new_financial_effects(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    kind: RetailRefundSourceKind,
) -> None:
    case = await receive_case(db_session, maintenance_engine, kind)
    before = await refund_facts(db_session)
    service = SyncEdgeApplyService(SyncEdgeRepository(db_session))
    applied = await service.apply(case.pull())
    assert applied.status == "synced" and applied.applied == 2
    verified = await service.verify_projection(
        tenant_id=case.refund.tenant_id,
        branch_id=case.refund.branch_id,
        origin_node_id=case.refund.origin_node_id,
        writer_epoch=case.refund.writer_epoch,
    )
    assert verified.status == "synced" and verified.last_sequence == case.refund.sequence
    after = await refund_facts(db_session)
    assert_only_refund_projection_audit_added(before, after, case.refund)
    await db_session.commit()
    await edge_context(db_session, case)
    replay = await SyncEdgeApplyService(SyncEdgeRepository(db_session)).apply(case.pull())
    assert replay.applied == 0 and replay.duplicates == 2
    assert await refund_facts(db_session) == after


async def test_missing_refund_anchor_stops_after_parent_without_inventing_trust(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
) -> None:
    case = await receive_case(db_session, maintenance_engine, trust=False)
    repository = SyncEdgeRepository(db_session)
    result = await SyncEdgeApplyService(repository).apply(case.pull())
    assert result.status == "quarantined" and result.applied == 1
    assert result.last_sequence == case.parent.sequence
    assert await repository.get_retail_refund_projection(case.refund.event_id) is None


@pytest.mark.parametrize("failure", ["projection", "inbox", "cursor"])
async def test_late_receive_failure_rolls_back_only_refund_before_exact_retry(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    case = await receive_case(db_session, maintenance_engine)
    repository = SyncEdgeRepository(db_session)
    parent_pull = case.pull().model_copy(
        update={"events": [case.parent], "cloud_last_sequence": case.parent.sequence}
    )
    assert (await SyncEdgeApplyService(repository).apply(parent_pull)).applied == 1
    before = await refund_facts(db_session)
    name = {
        "projection": "insert_retail_refund_projection",
        "inbox": "mark_inbox",
        "cursor": "update_cursor",
    }[failure]
    original = getattr(repository, name)

    async def fail_after_write(*arguments: object, **values: object) -> object:
        await original(*arguments, **values)
        raise RuntimeError("injected refund receive failure")

    with monkeypatch.context() as patch:
        patch.setattr(repository, name, fail_after_write)
        with pytest.raises(RuntimeError, match="injected refund receive failure"):
            await SyncEdgeApplyService(repository).apply(case.pull(refund_only=True))
    assert await repository.get_inbox_event(case.refund.event_id) is None
    assert await repository.get_retail_refund_projection(case.refund.event_id) is None
    assert await refund_facts(db_session) == before
    retry = await SyncEdgeApplyService(repository).apply(case.pull(refund_only=True))
    assert retry.status == "synced" and retry.applied == 1


@pytest.mark.parametrize("kind", KINDS)
async def test_restored_refund_can_be_checked_in_read_only_transaction(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    kind: RetailRefundSourceKind,
) -> None:
    case = await receive_case(db_session, maintenance_engine, kind)
    repository = SyncEdgeRepository(db_session)
    assert (await SyncEdgeApplyService(repository).apply(case.pull())).applied == 2
    await db_session.commit()
    await db_session.execute(text("SET TRANSACTION READ ONLY"))
    await edge_context(db_session, case)
    checked = await SyncEdgeApplyService(repository).validate_refund_projection(case.refund)
    assert checked.result.operation_id == case.refund.operation_id
    assert runtime(db_session).stock.tenant_id == checked.result.tenant_id


@pytest.mark.parametrize(
    "field",
    [
        "parent_sale_id",
        "operation_id",
        "sequence",
        "source_payload_hash",
    ],
)
async def test_native_projection_guard_rejects_tampering_even_without_service_validation(
    db_session: AsyncSession,
    maintenance_engine: AsyncEngine,
    field: str,
) -> None:
    case = await receive_case(db_session, maintenance_engine)
    repository = SyncEdgeRepository(db_session)
    parent_pull = case.pull().model_copy(
        update={"events": [case.parent], "cloud_last_sequence": case.parent.sequence}
    )
    assert (await SyncEdgeApplyService(repository).apply(parent_pull)).applied == 1
    event = case.refund
    await repository.insert_inbox(event, status="received")
    values: dict[str, object] = {
        "source_event_id": event.event_id,
        "tenant_id": event.tenant_id,
        "branch_id": event.branch_id,
        "parent_sale_id": case.parent.aggregate_id,
        "origin_node_id": event.origin_node_id,
        "writer_epoch": event.writer_epoch,
        "sequence": event.sequence,
        "operation_id": event.operation_id,
        "source_id": event.aggregate_id,
        "payload": event.payload,
        "source_payload_hash": event.payload_hash,
        "projection_hash": event.projection_hash,
    }
    values[field] = (
        event.sequence + 1
        if field == "sequence"
        else "f" * 64 if field == "source_payload_hash" else uuid4()
    )
    before = await refund_facts(db_session)
    with pytest.raises(DBAPIError) as denied:
        async with db_session.begin_nested():
            await db_session.execute(insert(SyncRetailRefundProjection).values(**values))
    assert getattr(denied.value.orig, "sqlstate", None) == "23514"
    assert await repository.get_retail_refund_projection(event.event_id) is None
    assert await refund_facts(db_session) == before
