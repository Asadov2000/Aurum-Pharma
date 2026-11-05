"""Trusted stock reconstruction is ordered, exact and independent of financial effects."""

from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID

import pytest

from app.domains.pos.retail_sale_event_storage import (
    RetailSaleEventAnchor,
    build_retail_sale_event_anchor,
)
from app.domains.pos.retail_sale_events import (
    RetailCheckoutResult,
    RetailSaleStreamPosition,
    build_retail_sale_event,
)
from app.domains.sync.physical_read_snapshot import PhysicalLotRecord, PhysicalRecord
from app.domains.sync.physical_stock_events import (
    PhysicalStockChange,
    PhysicalStockConfig,
    PhysicalStockEventAnchor,
    build_physical_stock_event,
    build_physical_stock_event_anchor,
)
from app.domains.sync.physical_stock_projection import (
    PhysicalStockBaselineAnchor,
    PhysicalStockProjection,
    physical_baseline_configuration_hash,
    physical_baseline_records_hash,
    reduce_physical_stock_projection,
)
from app.domains.sync.schemas import SyncEventEnvelope
from tests.domains.pos.test_retail_sale_events import result as checkout_result
from tests.domains.sync.test_physical_read_snapshot import physical_config, physical_lot
from tests.domains.sync.test_physical_stock_events import PROJECTION, SOURCE, TIME


def baseline() -> tuple[tuple[PhysicalRecord, ...], tuple[PhysicalStockConfig, ...]]:
    config = physical_config()
    records: tuple[PhysicalRecord, ...] = (config, physical_lot())
    configs = (
        PhysicalStockConfig(
            catalog_id=config.id,
            revision=1,
            base_price=Decimal("23.45"),
            preparation=config.preparation,
        ),
    )
    return records, configs


def checkpoint() -> PhysicalStockBaselineAnchor:
    records, configs = baseline()
    return PhysicalStockBaselineAnchor(
        snapshot_id=UUID(int=101),
        snapshot_hash="c" * 64,
        physical_records_hash=physical_baseline_records_hash(records),
        configuration_hash=physical_baseline_configuration_hash(configs),
        tenant_id=physical_config().tenant_id,
        branch_id=physical_config().branch_id,
        target_edge_node_id=UUID(int=102),
        origin_node_id=UUID(int=90),
        writer_epoch=2,
        sequence=2,
        source_checksum=SOURCE,
        projection_checksum=PROJECTION,
    )


def stock_change(kind: str = "dispense", *, number: int = 1) -> PhysicalStockChange:
    lot = physical_lot()
    fields: dict[str, object] = {
        "event_id": UUID(int=200 + number),
        "operation_id": UUID(int=300 + number),
        "tenant_id": lot.tenant_id,
        "branch_id": lot.branch_id,
        "source_kind": kind,
        "source_id": UUID(int=400 + number),
        "occurred_at": TIME,
    }
    if kind in ("dispense", "writeoff"):
        fields["movements"] = (
            {
                "id": UUID(int=500 + number),
                "line_id": UUID(int=600 + number),
                "entry_order": 0,
                "lot_id": lot.id,
                "catalog_id": lot.catalog_id,
                "kind": "writeoff" if kind == "writeoff" else "pack_take",
                "closed_delta": -1,
                "units_taken": 30,
                "sealed_start": 3,
                "sealed_count": 1,
                "cost_unit_offset": 0,
                "cost_original_pack_cost": "10.00",
                "cost_amount": "10.00",
                "cost_version": 1,
            },
        )
    elif kind == "catalog_config":
        config = physical_config().preparation
        assert config is not None
        fields["configuration"] = PhysicalStockConfig(
            catalog_id=lot.catalog_id,
            revision=2,
            base_price=Decimal("99.00"),
            preparation=config.model_copy(update={"units_per_pack": 60}),
        )
    elif kind == "block":
        fields["blocking"] = {
            "lot_id": lot.id,
            "catalog_id": lot.catalog_id,
            "is_blocked": True,
            "revision": 1,
        }
    elif kind == "baseline_required":
        fields["invalidation"] = {
            "source_table": "unit_stock_operation",
            "source_id": UUID(int=91),
        }
    return PhysicalStockChange.model_validate(fields)


Stream = tuple[
    tuple[SyncEventEnvelope, ...],
    dict[UUID, PhysicalStockEventAnchor],
    dict[UUID, RetailSaleEventAnchor],
]


def stream(*sources: PhysicalStockChange | RetailCheckoutResult) -> Stream:
    anchor = checkpoint()
    source_checksum, projection_checksum = anchor.source_checksum, anchor.projection_checksum
    envelopes: list[SyncEventEnvelope] = []
    stocks: dict[UUID, PhysicalStockEventAnchor] = {}
    retail: dict[UUID, RetailSaleEventAnchor] = {}
    for index, source in enumerate(sources, start=anchor.sequence + 1):
        position = RetailSaleStreamPosition(
            origin_node_id=anchor.origin_node_id,
            writer_epoch=anchor.writer_epoch,
            sequence=index,
            previous_source_checksum=source_checksum,
            previous_projection_checksum=projection_checksum,
        )
        if isinstance(source, PhysicalStockChange):
            envelope = build_physical_stock_event(source, position=position)
            stocks[envelope.event_id] = build_physical_stock_event_anchor(
                envelope,
                previous_source_checksum=source_checksum,
                previous_projection_checksum=projection_checksum,
            )
        else:
            envelope = build_retail_sale_event(source, position=position)
            retail[envelope.event_id] = build_retail_sale_event_anchor(
                envelope,
                previous_source_checksum=source_checksum,
                previous_projection_checksum=projection_checksum,
            )
        envelopes.append(envelope)
        source_checksum, projection_checksum = (
            envelope.stream_checksum,
            envelope.projection_checksum,
        )
    return tuple(envelopes), stocks, retail


def reduce(
    bundle: Stream,
    *,
    records: Sequence[PhysicalRecord] | None = None,
    configs: Sequence[PhysicalStockConfig] | None = None,
    anchor: PhysicalStockBaselineAnchor | None = None,
) -> PhysicalStockProjection:
    baseline_records, baseline_configs = baseline()
    events, stock_anchors, retail_anchors = bundle
    return reduce_physical_stock_projection(
        baseline_records=baseline_records if records is None else records,
        baseline_configuration=baseline_configs if configs is None else configs,
        anchor=anchor or checkpoint(),
        events=events,
        stock_anchors=stock_anchors,
        retail_anchors=retail_anchors,
    )


def projected_lot(projection: PhysicalStockProjection) -> PhysicalLotRecord:
    return next(row for row in projection.physical_records if isinstance(row, PhysicalLotRecord))


def test_empty_stream_keeps_the_independently_trusted_baseline() -> None:
    result = reduce(stream())
    assert result.ready and result.last_sequence == checkpoint().sequence
    assert projected_lot(result) == physical_lot()
    assert result.configuration == baseline()[1]


@pytest.mark.parametrize("kind", ["dispense", "writeoff"])
def test_sealed_consumption_subtracts_original_units_and_money_exactly_once(kind: str) -> None:
    bundle = stream(stock_change(kind))
    before = baseline()
    result = reduce(bundle)
    lot = projected_lot(result)
    assert result.ready and result.last_sequence == 3
    assert lot.closed_packs == 0 and lot.opened_packs == physical_lot().opened_packs
    assert lot.cost.initial_cost == Decimal("30.00")
    assert lot.cost.remaining_cost == Decimal("4.33")
    events, stock_anchors, retail_anchors = bundle
    replayed = reduce((events + events, stock_anchors, retail_anchors))
    assert replayed == result and baseline() == before


def test_later_catalog_packaging_and_price_never_reprice_existing_lots() -> None:
    result = reduce(stream(stock_change("catalog_config"), stock_change("writeoff", number=2)))
    assert result.configuration[0].base_price == Decimal("99.00")
    assert result.configuration[0].preparation is not None
    assert result.configuration[0].preparation.units_per_pack == 60
    lot = projected_lot(result)
    assert lot.packaging.units_per_pack == 30
    assert lot.purchase_cost_per_pack == Decimal("10.00")
    assert lot.cost.remaining_cost == Decimal("4.33")


def test_blocking_changes_only_current_availability_and_its_revision() -> None:
    lot = projected_lot(reduce(stream(stock_change("block"))))
    assert lot.is_blocked and lot.blocking_revision == 1
    assert lot.model_copy(update={"is_blocked": False, "blocking_revision": 0}) == physical_lot()


def test_cancellation_has_no_stock_or_money_effect() -> None:
    projection = reduce(stream(stock_change("writeoff_cancelled")))
    assert projection.ready and projected_lot(projection) == physical_lot()


@pytest.mark.parametrize("first", ["writeoff", "writeoff_cancelled"])
def test_writeoff_and_cancellation_cannot_both_take_effect(first: str) -> None:
    second = "writeoff" if first == "writeoff_cancelled" else "writeoff_cancelled"
    original = stock_change(first)
    conflicting = stock_change(second, number=2).model_copy(
        update={"source_id": original.source_id}
    )
    with pytest.raises(ValueError, match="cannot"):
        reduce(stream(original, conflicting))


@pytest.mark.parametrize(
    "field", ["tenant_id", "branch_id", "origin_node_id", "writer_epoch", "sequence"]
)
def test_wrong_scope_or_noncontiguous_sequence_cannot_move_the_checkpoint(field: str) -> None:
    events, stocks, retail = stream(stock_change())
    value = 99 if field in ("writer_epoch", "sequence") else UUID(int=999)
    forged = events[0].model_copy(update={field: value})
    with pytest.raises(ValueError):
        reduce(((forged,), stocks, retail))


@pytest.mark.parametrize(
    "target", ["baseline-records", "baseline-config", "event-anchor", "prior-chain"]
)
def test_independent_baseline_and_event_trust_cannot_be_rebuilt_from_input(target: str) -> None:
    events, stocks, retail = stream(stock_change())
    if target == "baseline-records":
        config, lot = baseline()[0]
        with pytest.raises(ValueError, match="hash"):
            reduce(
                (events, stocks, retail),
                records=(config, lot.model_copy(update={"is_blocked": True})),
            )
    elif target == "baseline-config":
        with pytest.raises(ValueError, match="hash"):
            reduce(
                (events, stocks, retail),
                configs=(baseline()[1][0].model_copy(update={"base_price": Decimal("1.00")}),),
            )
    elif target == "event-anchor":
        with pytest.raises(ValueError, match="anchor"):
            reduce((events, {}, retail))
    else:
        stocks[events[0].event_id] = stocks[events[0].event_id].model_copy(
            update={"previous_source_checksum": "0" * 64}
        )
        with pytest.raises(ValueError, match="checkpoint"):
            reduce((events, stocks, retail))


@pytest.mark.parametrize(
    "field,value",
    [
        ("sealed_start", 2),
        ("cost_amount", "9.99"),
        ("cost_original_pack_cost", "9.99"),
        ("cost_unit_offset", 1),
    ],
)
def test_source_facts_cannot_reuse_pack_ordinals_or_invent_cost(field: str, value: object) -> None:
    source = stock_change()
    payload = source.model_dump(mode="python")
    movement = source.movements[0].model_dump(mode="python")
    movement[field] = value
    payload["movements"] = (movement,)
    changed = PhysicalStockChange.model_validate(payload)
    with pytest.raises(ValueError):
        reduce(stream(changed))


@pytest.mark.parametrize("part", ["retail-only", "physical-only", "both"])
@pytest.mark.parametrize("kind", ["physical", "mixed"])
def test_checkout_stock_companion_is_required_and_financial_event_never_applies_stock_twice(
    part: str, kind: str
) -> None:
    checkout = checkout_result(kind)
    checkout = checkout.model_copy(
        update={"tenant_id": physical_config().tenant_id, "branch_id": physical_config().branch_id}
    )
    source = stock_change().model_copy(
        update={
            "source_id": checkout.stock_command_id,
            "source_owner": "checkout",
            "checkout_sale_id": checkout.sale_id,
            "checkout_event_id": checkout.event_id,
        }
    )
    if part == "retail-only":
        projection = reduce(stream(checkout))
        assert projected_lot(projection) == physical_lot()
    elif part == "physical-only":
        projection = reduce(stream(source))
    else:
        projection = reduce(stream(checkout, source))
    assert projection.ready == (part == "both")
    assert projection.pending_checkout_sale_ids == (() if part == "both" else (checkout.sale_id,))
    if part != "retail-only":
        assert projected_lot(projection).cost.remaining_cost == Decimal("4.33")


def test_changed_same_event_replay_is_rejected_without_touching_source_objects() -> None:
    events, stocks, retail = stream(stock_change())
    before = baseline()
    altered = events[0].model_copy(update={"payload_hash": "0" * 64})
    with pytest.raises(ValueError, match="replay"):
        reduce(((*events, altered), stocks, retail))
    assert baseline() == before


@pytest.mark.parametrize(
    "table",
    ["unit_stock_operation", "unit_stock_entry", "unit_stock_lot", "unit_stock_opened_pack"],
)
def test_legacy_physical_mutation_requires_a_new_baseline_without_inventing_v2_effects(
    table: str,
) -> None:
    source = stock_change("baseline_required")
    assert source.invalidation is not None
    source = source.model_copy(
        update={"invalidation": source.invalidation.model_copy(update={"source_table": table})}
    )
    events, stocks, retail = stream(source)
    projection = reduce((events + events, stocks, retail))
    assert not projection.ready
    assert projection.baseline_required_event_ids == (source.event_id,)
    assert projected_lot(projection) == physical_lot()
    assert projection.last_sequence == events[0].sequence


def test_configuration_can_advance_again_without_reinterpreting_previous_lot() -> None:
    first = stock_change("catalog_config")
    assert first.configuration is not None
    second = first.model_copy(
        update={
            "event_id": UUID(int=811),
            "operation_id": UUID(int=812),
            "configuration": first.configuration.model_copy(
                update={"revision": 3, "base_price": Decimal("70.00")}
            ),
        }
    )
    projection = reduce(stream(first, second))
    assert projection.configuration[0].revision == 3
    assert projected_lot(projection) == physical_lot()


def opened_source() -> PhysicalStockChange:
    source = stock_change()
    lot = physical_lot()
    opening: dict[str, object] = {
        "id": UUID(int=901),
        "line_id": UUID(int=904),
        "entry_order": 0,
        "lot_id": lot.id,
        "catalog_id": lot.catalog_id,
        "pack_id": UUID(int=902),
        "pack_ordinal": 3,
        "opened_at": TIME,
        "kind": "pack_open",
        "closed_delta": -1,
        "units_taken": 0,
        "before_state": {},
        "after_state": {"intact_blisters": (1, 2, 3)},
    }
    taking = {
        **opening,
        "id": UUID(int=903),
        "entry_order": 1,
        "kind": "unit_take",
        "closed_delta": 0,
        "units_taken": 1,
        "before_state": opening["after_state"],
        "after_state": {
            "intact_blisters": (2, 3),
            "opened_blisters": ({"number": 1, "remaining_units": 9},),
        },
        "cost_version": 1,
        "cost_unit_offset": 0,
        "cost_original_pack_cost": "10.00",
        "cost_amount": "0.33",
    }
    return PhysicalStockChange.model_validate(
        {**source.model_dump(), "movements": (opening, taking)}
    )


def test_opening_and_consumption_preserve_real_identity_before_after_and_exact_cost() -> None:
    source = opened_source()
    events, stocks, retail = stream(source)
    projection = reduce((events + events, stocks, retail))
    lot = projected_lot(projection)
    assert lot.closed_packs == 0 and lot.cost.remaining_cost == Decimal("14.00")
    assert [(pack.id, pack.pack_ordinal, pack.remaining_units) for pack in lot.opened_packs] == [
        (physical_lot().opened_packs[0].id, 2, 13),
        (UUID(int=902), 3, 29),
    ]


@pytest.mark.parametrize(
    "mutation", ["before", "ordinal", "identity", "opening-money", "delta", "kind", "line"]
)
def test_rehashed_opened_movements_cannot_substitute_identity_state_or_kind(mutation: str) -> None:
    payload = opened_source().model_dump(mode="python")
    opening, taking = (movement.model_dump() for movement in opened_source().movements)
    if mutation == "before":
        taking["before_state"] = {
            "intact_blisters": (1, 2),
            "opened_blisters": ({"number": 3, "remaining_units": 9},),
        }
    elif mutation == "ordinal":
        taking["pack_ordinal"] = 2
    elif mutation == "identity":
        opening["pack_id"] = physical_lot().opened_packs[0].id
    elif mutation == "opening-money":
        opening["cost_amount"] = Decimal("0.01")
    elif mutation == "delta":
        taking["units_taken"] = 2
    elif mutation == "line":
        taking["line_id"] = None
    else:
        taking["kind"] = "pack_take"
    payload["movements"] = (opening, taking)
    changed = PhysicalStockChange.model_validate(payload)
    with pytest.raises(ValueError):
        reduce(stream(changed))
