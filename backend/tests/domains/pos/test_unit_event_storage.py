"""Storage representation integrity; no persistence or event application is enabled."""

import json
from copy import deepcopy
from decimal import Decimal
from typing import cast

import pytest

from app.domains.pos.unit_contracts import CheckoutResultV2
from app.domains.pos.unit_event_storage import (
    UnitSaleEventAnchorV2,
    build_unit_sale_event_anchor,
    decode_unit_sale_event,
    encode_unit_sale_event,
)
from app.domains.pos.unit_events import build_unit_sale_event
from app.domains.sync.integrity import canonical_json_bytes, canonical_json_hash
from app.domains.sync.schemas import SyncEventEnvelope
from app.domains.sync.service import SyncEdgeApplyService, _EnvelopeError
from tests.domains.pos.test_unit_contracts import result, uid
from tests.domains.pos.test_unit_events import PROJECTION, SOURCE, event, position, rehash


def anchor(envelope: SyncEventEnvelope | None = None) -> UnitSaleEventAnchorV2:
    return build_unit_sale_event_anchor(
        event() if envelope is None else envelope,
        previous_source_checksum=SOURCE,
        previous_projection_checksum=PROJECTION,
    )


def record() -> bytes:
    return encode_unit_sale_event(event(), anchor=anchor())


def record_payload() -> dict[str, object]:
    return cast(dict[str, object], json.loads(record()))


def test_full_roundtrip_is_canonical_and_returns_frozen_mixed_result() -> None:
    saved = record()
    payload = record_payload()
    assert saved == canonical_json_bytes(payload)
    assert set(payload) == {
        "storage_version",
        "envelope",
        "previous_source_checksum",
        "previous_projection_checksum",
        "event_fingerprint",
    }
    assert payload["storage_version"] == 1
    assert payload["event_fingerprint"] == anchor().event_fingerprint
    restored = decode_unit_sale_event(saved, anchor=anchor())
    assert restored == result()
    assert str(restored.lines[0].quantity) == "1.250"
    assert type(restored.lines[1].quantity) is int and restored.lines[1].quantity == 3
    assert isinstance(restored.lines, tuple) and isinstance(restored.payments, tuple)
    with pytest.raises(ValueError):
        restored.total_amount = Decimal("0.00")
    trusted_anchor = anchor()
    with pytest.raises(ValueError):
        trusted_anchor.sequence = 9


def test_record_formatting_and_key_order_are_not_authority() -> None:
    payload = record_payload()
    reversed_payload = dict(reversed(tuple(payload.items())))
    alternate = json.dumps(reversed_payload, indent=3, ensure_ascii=True).encode()
    assert alternate != record()
    assert decode_unit_sale_event(alternate, anchor=anchor()) == result()


def test_original_payload_mutation_cannot_change_encoded_or_restored_value() -> None:
    envelope = event()
    trusted = anchor(envelope)
    saved = encode_unit_sale_event(envelope, anchor=trusted)
    envelope.payload["total_amount"] = "999.00"
    assert decode_unit_sale_event(saved, anchor=trusted) == result()
    with pytest.raises(ValueError):
        encode_unit_sale_event(envelope, anchor=trusted)
    first = decode_unit_sale_event(saved, anchor=trusted)
    second = decode_unit_sale_event(saved, anchor=trusted)
    assert first.lines[0] is not second.lines[0]
    changed_dump = first.model_dump(mode="json")
    changed_dump["total_amount"] = "999.00"
    assert first == second == result()


@pytest.mark.parametrize(
    "field", ["tenant_id", "branch_id", "origin_node_id", "writer_epoch", "sequence"]
)
def test_external_anchor_scope_is_checked_on_encode_and_decode(field: str) -> None:
    changed: object = 10 if field in {"writer_epoch", "sequence"} else uid(99)
    wrong = anchor().model_copy(update={field: changed})
    with pytest.raises(ValueError):
        encode_unit_sale_event(event(), anchor=wrong)
    with pytest.raises(ValueError):
        decode_unit_sale_event(record(), anchor=wrong)


@pytest.mark.parametrize(
    "field", ["previous_source_checksum", "previous_projection_checksum", "event_fingerprint"]
)
def test_independent_anchor_roots_and_fingerprint_are_required(field: str) -> None:
    wrong = anchor().model_copy(update={field: "f" * 64})
    with pytest.raises(ValueError):
        encode_unit_sale_event(event(), anchor=wrong)
    with pytest.raises(ValueError):
        decode_unit_sale_event(record(), anchor=wrong)
    payload = record_payload()
    payload[field] = "f" * 64
    with pytest.raises(ValueError):
        decode_unit_sale_event(canonical_json_bytes(payload), anchor=anchor())


def test_fully_rewritten_valid_event_and_roots_cannot_replace_original_anchor() -> None:
    payload = result().model_dump(mode="json")
    payload["receipt_number"] = "000002"
    replacement = CheckoutResultV2.model_validate(payload)
    other_position = position().model_copy(
        update={
            "origin_node_id": uid(99),
            "writer_epoch": 3,
            "sequence": 4,
            "previous_source_checksum": "c" * 64,
            "previous_projection_checksum": "d" * 64,
        }
    )
    other = build_unit_sale_event(replacement, position=other_position)
    other_anchor = build_unit_sale_event_anchor(
        other,
        previous_source_checksum="c" * 64,
        previous_projection_checksum="d" * 64,
    )
    rewritten = encode_unit_sale_event(other, anchor=other_anchor)
    assert decode_unit_sale_event(rewritten, anchor=other_anchor) == replacement
    with pytest.raises(ValueError):
        decode_unit_sale_event(rewritten, anchor=anchor())


def test_valid_retail_rewrite_with_unchanged_scope_and_roots_conflicts_with_old_anchor() -> None:
    payload = result().model_dump(mode="json")
    payload["receipt_number"] = "000002"
    replacement = CheckoutResultV2.model_validate(payload)
    other = build_unit_sale_event(replacement, position=position())
    rewritten = encode_unit_sale_event(other, anchor=anchor(other))
    assert decode_unit_sale_event(rewritten, anchor=anchor(other)) == replacement
    with pytest.raises(ValueError):
        decode_unit_sale_event(rewritten, anchor=anchor())


@pytest.mark.parametrize(
    "raw", [b"", b"{", b"[]", b"null", b"\xff", b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}']
)
def test_malformed_records_are_rejected(raw: bytes) -> None:
    with pytest.raises(ValueError):
        decode_unit_sale_event(raw, anchor=anchor())


def test_truncated_and_oversized_records_are_rejected() -> None:
    with pytest.raises(ValueError):
        decode_unit_sale_event(record()[:-1], anchor=anchor())
    oversized = record() + b" " * (1024 * 1024)
    with pytest.raises(ValueError):
        decode_unit_sale_event(oversized, anchor=anchor())


@pytest.mark.parametrize(
    "field", ["storage_version", "previous_source_checksum", "quantity", "schema_version"]
)
def test_duplicate_json_keys_are_rejected_at_every_depth(field: str) -> None:
    text = json.dumps(record_payload())
    value = {
        "storage_version": "1",
        "previous_source_checksum": json.dumps(SOURCE),
        "quantity": '"1.250"',
        "schema_version": "2",
    }[field]
    needle = f'"{field}": {value}'
    assert needle in text
    duplicated = text.replace(needle, f"{needle}, {needle}", 1).encode()
    with pytest.raises(ValueError):
        decode_unit_sale_event(duplicated, anchor=anchor())


@pytest.mark.parametrize("version", [True, "1", 1.0, 0, 2])
def test_storage_version_is_exact(version: object) -> None:
    payload = record_payload()
    payload["storage_version"] = version
    with pytest.raises(ValueError):
        decode_unit_sale_event(json.dumps(payload).encode(), anchor=anchor())


def test_storage_extra_fields_are_rejected() -> None:
    payload = record_payload()
    payload["patient_name"] = "forbidden"
    with pytest.raises(ValueError):
        decode_unit_sale_event(canonical_json_bytes(payload), anchor=anchor())


@pytest.mark.parametrize(
    "field", ["purchase_price", "original_cost_amount", "prescription", "patient_name"]
)
def test_nested_sensitive_extras_are_rejected_even_after_unsigned_rehash(field: str) -> None:
    envelope = event()
    payload = deepcopy(envelope.payload)
    lines = cast(list[dict[str, object]], payload["lines"])
    lines[1][field] = "forbidden"
    forged = rehash(envelope.model_copy(update={"payload": payload}))
    with pytest.raises(ValueError):
        build_unit_sale_event_anchor(
            forged, previous_source_checksum=SOURCE, previous_projection_checksum=PROJECTION
        )
    with pytest.raises(ValueError):
        encode_unit_sale_event(forged, anchor=anchor())
    stored = record_payload()
    stored["envelope"] = forged.model_dump(mode="json")
    stored["event_fingerprint"] = canonical_json_hash(
        {
            "domain": "aurum-pos-unit-sale-event-v2",
            "envelope": forged.model_dump(mode="json"),
        }
    )
    matching_anchor = anchor().model_copy(
        update={
            "event_fingerprint": stored["event_fingerprint"],
        }
    )
    with pytest.raises(ValueError):
        decode_unit_sale_event(canonical_json_bytes(stored), anchor=matching_anchor)


@pytest.mark.parametrize(
    "field,value,index",
    [
        ("quantity", "3", 1),
        ("original_retail_unit_price", "2.000", 0),
    ],
)
def test_payload_wire_types_remain_exact_even_with_matching_recomputed_anchor(
    field: str,
    value: object,
    index: int,
) -> None:
    envelope = event()
    payload = deepcopy(envelope.payload)
    cast(list[dict[str, object]], payload["lines"])[index][field] = value
    forged = rehash(envelope.model_copy(update={"payload": payload}))
    stored = record_payload()
    stored["envelope"] = forged.model_dump(mode="json")
    digest = canonical_json_hash(
        {
            "domain": "aurum-pos-unit-sale-event-v2",
            "envelope": stored["envelope"],
        }
    )
    stored["event_fingerprint"] = digest
    matching_anchor = anchor().model_copy(update={"event_fingerprint": digest})
    with pytest.raises(ValueError):
        decode_unit_sale_event(canonical_json_bytes(stored), anchor=matching_anchor)


@pytest.mark.parametrize("field", ["writer_epoch", "sequence"])
@pytest.mark.parametrize("value", [True, "2", 2.0, 0, -1, 2**63])
def test_anchor_strict_integers_cannot_be_bypassed_by_construct(field: str, value: object) -> None:
    values = anchor().model_dump(mode="python")
    values[field] = value
    with pytest.raises(ValueError):
        UnitSaleEventAnchorV2.model_validate(values)
    forged = UnitSaleEventAnchorV2.model_construct(**values)
    with pytest.raises(ValueError):
        encode_unit_sale_event(event(), anchor=forged)
    with pytest.raises(ValueError):
        decode_unit_sale_event(record(), anchor=forged)


@pytest.mark.parametrize(
    "field", ["previous_source_checksum", "previous_projection_checksum", "event_fingerprint"]
)
@pytest.mark.parametrize("value", ["A" * 64, "a" * 63, "g" * 64, 1, None])
def test_anchor_hashes_are_strict_even_after_model_copy(field: str, value: object) -> None:
    values = anchor().model_dump(mode="python")
    values[field] = value
    with pytest.raises(ValueError):
        UnitSaleEventAnchorV2.model_validate(values)
    forged = anchor().model_copy(update={field: value})
    with pytest.raises(ValueError):
        encode_unit_sale_event(event(), anchor=forged)
    with pytest.raises(ValueError):
        decode_unit_sale_event(record(), anchor=forged)


def test_constructed_nested_quantity_and_v1_envelope_are_rejected() -> None:
    envelope = event()
    payload = deepcopy(envelope.payload)
    cast(list[dict[str, object]], payload["lines"])[1]["quantity"] = True
    forged = SyncEventEnvelope.model_construct(
        **{
            **envelope.model_dump(mode="python"),
            "payload": payload,
        }
    )
    for invalid in (
        forged,
        envelope.model_copy(update={"schema_version": 1, "event_type": "pos.sale.completed.v1"}),
    ):
        with pytest.raises(ValueError):
            encode_unit_sale_event(invalid, anchor=anchor())


def test_restoring_v2_does_not_enable_current_v1_sync_handler() -> None:
    assert decode_unit_sale_event(record(), anchor=anchor()) == result()
    with pytest.raises(_EnvelopeError, match="unsupported_event"):
        SyncEdgeApplyService._validate_event(
            event(),
            previous_source_checksum=SOURCE,
            previous_projection_checksum=PROJECTION,
        )
