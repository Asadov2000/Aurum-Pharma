"""Deterministic read-only inventory reconstruction from independently trusted facts.

The repository must supply the baseline and event anchors from protected storage
for the target Edge. Creating these value objects from incoming data establishes
neither authentication nor a coherent baseline. This module grants no capability,
loads no anchors, and never writes operational stock or financial records.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import Context, localcontext
from uuid import UUID

from pydantic import Field

from app.domains.inventory.unit_stock_v2 import OpenedPackV2, _opened_units
from app.domains.pos.retail_refund_events import (
    retail_refund_event_fingerprint,
    validate_retail_refund_event,
)
from app.domains.pos.retail_sale_event_storage import (
    RetailSaleEventAnchor,
    decode_retail_sale_event,
    encode_retail_sale_event,
)
from app.domains.pos.schemas import SaleCheckoutResult, SaleRefundResult
from app.domains.pos.unit_contracts import FrozenContract
from app.domains.pos.unit_events import StreamChecksum, StreamInteger
from app.domains.pos.unit_money import allocate_unit_amount
from app.domains.sync.integrity import (
    canonical_json_hash,
    projection_stream_checksum,
    source_stream_checksum,
)
from app.domains.sync.physical_read_snapshot import (
    MAX_PHYSICAL_RECORDS,
    PHYSICAL_RECORD_ADAPTER,
    PhysicalCatalogConfigRecord,
    PhysicalCostRecord,
    PhysicalLotRecord,
    PhysicalOpenedPackRecord,
    PhysicalRecord,
    validate_physical_records,
)
from app.domains.sync.physical_stock_events import (
    PhysicalPackState,
    PhysicalStockChange,
    PhysicalStockConfig,
    PhysicalStockEventAnchor,
    PhysicalStockMovement,
    decode_physical_stock_event,
    encode_physical_stock_event,
)
from app.domains.sync.schemas import SyncEventEnvelope

MAX_PROJECTION_EVENTS = 10_000


class PhysicalStockBaselineAnchor(FrozenContract):
    """A protected coherent checkpoint, independent of snapshot revision numbers."""

    snapshot_id: UUID
    snapshot_hash: StreamChecksum
    physical_records_hash: StreamChecksum
    configuration_hash: StreamChecksum
    tenant_id: UUID
    branch_id: UUID
    target_edge_node_id: UUID
    origin_node_id: UUID
    writer_epoch: StreamInteger
    sequence: int = Field(strict=True, ge=0)
    source_checksum: StreamChecksum
    projection_checksum: StreamChecksum


class PhysicalStockProjection(FrozenContract):
    """Derived immutable read state; readiness also requires complete checkout pairs."""

    anchor: PhysicalStockBaselineAnchor
    physical_records: tuple[PhysicalRecord, ...]
    configuration: tuple[PhysicalStockConfig, ...]
    last_sequence: int = Field(strict=True, ge=0)
    source_checksum: StreamChecksum
    projection_checksum: StreamChecksum
    pending_checkout_sale_ids: tuple[UUID, ...]
    baseline_required_event_ids: tuple[UUID, ...]

    @property
    def ready(self) -> bool:
        """Only local projection completeness, never permission to activate Edge."""
        return not self.pending_checkout_sale_ids and not self.baseline_required_event_ids


def physical_baseline_records_hash(records: Sequence[PhysicalRecord]) -> str:
    ordered = sorted(records, key=lambda row: (row.record_type, str(row.id)))
    return canonical_json_hash([row.model_dump(mode="json") for row in ordered])


def physical_baseline_configuration_hash(configuration: Sequence[PhysicalStockConfig]) -> str:
    ordered = sorted(configuration, key=lambda row: str(row.catalog_id))
    return canonical_json_hash([row.model_dump(mode="json") for row in ordered])


def _pack_state(pack: PhysicalOpenedPackRecord) -> PhysicalPackState:
    return PhysicalPackState.model_validate(pack.state.model_dump(exclude={"id", "opened_at"}))


def _pack_record(
    movement: PhysicalStockMovement, state: PhysicalPackState, lot: PhysicalLotRecord
) -> PhysicalOpenedPackRecord:
    if movement.pack_id is None or movement.opened_at is None or movement.pack_ordinal is None:
        raise ValueError("Physical movement has no complete pack identity")
    pack = OpenedPackV2(
        id=movement.pack_id,
        opened_at=movement.opened_at,
        **state.model_dump(mode="python"),
    )
    remaining = (
        _opened_units(pack, lot.packaging)
        if pack.intact_blisters or pack.opened_blisters or pack.loose_units
        else 0
    )
    return PhysicalOpenedPackRecord(
        id=movement.pack_id,
        pack_ordinal=movement.pack_ordinal,
        opened_at=movement.opened_at,
        state=pack,
        remaining_units=remaining,
    )


def _blisters(state: PhysicalPackState, size: int) -> dict[int, int]:
    return {
        **dict.fromkeys(state.intact_blisters, size),
        **{blister.number: blister.remaining_units for blister in state.opened_blisters},
    }


def _validate_consumption(
    before: PhysicalOpenedPackRecord,
    after: PhysicalOpenedPackRecord,
    movement: PhysicalStockMovement,
    lot: PhysicalLotRecord,
) -> None:
    if before.remaining_units - after.remaining_units != movement.units_taken:
        raise ValueError("Physical consumption differs from its exact unit delta")
    previous, following = _pack_state(before), _pack_state(after)
    size = lot.packaging.units_per_blister
    if size is None:
        monotone = following.loose_units <= previous.loose_units
    else:
        old, new = _blisters(previous, size), _blisters(following, size)
        monotone = all(amount <= old.get(number, 0) for number, amount in new.items())
    if not monotone:
        raise ValueError("Physical consumption cannot restore or substitute blister identities")
    if movement.kind == "blister_take" and (
        size is None
        or movement.units_taken % size
        or following.opened_blisters != previous.opened_blisters
        or following.loose_units != previous.loose_units
    ):
        raise ValueError("Blister dispensing must consume only whole intact blisters")


def _remaining_cost(
    lot: PhysicalLotRecord, movement: PhysicalStockMovement, *, before_units: int | None
) -> PhysicalCostRecord:
    if movement.kind == "pack_open":
        if any(
            value is not None
            for value in (
                movement.cost_amount,
                movement.cost_version,
                movement.cost_original_pack_cost,
                movement.cost_unit_offset,
            )
        ):
            raise ValueError("Opening cannot consume historical cost")
        return lot.cost
    amount, original, offset = (
        movement.cost_amount,
        movement.cost_original_pack_cost,
        movement.cost_unit_offset,
    )
    if amount is None or original is None or offset is None or movement.cost_version != 1:
        raise ValueError("Physical consumption has incomplete historical cost")
    if lot.purchase_cost_per_pack is not None and original != lot.purchase_cost_per_pack:
        raise ValueError("Physical consumption changes its historical pack cost")
    if before_units is None:
        with localcontext(Context(prec=40)):
            expected = -movement.closed_delta * original
        if offset or movement.units_taken != -movement.closed_delta * lot.packaging.units_per_pack:
            raise ValueError("Sealed consumption has an invalid cost range")
    else:
        if offset != lot.packaging.units_per_pack - before_units:
            raise ValueError("Physical consumption changes its historical cost offset")
        expected = allocate_unit_amount(
            original,
            total_units=lot.packaging.units_per_pack,
            offset=offset,
            quantity=movement.units_taken,
        ).amount
    if amount != expected:
        raise ValueError("Physical consumption cost differs from its historical allocation")
    if lot.cost.status == "unknown":
        return lot.cost
    if lot.cost.remaining_cost is None:
        raise ValueError("Known physical cost has no remaining amount")
    with localcontext(Context(prec=40)):
        remaining = lot.cost.remaining_cost - amount
    return PhysicalCostRecord(
        status="known", version=1, initial_cost=lot.cost.initial_cost, remaining_cost=remaining
    )


def _apply_movement(lot: PhysicalLotRecord, movement: PhysicalStockMovement) -> PhysicalLotRecord:
    if movement.catalog_id != lot.catalog_id:
        raise ValueError("Physical movement escapes its original catalog item")
    if (movement.kind == "pack_take" and movement.pack_id is not None) or (
        movement.kind in ("blister_take", "unit_take") and movement.pack_id is None
    ):
        raise ValueError("Physical movement kind differs from its original stock representation")
    packs = {pack.id: pack for pack in lot.opened_packs}
    ordinal = lot.initial_closed_packs + lot.initial_opened_packs - lot.closed_packs + 1
    before_units: int | None = None
    if movement.pack_id is None:
        if movement.sealed_start != ordinal:
            raise ValueError("Physical sealed consumption reuses or skips a pack ordinal")
    elif movement.kind == "pack_open":
        _open_pack(lot, movement, packs, ordinal)
    else:
        previous = packs.get(movement.pack_id)
        if previous is None or movement.before_state != _pack_state(previous):
            raise ValueError("Physical consumption before-state differs from its projection")
        if (
            movement.pack_ordinal != previous.pack_ordinal
            or movement.opened_at != previous.opened_at
        ):
            raise ValueError("Physical consumption changes its pack identity")
        if movement.closed_delta or movement.after_state is None:
            raise ValueError("Opened consumption must not change closed stock")
        following = _pack_record(movement, movement.after_state, lot)
        _validate_consumption(previous, following, movement, lot)
        packs[movement.pack_id] = following
        before_units = previous.remaining_units
    cost = _remaining_cost(lot, movement, before_units=before_units)
    data = lot.model_dump(mode="python")
    data.update(
        closed_packs=lot.closed_packs + movement.closed_delta,
        opened_packs=tuple(sorted(packs.values(), key=lambda pack: pack.pack_ordinal)),
        cost=cost,
    )
    return PhysicalLotRecord.model_validate(data)


def _open_pack(
    lot: PhysicalLotRecord,
    movement: PhysicalStockMovement,
    packs: dict[UUID, PhysicalOpenedPackRecord],
    ordinal: int,
) -> None:
    if (
        movement.pack_id is None
        or movement.pack_id in packs
        or movement.pack_ordinal != ordinal
        or movement.before_state != PhysicalPackState()
        or movement.after_state is None
    ):
        raise ValueError("Physical opening changes its original pack identity or before-state")
    following = _pack_record(movement, movement.after_state, lot)
    if following.remaining_units != lot.packaging.units_per_pack:
        raise ValueError("Physical opening must preserve the entire original pack")
    packs[movement.pack_id] = following


@dataclass
class _ReadState:
    records: dict[tuple[str, UUID], PhysicalRecord]
    configuration: dict[UUID, PhysicalStockConfig]
    checkout: dict[UUID, tuple[UUID, UUID]] = field(default_factory=dict)
    effects: dict[UUID, tuple[UUID, UUID]] = field(default_factory=dict)
    sources: set[tuple[str, UUID, int | None]] = field(default_factory=set)
    movements: set[UUID] = field(default_factory=set)
    cancelled: set[UUID] = field(default_factory=set)
    written_off: set[UUID] = field(default_factory=set)
    baseline_required: set[UUID] = field(default_factory=set)

    def apply(self, change: PhysicalStockChange) -> None:
        revision = (
            change.configuration.revision
            if change.configuration is not None
            else change.blocking.revision if change.blocking is not None else None
        )
        key = change.source_kind, change.source_id, revision
        if key in self.sources:
            raise ValueError("Physical source has already acquired an inventory effect")
        self.sources.add(key)
        if change.source_kind == "baseline_required":
            self.baseline_required.add(change.event_id)
        self._writeoff_terminal(change)
        self._dispense_complete(change)
        for initial_lot in change.initializations:
            self._initialize(initial_lot)
        for movement in change.movements:
            if movement.id in self.movements:
                raise ValueError("Physical movement has already been projected")
            if len(self.movements) >= MAX_PROJECTION_EVENTS:
                raise ValueError("Physical projection movement bound exceeded")
            self.movements.add(movement.id)
            lot = self.records.get(("physical_lot", movement.lot_id))
            if not isinstance(lot, PhysicalLotRecord):
                raise ValueError("Physical movement references an absent v2 lot")
            if change.source_kind == "dispense" and lot.is_blocked:
                raise ValueError("Dispensing cannot consume blocked physical stock")
            self.records[("physical_lot", lot.id)] = _apply_movement(lot, movement)
        if change.configuration is not None:
            self._configure(change)
        if change.blocking is not None:
            self._block(change)
        if change.source_owner == "checkout":
            self._checkout_effect(change)

    @staticmethod
    def _dispense_complete(change: PhysicalStockChange) -> None:
        if change.source_kind != "dispense":
            return
        consumed_packs = {movement.pack_id for movement in change.movements if movement.units_taken}
        if any(movement.line_id is None for movement in change.movements) or any(
            movement.pack_id not in consumed_packs
            for movement in change.movements
            if movement.kind == "pack_open"
        ):
            raise ValueError("Physical dispensing command has incomplete line or opening facts")

    def _initialize(self, lot: PhysicalLotRecord) -> None:
        if len(self.records) >= MAX_PROJECTION_EVENTS:
            raise ValueError("Physical projection record bound exceeded")
        if any(key[1] == lot.id for key in self.records if key[0] != "physical_catalog_config"):
            raise ValueError("Physical initialization aliases an existing stock lot")
        if lot.catalog_id not in self.configuration:
            raise ValueError("Physical initialization references absent catalog configuration")
        if (
            lot.closed_packs != lot.initial_closed_packs
            or lot.cost.remaining_cost != lot.cost.initial_cost
        ):
            raise ValueError("Physical initialization includes later consumption")
        basis = {pack.id: pack for pack in lot.opening_basis}
        if len(lot.opened_packs) != lot.initial_opened_packs or any(
            pack.id not in basis
            or pack.state != basis[pack.id].state
            or pack.remaining_units != basis[pack.id].remaining_units
            for pack in lot.opened_packs
        ):
            raise ValueError("Physical initialization changes its original opened basis")
        self.records[(lot.record_type, lot.id)] = lot

    def _configure(self, change: PhysicalStockChange) -> None:
        config = change.configuration
        if config is None:
            raise ValueError("Catalog change has no configuration")
        previous = self.configuration.get(config.catalog_id)
        if previous is None and len(self.records) >= MAX_PROJECTION_EVENTS:
            raise ValueError("Physical projection record bound exceeded")
        if config.revision != (previous.revision + 1 if previous is not None else 1):
            raise ValueError("Physical configuration revision skips or replays history")
        self.configuration[config.catalog_id] = config
        self.records[("physical_catalog_config", config.catalog_id)] = PhysicalCatalogConfigRecord(
            id=config.catalog_id,
            tenant_id=change.tenant_id,
            branch_id=change.branch_id,
            preparation=config.preparation,
        )

    def _block(self, change: PhysicalStockChange) -> None:
        block = change.blocking
        if block is None:
            raise ValueError("Blocking change has no physical state")
        lot = self.records.get(("physical_lot", block.lot_id))
        if not isinstance(lot, PhysicalLotRecord) or lot.catalog_id != block.catalog_id:
            raise ValueError("Blocking change references an absent or different physical lot")
        if block.revision != lot.blocking_revision + 1 or block.is_blocked == lot.is_blocked:
            raise ValueError("Physical blocking revision skips or replays history")
        self.records[("physical_lot", lot.id)] = PhysicalLotRecord.model_validate(
            {
                **lot.model_dump(mode="python"),
                "is_blocked": block.is_blocked,
                "blocking_revision": block.revision,
            }
        )

    def _writeoff_terminal(self, change: PhysicalStockChange) -> None:
        if change.source_kind == "writeoff":
            if change.source_id in self.cancelled:
                raise ValueError("Cancelled write-off cannot consume physical stock")
            self.written_off.add(change.source_id)
        if change.source_kind == "writeoff_cancelled":
            if change.source_id in self.written_off:
                raise ValueError("Completed write-off cannot be cancelled or restore stock")
            self.cancelled.add(change.source_id)

    def _checkout_effect(self, change: PhysicalStockChange) -> None:
        if change.checkout_sale_id is None or change.checkout_event_id is None:
            raise ValueError("Checkout stock effect has no financial binding")
        if change.source_id in self.effects:
            raise ValueError("Checkout stock command has more than one inventory owner")
        self.effects[change.source_id] = change.checkout_sale_id, change.checkout_event_id

    def pending(self) -> tuple[UUID, ...]:
        pending: set[UUID] = set()
        for command in self.checkout.keys() | self.effects.keys():
            financial, physical = self.checkout.get(command), self.effects.get(command)
            if financial is not None and physical is not None and financial != physical:
                raise ValueError("Checkout and inventory effect disagree about their source")
            if financial is None or physical is None:
                binding = financial if financial is not None else physical
                if binding is None:
                    raise ValueError("Physical checkout coverage has no source binding")
                pending.add(binding[0])
        return tuple(sorted(pending, key=str))


def _baseline_state(
    records: Sequence[PhysicalRecord],
    configuration: Sequence[PhysicalStockConfig],
    anchor: PhysicalStockBaselineAnchor,
) -> _ReadState:
    if len(records) > MAX_PHYSICAL_RECORDS or len(configuration) > MAX_PHYSICAL_RECORDS:
        raise ValueError("Physical baseline record bound exceeded")
    checked = tuple(PHYSICAL_RECORD_ADAPTER.validate_python(row.model_dump()) for row in records)
    configs = tuple(PhysicalStockConfig.model_validate(row.model_dump()) for row in configuration)
    if physical_baseline_records_hash(checked) != anchor.physical_records_hash:
        raise ValueError("Physical baseline differs from its independently trusted hash")
    if physical_baseline_configuration_hash(configs) != anchor.configuration_hash:
        raise ValueError("Physical configuration differs from its independently trusted hash")
    by_catalog = {config.catalog_id: config for config in configs}
    if len(by_catalog) != len(configs):
        raise ValueError("Physical baseline configuration identities must be unique")
    ordered = validate_physical_records(
        checked,
        tenant_id=anchor.tenant_id,
        branch_id=anchor.branch_id,
        catalog_ids=set(by_catalog),
    )
    if any(
        isinstance(row, PhysicalCatalogConfigRecord)
        and row.preparation != by_catalog[row.id].preparation
        for row in ordered
    ):
        raise ValueError("Physical baseline configuration differs from its snapshot")
    return _ReadState({(row.record_type, row.id): row for row in ordered}, by_catalog)


def _legacy_event(
    envelope: SyncEventEnvelope, source_checksum: str, projection_checksum: str
) -> None:
    if envelope.event_type == "pos.sale.completed.v1":
        result: SaleCheckoutResult | SaleRefundResult = SaleCheckoutResult.model_validate(
            envelope.payload
        )
    elif envelope.event_type == "pos.sale.refunded.v1":
        result = SaleRefundResult.model_validate(envelope.payload)
    else:
        raise ValueError("Unsupported legacy event in physical projection")
    if (
        envelope.aggregate_type != "sale"
        or envelope.aggregate_id != result.sale_id
        or envelope.event_id != result.event_id
        or envelope.operation_id != result.operation_id
        or envelope.tenant_id != result.tenant_id
        or envelope.branch_id != result.branch_id
        or envelope.occurred_at != result.completed_at
    ):
        raise ValueError("Legacy projection differs from its source envelope")
    digest = canonical_json_hash(result.model_dump(mode="json"))
    source = source_stream_checksum(
        previous_checksum=source_checksum,
        event_id=envelope.event_id,
        tenant_id=envelope.tenant_id,
        branch_id=envelope.branch_id,
        origin_node_id=envelope.origin_node_id,
        writer_epoch=envelope.writer_epoch,
        sequence=envelope.sequence,
        operation_id=envelope.operation_id,
        aggregate_type=envelope.aggregate_type,
        aggregate_id=envelope.aggregate_id,
        event_type=envelope.event_type,
        schema_version=envelope.schema_version,
        occurred_at=envelope.occurred_at,
        payload_hash=envelope.payload_hash,
    )
    projection = projection_stream_checksum(
        previous_checksum=projection_checksum,
        origin_node_id=envelope.origin_node_id,
        writer_epoch=envelope.writer_epoch,
        sequence=envelope.sequence,
        sale_id=result.sale_id,
        projection_hash=digest,
    )
    if (
        canonical_json_hash(envelope.payload) != envelope.payload_hash
        or digest != envelope.projection_hash
        or source != envelope.stream_checksum
        or projection != envelope.projection_checksum
    ):
        raise ValueError("Legacy physical projection chain differs from its source")


def _apply_event(
    state: _ReadState,
    envelope: SyncEventEnvelope,
    *,
    source_checksum: str,
    projection_checksum: str,
    stock_anchors: Mapping[UUID, PhysicalStockEventAnchor],
    retail_anchors: Mapping[UUID, RetailSaleEventAnchor],
    refund_anchors: Mapping[UUID, RetailSaleEventAnchor],
) -> None:
    if envelope.schema_version == 5:
        stock_anchor = stock_anchors.get(envelope.event_id)
        if stock_anchor is None:
            raise ValueError("Physical event has no independently trusted anchor")
        _anchor_checkpoint(stock_anchor, source_checksum, projection_checksum)
        change = decode_physical_stock_event(
            encode_physical_stock_event(envelope, anchor=stock_anchor), anchor=stock_anchor
        )
        state.apply(change)
    elif envelope.schema_version in (3, 4):
        retail_anchor = retail_anchors.get(envelope.event_id)
        if retail_anchor is None:
            raise ValueError("Retail event has no independently trusted anchor")
        _anchor_checkpoint(retail_anchor, source_checksum, projection_checksum)
        checkout = decode_retail_sale_event(
            encode_retail_sale_event(envelope, anchor=retail_anchor), anchor=retail_anchor
        )
        if checkout.is_test or checkout.stock_command_id in state.checkout:
            raise ValueError("Physical projection cannot repeat or project training checkout")
        state.checkout[checkout.stock_command_id] = checkout.sale_id, checkout.event_id
    elif envelope.schema_version == 6:
        refund_anchor = refund_anchors.get(envelope.event_id)
        if refund_anchor is None:
            raise ValueError("Refund event has no independently trusted anchor")
        _anchor_checkpoint(refund_anchor, source_checksum, projection_checksum)
        if (
            refund_anchor.tenant_id != envelope.tenant_id
            or refund_anchor.branch_id != envelope.branch_id
            or refund_anchor.origin_node_id != envelope.origin_node_id
            or refund_anchor.writer_epoch != envelope.writer_epoch
            or refund_anchor.sequence != envelope.sequence
            or refund_anchor.event_fingerprint != retail_refund_event_fingerprint(envelope)
        ):
            raise ValueError("Refund event differs from its independently trusted anchor")
        validate_retail_refund_event(
            envelope,
            previous_source_checksum=source_checksum,
            previous_projection_checksum=projection_checksum,
        )
        # A paid refund records quarantine, never replenishes available inventory.
    elif envelope.schema_version == 1:
        _legacy_event(envelope, source_checksum, projection_checksum)
    else:
        raise ValueError("Unsupported event in physical projection")


def _anchor_checkpoint(anchor: RetailSaleEventAnchor, source: str, projection: str) -> None:
    if (
        anchor.previous_source_checksum != source
        or anchor.previous_projection_checksum != projection
    ):
        raise ValueError("Event anchor differs from the coherent physical checkpoint")


def reduce_physical_stock_projection(
    *,
    baseline_records: Sequence[PhysicalRecord],
    baseline_configuration: Sequence[PhysicalStockConfig],
    anchor: PhysicalStockBaselineAnchor,
    events: Sequence[SyncEventEnvelope],
    stock_anchors: Mapping[UUID, PhysicalStockEventAnchor],
    retail_anchors: Mapping[UUID, RetailSaleEventAnchor],
    refund_anchors: Mapping[UUID, RetailSaleEventAnchor] | None = None,
) -> PhysicalStockProjection:
    """Fold one bounded coherent epoch, requiring protected caller-supplied evidence.

    Exact input replays have no second effect. A partial checkout/stock pair is
    returned with ready=False; mismatched pairs or invalid facts fail closed.
    The caller must authenticate legacy v1 delivery and protect target-node
    scoping when retrieving event anchors, as their existing DTO omits that node.
    """
    anchor = PhysicalStockBaselineAnchor.model_validate(anchor.model_dump())
    if len(events) > MAX_PROJECTION_EVENTS:
        raise ValueError("Physical projection event bound exceeded")
    state = _baseline_state(baseline_records, baseline_configuration, anchor)
    sequence, source, projection = (
        anchor.sequence,
        anchor.source_checksum,
        anchor.projection_checksum,
    )
    seen: dict[UUID, str] = {}
    operations: set[UUID] = set()
    for event in events:
        envelope = SyncEventEnvelope.model_validate(event.model_dump())
        digest = canonical_json_hash(envelope.model_dump(mode="json"))
        if envelope.event_id in seen:
            if seen[envelope.event_id] != digest:
                raise ValueError("Physical event replay changes its original contents")
            continue
        if (
            envelope.tenant_id != anchor.tenant_id
            or envelope.branch_id != anchor.branch_id
            or envelope.origin_node_id != anchor.origin_node_id
            or envelope.writer_epoch != anchor.writer_epoch
            or envelope.sequence != sequence + 1
        ):
            raise ValueError("Physical event skips its coherent scope or sequence")
        if envelope.operation_id in operations:
            raise ValueError("Physical event operation has already been projected")
        _apply_event(
            state,
            envelope,
            source_checksum=source,
            projection_checksum=projection,
            stock_anchors=stock_anchors,
            retail_anchors=retail_anchors,
            refund_anchors=refund_anchors if refund_anchors is not None else {},
        )
        seen[envelope.event_id] = digest
        operations.add(envelope.operation_id)
        sequence, source, projection = (
            envelope.sequence,
            envelope.stream_checksum,
            envelope.projection_checksum,
        )
    records = validate_physical_records(
        tuple(state.records.values()),
        tenant_id=anchor.tenant_id,
        branch_id=anchor.branch_id,
        catalog_ids=set(state.configuration),
    )
    return PhysicalStockProjection(
        anchor=anchor,
        physical_records=records,
        configuration=tuple(
            sorted(state.configuration.values(), key=lambda row: str(row.catalog_id))
        ),
        last_sequence=sequence,
        source_checksum=source,
        projection_checksum=projection,
        pending_checkout_sale_ids=state.pending(),
        baseline_required_event_ids=tuple(sorted(state.baseline_required, key=str)),
    )
