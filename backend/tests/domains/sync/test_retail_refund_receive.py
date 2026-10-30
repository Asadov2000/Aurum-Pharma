"""Refund inbox evidence never invokes payout, stock or quarantine writers."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal, localcontext
from typing import cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ClauseElement

from app.domains.pos.mixed_documents import MixedCheckoutResultV4
from app.domains.pos.physical_cash_refund_schemas import PhysicalCashRefundLineV3
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.retail_refund_events import (
    RetailRefundEventResult,
    RetailRefundSourceKind,
    build_retail_refund_event,
    retail_refund_event_fingerprint,
)
from app.domains.pos.retail_sale_events import (
    RetailCheckoutResult,
    RetailSaleStreamPosition,
    build_retail_sale_event,
)
from app.domains.pos.unit_contracts import EXACT_CONTEXT
from app.domains.sync.models import (
    SyncRetailRefundAnchor,
    SyncRetailRefundProjection,
    SyncSaleProjection,
)
from app.domains.sync.refund_read_projection import validate_retail_refund_parent
from app.domains.sync.repository import SyncEdgeRepository
from app.domains.sync.schemas import SyncEventEnvelope, SyncPullResponse
from app.domains.sync.service import SyncEdgeApplyService
from tests.domains.pos.test_retail_refund_events import KINDS, payload
from tests.domains.sync.test_retail_edge_apply import NODE, PROJECTION, SOURCE, Repository


def parent(refund: RetailRefundEventResult) -> RetailCheckoutResult:
    result, receipt = refund.result, refund.result.receipt
    lines: list[dict[str, object]] = []
    with localcontext(EXACT_CONTEXT):
        for entry in receipt.lines:
            common: dict[str, object] = {
                "id": entry.original_line_id,
                "catalog_id": uuid4(),
                "name": entry.name,
                "original_retail_unit_price": entry.original_retail_unit_price,
                "position": len(lines) + 1,
            }
            if isinstance(entry, PhysicalCashRefundLineV3):
                units = entry.packaging.units_per_pack
                discount = (entry.discount_amount * units / entry.base_units).quantize(
                    Decimal("0.01")
                )
                common.update(
                    inventory_kind="physical_unit",
                    packaging=entry.packaging,
                    sale_format="pack",
                    quantity=1,
                    base_units=units,
                    gross_amount=entry.original_retail_unit_price,
                    discount_amount=discount,
                    net_amount=entry.original_retail_unit_price - discount,
                )
            else:
                journal_line = refund.quarantine.lines[len(lines)]
                quantity = entry.offset + entry.quantity
                gross = (entry.original_retail_unit_price * quantity).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
                discount = entry.discount_amount
                common.update(
                    inventory_kind="legacy_pack",
                    batch_id=journal_line.model_dump()["batch_id"],
                    quantity=quantity,
                    gross_amount=gross,
                    discount_amount=discount,
                    net_amount=gross - discount,
                )
            lines.append(common)
        total = sum((cast(Decimal, entry["net_amount"]) for entry in lines), Decimal("0.00"))
    payment: dict[str, object] = {"id": uuid4(), "method": "cash", "amount": total}
    if refund.source_kind.endswith("electronic"):
        component = result.receipt.model_dump()["payments"][0]
        payment.update(id=component["original_payment_id"], method=component["method"])
    values = {
        "schema_version": 4 if refund.source_kind.startswith("mixed") else 3,
        "tenant_id": result.tenant_id,
        "branch_id": result.branch_id,
        "register_id": result.register_id,
        "shift_id": uuid4(),
        "cashier_id": result.actor_id,
        "sale_id": result.parent_sale_id,
        "operation_id": uuid4(),
        "event_id": uuid4(),
        "stock_command_id": uuid4(),
        "created_at": receipt.completed_at - timedelta(hours=2),
        "completed_at": receipt.completed_at - timedelta(hours=1),
        "receipt_number": receipt.parent_receipt_number,
        "receipt_sequence": 1,
        "is_test": False,
        "lines": lines,
        "payments": [payment],
        "total_amount": total,
    }
    model = MixedCheckoutResultV4 if values["schema_version"] == 4 else PhysicalCheckoutResultV3
    return model.model_validate(values)


def position(
    sequence: int = 1, source: str = SOURCE, projection: str = PROJECTION
) -> RetailSaleStreamPosition:
    return RetailSaleStreamPosition(
        origin_node_id=UUID(int=90),
        writer_epoch=2,
        sequence=sequence,
        previous_source_checksum=source,
        previous_projection_checksum=projection,
    )


def pull(
    event: SyncEventEnvelope, *, source: str = SOURCE, projection: str = PROJECTION
) -> SyncPullResponse:
    return SyncPullResponse(
        edge_node_id=NODE,
        tenant_id=event.tenant_id,
        branch_id=event.branch_id,
        origin_node_id=event.origin_node_id,
        writer_epoch=event.writer_epoch,
        effective_after_sequence=event.sequence - 1,
        after_source_checksum=source,
        after_projection_checksum=projection,
        cloud_last_sequence=event.sequence,
        events=[event],
        has_more=False,
    )


class RefundRepository(Repository):
    def __init__(self, original: SyncEventEnvelope, refund: SyncEventEnvelope) -> None:
        super().__init__(original)
        self.refund_rows: dict[UUID, SyncRetailRefundProjection] = {}
        self.refund_anchors = {
            refund.event_id: SyncRetailRefundAnchor(
                event_id=refund.event_id,
                tenant_id=refund.tenant_id,
                branch_id=refund.branch_id,
                target_edge_node_id=NODE,
                origin_node_id=refund.origin_node_id,
                writer_epoch=refund.writer_epoch,
                sequence=refund.sequence,
                previous_source_checksum=original.stream_checksum,
                previous_projection_checksum=original.projection_checksum,
                event_fingerprint=retail_refund_event_fingerprint(refund),
            )
        }
        self.parent_mutexes = 0
        self.parent_prelocks: list[tuple[UUID, ...]] = []

    async def lock_retail_refund_parents(
        self, *, tenant_id: UUID, branch_id: UUID, parent_sale_ids: tuple[UUID, ...]
    ) -> None:
        assert tenant_id == self.cursor.tenant_id and branch_id == self.cursor.branch_id
        assert parent_sale_ids == tuple(sorted(set(parent_sale_ids)))
        self.parent_prelocks.append(parent_sale_ids)

    async def get_retail_refund_anchor(self, **scope: object) -> SyncRetailRefundAnchor | None:
        for anchor in self.refund_anchors.values():
            expected: dict[str, object] = {
                "tenant_id": anchor.tenant_id,
                "branch_id": anchor.branch_id,
                "edge_node_id": anchor.target_edge_node_id,
            }
            if "event_id" in scope:
                expected["event_id"] = anchor.event_id
            else:
                expected.update(
                    origin_node_id=anchor.origin_node_id,
                    writer_epoch=anchor.writer_epoch,
                    sequence=anchor.sequence,
                )
            if scope == expected:
                return anchor
        return None

    async def get_retail_refund_projection(
        self, event_id: UUID
    ) -> SyncRetailRefundProjection | None:
        return self.refund_rows.get(event_id)

    async def get_retail_refund_projection_by_source(
        self, *, tenant_id: UUID, source_id: UUID
    ) -> SyncRetailRefundProjection | None:
        return next(
            (
                row
                for row in self.refund_rows.values()
                if (row.tenant_id, row.source_id) == (tenant_id, source_id)
            ),
            None,
        )

    async def get_sale_projection_for_update(self, sale_id: UUID) -> SyncSaleProjection | None:
        self.parent_mutexes += 1
        return await self.get_sale_projection(sale_id)

    async def list_retail_refund_projections(
        self, **scope: object
    ) -> list[SyncRetailRefundProjection]:
        rows = list(self.refund_rows.values())
        if "parent_sale_id" in scope:
            rows = [row for row in rows if row.parent_sale_id == scope["parent_sale_id"]]
        if "after_sequence" in scope:
            rows = [row for row in rows if row.sequence > cast(int, scope["after_sequence"])]
        return rows

    async def insert_retail_refund_projection(
        self, event: SyncEventEnvelope, *, parent_sale_id: UUID
    ) -> SyncRetailRefundProjection:
        row = SyncRetailRefundProjection(
            source_event_id=event.event_id,
            parent_sale_id=parent_sale_id,
            tenant_id=event.tenant_id,
            branch_id=event.branch_id,
            origin_node_id=event.origin_node_id,
            writer_epoch=event.writer_epoch,
            sequence=event.sequence,
            operation_id=event.operation_id,
            source_id=event.aggregate_id,
            payload=deepcopy(event.payload),
            source_payload_hash=event.payload_hash,
            projection_hash=event.projection_hash,
        )
        self.refund_rows[event.event_id] = row
        if self.fail_at == "projection":
            raise RuntimeError("injected refund projection failure")
        return row

    @asynccontextmanager
    async def event_savepoint(self) -> AsyncIterator[None]:
        rows = dict(self.refund_rows)
        try:
            async with super().event_savepoint():
                yield
        except Exception:
            self.refund_rows = rows
            raise


async def prepared(
    kind: RetailRefundSourceKind = "physical_cash",
) -> tuple[RefundRepository, SyncEventEnvelope, SyncEventEnvelope]:
    refund = payload(kind)
    original = build_retail_sale_event(parent(refund), position=position())
    event = build_retail_refund_event(
        refund,
        position=position(2, original.stream_checksum, original.projection_checksum),
    )
    repository = RefundRepository(original, event)
    result = await receiver(repository).apply(pull(original))
    assert result.applied == 1
    return repository, original, event


def receiver(repository: RefundRepository) -> SyncEdgeApplyService:
    return SyncEdgeApplyService(cast(SyncEdgeRepository, repository))


def refund_pull(event: SyncEventEnvelope, original: SyncEventEnvelope) -> SyncPullResponse:
    return pull(event, source=original.stream_checksum, projection=original.projection_checksum)


def later_refund(
    refund: RetailRefundEventResult, *, advance: bool = True
) -> RetailRefundEventResult:
    values = refund.model_dump(mode="python")
    operation = uuid4()
    values["result"].update(operation_id=operation, sale_id=uuid4())
    if "payment_id" in values["result"]:
        values["result"]["payment_id"] = operation
    else:
        for component in values["result"]["receipt"]["payments"]:
            component["id"] = uuid4()
    values["quarantine"]["operation_id"] = operation
    for line in values["result"]["receipt"]["lines"]:
        if advance:
            line["offset"] += line["base_units"]
    for line in values["quarantine"]["lines"]:
        if advance:
            line["offset"] += line["base_units"]
            for part in line["slices"]:
                part["source_offset"] += part["base_units"]
    values["result"]["receipt"]["receipt_sequence"] += 1
    values["result"]["receipt"]["receipt_number"] += "-next"
    values["result"]["receipt"]["completed_at"] += timedelta(minutes=1)
    return RetailRefundEventResult.model_validate(values)


def add_anchor(
    repository: RefundRepository, event: SyncEventEnvelope, before: SyncEventEnvelope
) -> None:
    repository.refund_anchors[event.event_id] = SyncRetailRefundAnchor(
        event_id=event.event_id,
        tenant_id=event.tenant_id,
        branch_id=event.branch_id,
        target_edge_node_id=NODE,
        origin_node_id=event.origin_node_id,
        writer_epoch=event.writer_epoch,
        sequence=event.sequence,
        previous_source_checksum=before.stream_checksum,
        previous_projection_checksum=before.projection_checksum,
        event_fingerprint=retail_refund_event_fingerprint(event),
    )


@pytest.mark.parametrize("kind", KINDS)
async def test_all_refunds_apply_and_replay_only_exact_read_evidence(
    kind: RetailRefundSourceKind,
) -> None:
    repository, original, event = await prepared(kind)
    service = receiver(repository)
    applied = await service.apply(refund_pull(event, original))
    assert applied.status == "synced" and applied.applied == 1
    assert len(repository.refund_rows) == 1 and len(repository.inbox) == 2
    assert len(repository.projections) == 1 and repository.parent_mutexes == 1
    assert repository.parent_prelocks == [(original.aggregate_id,)]
    assert repository.refund_rows[event.event_id].payload == event.payload
    verified = await service.verify_projection(
        tenant_id=event.tenant_id,
        branch_id=event.branch_id,
        origin_node_id=event.origin_node_id,
        writer_epoch=event.writer_epoch,
    )
    assert verified.status == "synced"
    replay = await receiver(repository).apply(refund_pull(event, original))
    assert replay.applied == 0 and replay.duplicates == 1
    await receiver(repository).validate_refund_projection(event)
    assert repository.parent_mutexes == 1
    assert repository.parent_prelocks == [(original.aggregate_id,)]
    assert len(repository.refund_rows) == 1 and len(repository.inbox) == 2


@pytest.mark.parametrize("failure", ["projection", "inbox", "cursor"])
async def test_late_failure_rolls_back_all_refund_evidence_before_clean_retry(failure: str) -> None:
    repository, original, event = await prepared()
    repository.fail_at = failure
    with pytest.raises(RuntimeError, match="injected"):
        await receiver(repository).apply(refund_pull(event, original))
    assert not repository.refund_rows and len(repository.inbox) == 1
    assert repository.cursor.last_sequence == 1
    repository.fail_at = None
    applied = await receiver(repository).apply(refund_pull(event, original))
    assert applied.applied == 1


@pytest.mark.parametrize("trust", ["missing", "node", "fingerprint"])
async def test_unsigned_valid_hashes_do_not_replace_independent_refund_trust(trust: str) -> None:
    repository, original, event = await prepared()
    anchor = repository.refund_anchors[event.event_id]
    if trust == "missing":
        repository.refund_anchors.clear()
    elif trust == "node":
        anchor.target_edge_node_id = uuid4()
    else:
        anchor.event_fingerprint = "0" * 64
    refused = await receiver(repository).apply(refund_pull(event, original))
    assert refused.status == "quarantined" and refused.applied == 0
    assert repository.cursor.last_sequence == 1 and not repository.refund_rows


@pytest.mark.parametrize("field", ["parent_sale_id", "source_id", "projection_hash", "payload"])
async def test_projection_replay_and_verifier_reject_saved_metadata_or_payload_tampering(
    field: str,
) -> None:
    repository, original, event = await prepared()
    await receiver(repository).apply(refund_pull(event, original))
    row = repository.refund_rows[event.event_id]
    if field == "payload":
        row.payload = {**row.payload, "unexpected": True}
    elif field == "projection_hash":
        row.projection_hash = "0" * 64
    else:
        setattr(row, field, uuid4())
    refused = await receiver(repository).apply(refund_pull(event, original))
    assert refused.status == "quarantined" and refused.duplicates == 0


@pytest.mark.parametrize("kind", KINDS)
def test_parent_checker_preserves_original_net_and_rejects_duplicate_paid_ranges(
    kind: RetailRefundSourceKind,
) -> None:
    refund = payload(kind)
    original = parent(refund)
    validate_retail_refund_parent(refund, original, [])
    with pytest.raises(ValueError, match="repeats|overlap"):
        validate_retail_refund_parent(refund, original, [refund])


def test_parent_checker_rejects_historical_receipt_basis_changes() -> None:
    refund = payload()
    original = parent(refund)
    changed = refund.model_dump(mode="python")
    changed["result"]["receipt"]["lines"][0]["name"] = "Rewritten historical name"
    forged = RetailRefundEventResult.model_validate(changed)
    with pytest.raises(ValueError, match="original line"):
        validate_retail_refund_parent(forged, original, [])


@pytest.mark.parametrize("kind", ["physical_cash", "physical_electronic"])
async def test_partial_history_replays_earlier_refund_after_later_return_without_double_counting(
    kind: RetailRefundSourceKind,
) -> None:
    repository, original, first = await prepared(kind)
    first_result = RetailRefundEventResult.model_validate(first.payload)
    second = build_retail_refund_event(
        later_refund(first_result),
        position=position(3, first.stream_checksum, first.projection_checksum),
    )
    add_anchor(repository, second, first)
    service = receiver(repository)
    await service.apply(refund_pull(first, original))
    applied = await service.apply(refund_pull(second, first))
    assert applied.applied == 1 and applied.status == "synced"
    replay = await service.apply(refund_pull(first, original))
    assert replay.duplicates == 1 and replay.applied == 0
    verified = await service.verify_projection(
        tenant_id=first.tenant_id,
        branch_id=first.branch_id,
        origin_node_id=first.origin_node_id,
        writer_epoch=first.writer_epoch,
    )
    assert verified.status == "synced" and repository.cursor.last_sequence == 3
    assert len(repository.refund_rows) == 2 and repository.parent_mutexes == 2


def test_distinct_refund_operations_cannot_claim_overlapping_parent_ranges() -> None:
    first = payload()
    other = later_refund(first, advance=False)
    with pytest.raises(ValueError, match="overlap"):
        validate_retail_refund_parent(other, parent(first), [first])


def test_electronic_refund_history_cannot_exceed_an_original_payment_component() -> None:
    first = payload("physical_electronic")
    original = parent(first)
    data = original.model_dump(mode="python")
    paid = first.result.total_amount
    data["payments"][0]["amount"] = paid
    data["payments"] = [
        data["payments"][0],
        {"id": uuid4(), "method": "cash", "amount": original.total_amount - paid},
    ]
    original = PhysicalCheckoutResultV3.model_validate(data)
    validate_retail_refund_parent(first, original, [])
    with pytest.raises(ValueError, match="paid amount"):
        validate_retail_refund_parent(later_refund(first), original, [first])


@pytest.mark.parametrize("damage", ["missing", "source", "hash"])
async def test_refund_requires_an_exact_applied_parent_projection(damage: str) -> None:
    repository, original, event = await prepared()
    parent_row = repository.projections[original.aggregate_id]
    if damage == "missing":
        repository.projections.clear()
    elif damage == "source":
        repository.inbox[original.event_id].status = "received"
    else:
        parent_row.projection_hash = "0" * 64
    refused = await receiver(repository).apply(refund_pull(event, original))
    assert refused.applied == 0 and refused.status == "quarantined"
    assert repository.cursor.last_sequence == 1 and not repository.refund_rows


async def test_refund_slot_cannot_be_reinterpreted_as_a_legacy_sale() -> None:
    repository, original, event = await prepared()
    rewritten = event.model_copy(update={"schema_version": 1, "event_type": "pos.sale.refunded.v1"})
    refused = await receiver(repository).apply(refund_pull(rewritten, original))
    assert refused.status == "quarantined" and refused.applied == 0
    assert refused.incident is not None
    assert refused.incident.reason_code == "retail_refund_anchor_mismatch"


async def test_readonly_materializer_validation_rejects_unapplied_source_without_writes() -> None:
    repository, original, event = await prepared()
    await receiver(repository).apply(refund_pull(event, original))
    repository.inbox[event.event_id].status = "received"
    before = repository.parent_mutexes, repository.cursor.last_sequence, len(repository.inbox)
    with pytest.raises(ValueError, match="applied source"):
        await receiver(repository).validate_refund_projection(event)
    assert (
        repository.parent_mutexes,
        repository.cursor.last_sequence,
        len(repository.inbox),
    ) == before


async def test_batch_requests_one_branch_mutex_for_all_validated_parents() -> None:
    repository, original, first = await prepared()
    second_values = later_refund(RetailRefundEventResult.model_validate(first.payload)).model_dump(
        mode="python"
    )
    second_parent = uuid4()
    second_values["result"]["parent_sale_id"] = second_parent
    second_values["result"]["receipt"]["parent_sale_id"] = second_parent
    second_values["quarantine"]["parent_sale_id"] = second_parent
    second = build_retail_refund_event(
        RetailRefundEventResult.model_validate(second_values),
        position=position(3, first.stream_checksum, first.projection_checksum),
    )
    add_anchor(repository, second, first)
    service = receiver(repository)
    service._edge_node_id = NODE
    reversed_pull = refund_pull(first, original).model_copy(update={"events": [second, first]})
    await service._lock_refund_parents(reversed_pull, repository.cursor)
    assert repository.parent_prelocks == [tuple(sorted((original.aggregate_id, second_parent)))]
    assert not repository.refund_rows and repository.cursor.last_sequence == 1


@pytest.mark.parametrize("damage", ["scope", "fingerprint"])
async def test_parent_prelocks_ignore_untrusted_or_foreign_scope_events(damage: str) -> None:
    repository, original, event = await prepared()
    if damage == "scope":
        event = event.model_copy(update={"branch_id": uuid4()})
    else:
        repository.refund_anchors[event.event_id].event_fingerprint = "0" * 64
    service = receiver(repository)
    service._edge_node_id = NODE
    before = refund_pull(original, original).model_copy(update={"events": [event]})
    await service._lock_refund_parents(before, repository.cursor)
    assert not repository.parent_prelocks and repository.parent_mutexes == 0


class ParentReadResult:
    def __init__(self, parent_row: SyncSaleProjection | None) -> None:
        self.parent_row = parent_row

    def scalar_one_or_none(self) -> SyncSaleProjection | None:
        return self.parent_row


class ParentMutexSession:
    """Record generated SQL without a database, role grants or application runtime."""

    def __init__(self, parent_row: SyncSaleProjection | None) -> None:
        self.parent_row = parent_row
        self.statements: list[str] = []
        self.mutex_scopes: list[str] = []
        self.populate_existing: list[bool] = []

    async def execute(
        self, statement: ClauseElement, parameters: dict[str, object] | None = None
    ) -> ParentReadResult:
        self.statements.append(str(statement.compile(dialect=postgresql.dialect())))
        if parameters is not None:
            self.mutex_scopes.append(cast(str, parameters["scope"]))
        else:
            self.populate_existing.append(
                bool(statement.get_execution_options().get("populate_existing"))
            )
        return ParentReadResult(self.parent_row)

    async def scalar(self, statement: ClauseElement) -> UUID | None:
        self.statements.append(str(statement.compile(dialect=postgresql.dialect())))
        return self.parent_row.sale_id if self.parent_row is not None else None


@pytest.mark.parametrize("visible", [True, False])
async def test_parent_mutex_protocol_uses_scoped_selects_without_update_privileges(
    visible: bool,
) -> None:
    parent_id, tenant_id, branch_id = uuid4(), uuid4(), uuid4()
    parent_row = (
        SyncSaleProjection(sale_id=parent_id, tenant_id=tenant_id, branch_id=branch_id)
        if visible
        else None
    )
    session = ParentMutexSession(parent_row)
    repository = SyncEdgeRepository(cast(AsyncSession, session))
    assert await repository.get_sale_projection_for_update(parent_id) is parent_row
    assert all("FOR UPDATE" not in sql and " UPDATE " not in sql for sql in session.statements)
    assert "current_tenant_id()" in session.statements[0]
    assert "current_setting" in session.statements[0]
    assert session.populate_existing == ([True, True] if visible else [True])
    if visible:
        assert session.mutex_scopes == [f"aurum-retail-refund-history:{tenant_id}:{branch_id}"]
        assert sum("pg_advisory_xact_lock" in sql for sql in session.statements) == 1
        assert "hashtextextended" in session.statements[2]
        assert "tenant_id =" in session.statements[1] and "branch_id =" in session.statements[1]
    else:
        assert not session.mutex_scopes


async def test_no_branch_mutex_is_acquired_for_absent_or_rls_hidden_parents() -> None:
    session = ParentMutexSession(None)
    await SyncEdgeRepository(cast(AsyncSession, session)).lock_retail_refund_parents(
        tenant_id=uuid4(), branch_id=uuid4(), parent_sale_ids=(uuid4(),)
    )
    assert len(session.statements) == 1 and not session.mutex_scopes
    assert "current_tenant_id()" in session.statements[0]
    assert "current_setting" in session.statements[0]
