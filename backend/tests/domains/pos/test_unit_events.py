"""Pure v2 event integrity without enabling the live sync or payment paths."""

from copy import deepcopy
from datetime import timedelta
from decimal import Decimal
from typing import cast

import pytest

from app.domains.pos.unit_contracts import CheckoutResultV2
from app.domains.pos.unit_events import (
    UnitSaleStreamPositionV2,
    build_unit_sale_event,
    unit_sale_event_fingerprint,
    validate_unit_sale_event,
)
from app.domains.sync.integrity import (
    canonical_json_hash,
    projection_stream_checksum,
    source_stream_checksum,
)
from app.domains.sync.schemas import SyncEventEnvelope
from app.domains.sync.service import SyncEdgeApplyService, _EnvelopeError
from tests.domains.pos.test_unit_contracts import result, uid

SOURCE = "a" * 64
PROJECTION = "b" * 64


def position() -> UnitSaleStreamPositionV2:
    return UnitSaleStreamPositionV2(
        origin_node_id=uid(30),
        writer_epoch=2,
        sequence=3,
        previous_source_checksum=SOURCE,
        previous_projection_checksum=PROJECTION,
    )


def event() -> SyncEventEnvelope:
    return build_unit_sale_event(result(), position=position())


def validate(envelope: SyncEventEnvelope) -> CheckoutResultV2:
    return validate_unit_sale_event(
        envelope, previous_source_checksum=SOURCE, previous_projection_checksum=PROJECTION
    )


def fingerprint(envelope: SyncEventEnvelope) -> str:
    return unit_sale_event_fingerprint(
        envelope, previous_source_checksum=SOURCE, previous_projection_checksum=PROJECTION
    )


def rehash(envelope: SyncEventEnvelope) -> SyncEventEnvelope:
    """An attacker can recompute unsigned checksums, but cannot bypass schemas."""
    digest = canonical_json_hash(envelope.payload)
    source = source_stream_checksum(
        previous_checksum=SOURCE,
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
        payload_hash=digest,
    )
    projection = projection_stream_checksum(
        previous_checksum=PROJECTION,
        origin_node_id=envelope.origin_node_id,
        writer_epoch=envelope.writer_epoch,
        sequence=envelope.sequence,
        sale_id=envelope.aggregate_id,
        projection_hash=digest,
    )
    return envelope.model_copy(
        update={
            "payload_hash": digest,
            "stream_checksum": source,
            "projection_hash": digest,
            "projection_checksum": projection,
        }
    )


def payload_lines(payload: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], payload["lines"])


def test_event_preserves_mixed_formats_and_all_original_retail_facts() -> None:
    original = result()
    envelope = build_unit_sale_event(original, position=position())
    restored = validate(envelope)
    assert restored == original
    assert envelope.event_type == "pos.sale.completed.v2"
    assert envelope.schema_version == 2 and envelope.aggregate_type == "sale"
    assert envelope.payload == original.model_dump(mode="json")
    legacy, units = payload_lines(envelope.payload)
    assert legacy["inventory_kind"] == "legacy_pack" and legacy["quantity"] == "1.250"
    assert units["inventory_kind"] == "prepared_unit" and type(units["quantity"]) is int
    assert units["quantity"] == units["base_units"] == 3
    assert restored.total_amount == Decimal("5.49")
    roundtrip = SyncEventEnvelope.model_validate_json(envelope.model_dump_json())
    assert validate(roundtrip) == original
    assert fingerprint(roundtrip) == fingerprint(envelope)
    assert fingerprint(envelope) == fingerprint(event())
    assert fingerprint(envelope) != envelope.payload_hash
    assert len(fingerprint(envelope)) == 64


def test_same_catalog_mixed_formats_remain_separate_positions() -> None:
    payload = result().model_dump(mode="json")
    payload_lines(payload)[0]["catalog_id"] = str(uid(4))
    mixed = CheckoutResultV2.model_validate(payload)
    restored = validate(build_unit_sale_event(mixed, position=position()))
    assert len(restored.lines) == 2
    assert restored.lines[0].catalog_id == restored.lines[1].catalog_id
    assert restored.lines[0].quantity == Decimal("1.250")
    assert restored.lines[1].quantity == 3


@pytest.mark.parametrize(
    "field", ["payload_hash", "stream_checksum", "projection_hash", "projection_checksum"]
)
def test_each_checksum_is_independently_verified(field: str) -> None:
    forged = event().model_copy(update={field: "0" * 64})
    with pytest.raises(ValueError):
        validate(forged)
    with pytest.raises(ValueError):
        fingerprint(forged)


@pytest.mark.parametrize("chain", ["source", "projection"])
def test_previous_chain_checksum_is_verified(chain: str) -> None:
    with pytest.raises(ValueError):
        validate_unit_sale_event(
            event(),
            previous_source_checksum="c" * 64 if chain == "source" else SOURCE,
            previous_projection_checksum="c" * 64 if chain == "projection" else PROJECTION,
        )


@pytest.mark.parametrize(
    "field", ["event_id", "tenant_id", "branch_id", "operation_id", "aggregate_id", "occurred_at"]
)
def test_payload_envelope_scope_mismatch_survives_recomputed_hash_attack(field: str) -> None:
    envelope = event()
    changed = envelope.occurred_at + timedelta(seconds=1) if field == "occurred_at" else uid(99)
    forged = rehash(envelope.model_copy(update={field: changed}))
    with pytest.raises(ValueError):
        validate(forged)


@pytest.mark.parametrize(
    "field,value",
    [("event_type", "pos.sale.completed.v1"), ("schema_version", 1), ("aggregate_type", "stock")],
)
def test_v2_adapter_does_not_accept_other_event_contracts(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        validate(rehash(event().model_copy(update={field: value})))


@pytest.mark.parametrize("field", ["origin_node_id", "writer_epoch", "sequence"])
def test_replay_fingerprint_binds_transport_origin_and_position(field: str) -> None:
    original = event()
    changed = uid(99) if field == "origin_node_id" else 4
    other = rehash(original.model_copy(update={field: changed}))
    assert validate(other) == validate(original)
    assert fingerprint(other) != fingerprint(original)


@pytest.mark.parametrize("level", ["root", "legacy", "unit", "packaging", "payment"])
@pytest.mark.parametrize(
    "field", ["original_cost_amount", "purchase_price", "prescription", "patient_name"]
)
def test_extra_procurement_or_recipe_data_is_rejected_even_with_valid_hashes(
    level: str, field: str
) -> None:
    envelope = event()
    payload = deepcopy(envelope.payload)
    target = payload
    if level in {"legacy", "unit", "packaging"}:
        target = payload_lines(payload)[0 if level == "legacy" else 1]
        if level == "packaging":
            target = cast(dict[str, object], target["packaging"])
    elif level == "payment":
        target = cast(list[dict[str, object]], payload["payments"])[0]
    target[field] = "SYNTHETIC-FORBIDDEN"
    with pytest.raises(ValueError):
        validate(rehash(envelope.model_copy(update={"payload": payload})))


@pytest.mark.parametrize("quantity", [True, "3", 3.0, 0, -1])
@pytest.mark.parametrize("field", ["quantity", "base_units"])
def test_recomputed_event_hashes_do_not_allow_coerced_or_invalid_unit_quantity(
    quantity: object,
    field: str,
) -> None:
    envelope = event()
    payload = deepcopy(envelope.payload)
    payload_lines(payload)[1][field] = quantity
    with pytest.raises(ValueError):
        validate(rehash(envelope.model_copy(update={"payload": payload})))


@pytest.mark.parametrize(
    "mutation", ["gap", "order", "packaging", "total", "noncanonical_money", "legacy_price"]
)
def test_invalid_semantics_or_noncanonical_payload_cannot_be_signed_by_rehashing(
    mutation: str,
) -> None:
    envelope = event()
    payload = deepcopy(envelope.payload)
    lines = payload_lines(payload)
    if mutation == "gap":
        lines[1]["position"] = 3
    elif mutation == "order":
        payload["lines"] = list(reversed(lines))
    elif mutation == "packaging":
        cast(dict[str, object], lines[1]["packaging"])["units_per_pack"] = 31
        lines[1]["sale_format"] = "pack"
    elif mutation == "total":
        payload["total_amount"] = "5.50"
    elif mutation == "noncanonical_money":
        payload["total_amount"] = "5.490"
    else:
        lines[0]["original_retail_unit_price"] = "2.000"
    with pytest.raises(ValueError):
        validate(rehash(envelope.model_copy(update={"payload": payload})))


@pytest.mark.parametrize("field", ["writer_epoch", "sequence"])
@pytest.mark.parametrize("number", [True, "1", 1.0, 0, -1, 2**63])
def test_stream_position_integers_are_strict_and_bounded(field: str, number: object) -> None:
    with pytest.raises(ValueError):
        UnitSaleStreamPositionV2.model_validate(position().model_dump() | {field: number})
    with pytest.raises(ValueError):
        build_unit_sale_event(result(), position=position().model_copy(update={field: number}))
    with pytest.raises(ValueError):
        validate(event().model_copy(update={field: number}))


@pytest.mark.parametrize("field", ["previous_source_checksum", "previous_projection_checksum"])
@pytest.mark.parametrize("digest", ["A" * 64, "a" * 63, "g" * 64, 123, None])
def test_stream_position_checksums_are_strict(field: str, digest: object) -> None:
    with pytest.raises(ValueError):
        UnitSaleStreamPositionV2.model_validate(position().model_dump() | {field: digest})
    with pytest.raises(ValueError):
        build_unit_sale_event(result(), position=position().model_copy(update={field: digest}))


def test_model_construct_cannot_bypass_nested_event_validation() -> None:
    original = result()
    forged_line = original.lines[1].model_copy(update={"quantity": True})
    forged_result = CheckoutResultV2.model_construct(
        **(original.model_dump() | {"lines": (original.lines[0], forged_line)})
    )
    with pytest.raises(ValueError):
        build_unit_sale_event(forged_result, position=position())
    envelope = event()
    forged_envelope = SyncEventEnvelope.model_construct(
        **(envelope.model_dump() | {"schema_version": True})
    )
    with pytest.raises(ValueError):
        validate(forged_envelope)


@pytest.mark.parametrize("field", ["register_id", "shift_id", "cashier_id", "receipt_number"])
def test_replay_fingerprint_binds_retail_identity_not_present_in_the_envelope(field: str) -> None:
    payload = result().model_dump(mode="json")
    payload[field] = "000002" if field == "receipt_number" else str(uid(99))
    changed = CheckoutResultV2.model_validate(payload)
    other = build_unit_sale_event(changed, position=position())
    assert validate(other) == changed
    assert fingerprint(other) != fingerprint(event())


def test_naive_timestamp_is_rejected_even_for_constructed_envelope() -> None:
    envelope = event()
    forged = envelope.model_copy(update={"occurred_at": envelope.occurred_at.replace(tzinfo=None)})
    with pytest.raises(ValueError):
        validate(forged)


def test_live_v1_sync_handler_still_rejects_the_valid_v2_event() -> None:
    envelope = event()
    assert validate(envelope) == result()
    with pytest.raises(_EnvelopeError, match="unsupported_event"):
        SyncEdgeApplyService._validate_event(
            envelope,
            previous_source_checksum=SOURCE,
            previous_projection_checksum=PROJECTION,
        )
