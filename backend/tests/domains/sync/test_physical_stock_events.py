"""Independent inventory evidence preserves history without issuing stock commands."""

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest

from app.domains.pos.retail_sale_events import RetailSaleStreamPosition
from app.domains.sync.physical_stock_events import (
    EVENT_TYPE,
    MAX_RECORD_BYTES,
    PhysicalPackState,
    PhysicalStockBlock,
    PhysicalStockChange,
    PhysicalStockConfig,
    PhysicalStockEventAnchor,
    PhysicalStockMovement,
    build_physical_stock_event,
    build_physical_stock_event_anchor,
    decode_physical_stock_event,
    encode_physical_stock_event,
    physical_stock_event_fingerprint,
    validate_physical_stock_event,
)
from app.domains.sync.schemas import SyncEventEnvelope
from tests.domains.sync.test_physical_read_snapshot import physical_config, physical_lot

SOURCE = "a" * 64
PROJECTION = "b" * 64
TIME = datetime(2026, 10, 29, 9, 0, tzinfo=UTC)


def position() -> RetailSaleStreamPosition:
    return RetailSaleStreamPosition(
        origin_node_id=UUID(int=90),
        writer_epoch=2,
        sequence=3,
        previous_source_checksum=SOURCE,
        previous_projection_checksum=PROJECTION,
    )


def movement(*, kind: str = "unit_take") -> dict[str, object]:
    return {
        "id": UUID(int=61),
        "entry_order": 0,
        "lot_id": UUID(int=62),
        "catalog_id": UUID(int=63),
        "pack_id": UUID(int=64),
        "kind": kind,
        "closed_delta": -1 if kind == "pack_open" else 0,
        "units_taken": 0 if kind == "pack_open" else 1,
        "before_state": {"loose_units": 0 if kind == "pack_open" else 30},
        "after_state": {"loose_units": 30 if kind == "pack_open" else 29},
        "pack_ordinal": 1,
        "opened_at": TIME,
        "cost_unit_offset": None if kind == "pack_open" else 0,
        "cost_original_pack_cost": None if kind == "pack_open" else Decimal("10.00"),
        "cost_amount": None if kind == "pack_open" else Decimal("0.33"),
        "cost_version": None if kind == "pack_open" else 1,
    }


def change(kind: str = "catalog_config") -> PhysicalStockChange:
    config = physical_config()
    fields: dict[str, object] = {
        "event_id": UUID(int=71),
        "operation_id": UUID(int=72),
        "tenant_id": config.tenant_id,
        "branch_id": config.branch_id,
        "source_kind": kind,
        "source_id": config.id,
        "occurred_at": TIME,
    }
    if kind == "catalog_config":
        fields["configuration"] = PhysicalStockConfig(
            catalog_id=config.id,
            revision=1,
            base_price=Decimal("23.45"),
            preparation=config.preparation,
        )
    elif kind == "block":
        fields["blocking"] = PhysicalStockBlock(
            lot_id=UUID(int=62), catalog_id=config.id, is_blocked=True, revision=1
        )
    elif kind in ("initialize", "legacy_transition"):
        fields["initializations"] = (physical_lot(),)
    elif kind in ("dispense", "writeoff"):
        fields["movements"] = (movement(kind="writeoff" if kind == "writeoff" else "unit_take"),)
    elif kind == "baseline_required":
        fields["invalidation"] = {
            "source_table": "unit_stock_operation",
            "source_id": UUID(int=91),
        }
    return PhysicalStockChange.model_validate(fields)


def event(source: PhysicalStockChange | None = None) -> SyncEventEnvelope:
    return build_physical_stock_event(source or change(), position=position())


def anchor(envelope: SyncEventEnvelope) -> PhysicalStockEventAnchor:
    return build_physical_stock_event_anchor(
        envelope, previous_source_checksum=SOURCE, previous_projection_checksum=PROJECTION
    )


def validate(envelope: SyncEventEnvelope) -> PhysicalStockChange:
    return validate_physical_stock_event(
        envelope, previous_source_checksum=SOURCE, previous_projection_checksum=PROJECTION
    )


@pytest.mark.parametrize(
    "kind",
    [
        "initialize",
        "legacy_transition",
        "dispense",
        "writeoff",
        "writeoff_cancelled",
        "block",
        "catalog_config",
        "baseline_required",
    ],
)
def test_every_source_roundtrips_exact_history_in_one_nonfinancial_event(kind: str) -> None:
    original = change(kind)
    before = original.model_dump(mode="json")
    envelope = event(original)
    assert envelope.event_type == EVENT_TYPE and envelope.schema_version == 5
    assert envelope.aggregate_type == "physical_stock"
    assert envelope.aggregate_id == original.source_id
    assert envelope.operation_id != original.source_id
    assert envelope.payload == before
    assert validate(envelope) == original
    encoded = encode_physical_stock_event(envelope, anchor=anchor(envelope))
    assert decode_physical_stock_event(encoded, anchor=anchor(envelope)) == original
    assert encode_physical_stock_event(envelope, anchor=anchor(envelope)) == encoded
    assert original.model_dump(mode="json") == before


@pytest.mark.parametrize(
    "field",
    ["event_id", "tenant_id", "branch_id", "operation_id", "aggregate_id", "origin_node_id"],
)
def test_envelope_identity_cannot_be_detached_from_source_or_chain(field: str) -> None:
    forged = event().model_copy(update={field: UUID(int=999)})
    with pytest.raises(ValueError):
        validate(forged)


@pytest.mark.parametrize(
    "field", ["payload_hash", "projection_hash", "stream_checksum", "projection_checksum"]
)
def test_changed_hash_or_chain_is_refused(field: str) -> None:
    with pytest.raises(ValueError):
        validate(event().model_copy(update={field: "0" * 64}))


def test_recomputed_unsigned_hashes_cannot_replace_independently_trusted_history() -> None:
    original = event()
    payload = change().model_dump(mode="python")
    config = change().configuration
    assert config is not None
    payload["configuration"] = config.model_copy(update={"base_price": Decimal("99.00")})
    rewritten = event(PhysicalStockChange.model_validate(payload))
    assert validate(rewritten).configuration != change().configuration
    with pytest.raises(ValueError, match="anchor"):
        encode_physical_stock_event(rewritten, anchor=anchor(original))
    rewritten_record = encode_physical_stock_event(rewritten, anchor=anchor(rewritten))
    with pytest.raises(ValueError, match="anchor"):
        decode_physical_stock_event(rewritten_record, anchor=anchor(original))


@pytest.mark.parametrize(
    "field",
    ["tenant_id", "branch_id", "origin_node_id", "writer_epoch", "sequence", "event_fingerprint"],
)
def test_external_anchor_scope_and_fingerprint_are_mandatory(field: str) -> None:
    envelope = event()
    updates: dict[str, object] = {field: UUID(int=999)}
    if field in ("writer_epoch", "sequence"):
        updates[field] = 99
    elif field == "event_fingerprint":
        updates[field] = "0" * 64
    with pytest.raises(ValueError):
        encode_physical_stock_event(envelope, anchor=anchor(envelope).model_copy(update=updates))


@pytest.mark.parametrize(
    "record",
    [b"", b"\xff", b"null", b"{}", b"[1]", b"{" + b" " * MAX_RECORD_BYTES],
    ids=["empty", "invalid-utf8", "null", "empty-object", "array", "oversized"],
)
def test_untrusted_storage_is_bounded_utf8_and_typed(record: bytes) -> None:
    with pytest.raises(ValueError):
        decode_physical_stock_event(record, anchor=anchor(event()))


@pytest.mark.parametrize("version", [True, "1", 1.0, 2])
def test_storage_version_never_coerces_boolean_string_or_float(version: object) -> None:
    envelope = event()
    raw = json.loads(encode_physical_stock_event(envelope, anchor=anchor(envelope)))
    raw["storage_version"] = version
    with pytest.raises(ValueError):
        decode_physical_stock_event(json.dumps(raw).encode(), anchor=anchor(envelope))


def test_duplicate_json_keys_and_nonfinite_numbers_are_refused() -> None:
    for record in (b'{"storage_version":1,"storage_version":1}', b'{"x":NaN}'):
        with pytest.raises(ValueError):
            decode_physical_stock_event(record, anchor=anchor(event()))


def test_exact_decimal_history_and_utc_survive_host_rounding_and_time_zone() -> None:
    source = change().model_copy(
        update={"occurred_at": TIME.astimezone(timezone(timedelta(hours=3)))}
    )
    restored = validate(event(source))
    assert restored.occurred_at == TIME and restored.occurred_at.tzinfo == UTC
    config = restored.configuration
    assert config is not None and config.base_price == Decimal("23.45")
    assert event(restored).payload["configuration"]["base_price"] == "23.45"


@pytest.mark.parametrize(
    "field",
    [
        "before_state",
        "after_state",
        "pack_ordinal",
        "opened_at",
        "cost_unit_offset",
        "cost_original_pack_cost",
        "cost_amount",
        "cost_version",
    ],
)
def test_consumption_never_invents_missing_pack_or_cost_evidence(field: str) -> None:
    fields = movement()
    fields[field] = None
    with pytest.raises(ValueError):
        PhysicalStockMovement.model_validate(fields)


def test_opening_without_consumption_keeps_pack_and_requires_no_money_snapshot() -> None:
    opened = PhysicalStockMovement.model_validate(movement(kind="pack_open"))
    assert opened.closed_delta == -1 and opened.units_taken == 0
    assert opened.cost_amount is None and opened.cost_version is None
    assert opened.before_state == PhysicalPackState(loose_units=0)
    assert opened.after_state == PhysicalPackState(loose_units=30)


@pytest.mark.parametrize("owner", ["standalone", "checkout"])
def test_sale_companion_binding_cannot_be_partial_or_attached_to_standalone(owner: str) -> None:
    payload = change("dispense").model_dump(mode="python")
    payload.update(source_owner=owner, checkout_sale_id=UUID(int=1))
    with pytest.raises(ValueError):
        PhysicalStockChange.model_validate(payload)


def test_bound_sale_companion_retains_same_exact_movements_without_new_financial_document() -> None:
    payload = change("dispense").model_dump(mode="python")
    payload.update(
        source_owner="checkout", checkout_sale_id=UUID(int=81), checkout_event_id=UUID(int=82)
    )
    source = PhysicalStockChange.model_validate(payload)
    restored = decode_physical_stock_event(
        encode_physical_stock_event(event(source), anchor=anchor(event(source))),
        anchor=anchor(event(source)),
    )
    assert restored == source and restored.movements == change("dispense").movements
    assert "payments" not in event(source).payload and "total_amount" not in event(source).payload


@pytest.mark.parametrize("mutation", ["missing", "reordered", "duplicate", "wrong-kind"])
def test_complete_ordered_movement_set_is_required(mutation: str) -> None:
    payload = change("dispense").model_dump(mode="python")
    first = movement()
    second = deepcopy(first)
    second.update(id=UUID(int=65), entry_order=1)
    if mutation == "missing":
        payload["movements"] = ()
    elif mutation == "reordered":
        payload["movements"] = (second, first)
    elif mutation == "duplicate":
        second["id"] = first["id"]
        payload["movements"] = (first, second)
    else:
        first["kind"] = "writeoff"
        payload["movements"] = (first,)
    with pytest.raises(ValueError):
        PhysicalStockChange.model_validate(payload)


def test_fingerprint_is_stable_after_serialization() -> None:
    envelope = event()
    restored = SyncEventEnvelope.model_validate_json(envelope.model_dump_json())
    assert (
        physical_stock_event_fingerprint(
            restored, previous_source_checksum=SOURCE, previous_projection_checksum=PROJECTION
        )
        == anchor(envelope).event_fingerprint
    )
