"""Bounded retail-event representation with an independently supplied anchor."""

import json
from copy import deepcopy
from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest

from app.domains.pos import retail_sale_event_storage as storage
from app.domains.pos.retail_sale_event_storage import (
    RetailSaleEventAnchor,
    build_retail_sale_event_anchor,
    decode_retail_sale_event,
    encode_retail_sale_event,
)
from app.domains.pos.retail_sale_events import build_retail_sale_event
from app.domains.pos.unit_event_storage import decode_unit_sale_event
from app.domains.sync.integrity import canonical_json_bytes, canonical_json_hash
from app.domains.sync.schemas import SyncEventEnvelope
from app.domains.sync.service import SyncEdgeApplyService, _EnvelopeError
from tests.domains.pos.test_retail_sale_events import (
    PROJECTION,
    SOURCE,
    Kind,
    event,
    position,
    rehash,
    result,
    rows,
)
from tests.domains.pos.test_unit_event_storage import anchor as prepared_anchor
from tests.domains.pos.test_unit_event_storage import record as prepared_record


def anchor(
    kind: Kind = "mixed", envelope: SyncEventEnvelope | None = None
) -> RetailSaleEventAnchor:
    return build_retail_sale_event_anchor(
        event(kind) if envelope is None else envelope,
        previous_source_checksum=SOURCE,
        previous_projection_checksum=PROJECTION,
    )


def record(kind: Kind = "mixed") -> bytes:
    return encode_retail_sale_event(event(kind), anchor=anchor(kind))


def payload(kind: Kind = "mixed") -> dict[str, object]:
    return cast(dict[str, object], json.loads(record(kind)))


@pytest.mark.parametrize("kind", ["physical", "mixed"])
def test_roundtrip_preserves_canonical_wire_facts_and_frozen_independent_results(
    kind: Kind,
) -> None:
    saved = record(kind)
    stored = payload(kind)
    assert saved == canonical_json_bytes(stored)
    assert stored["record_kind"] == "retail_sale_event"
    assert stored["storage_version"] == 1
    assert set(stored) == {
        "record_kind",
        "storage_version",
        "envelope",
        "previous_source_checksum",
        "previous_projection_checksum",
        "event_fingerprint",
    }
    first = decode_retail_sale_event(saved, anchor=anchor(kind))
    second = decode_retail_sale_event(saved, anchor=anchor(kind))
    assert first == second == result(kind)
    assert first.lines[-1] is not second.lines[-1]
    assert first.lines[-1].quantity == 2 and type(first.lines[-1].quantity) is int
    assert first.lines[-1].base_units == 20
    assert first.lines[-1].sale_format == "blister"
    assert first.completed_at.isoformat() == "2026-10-07T05:01:00+00:00"
    if kind == "mixed":
        assert str(first.lines[0].quantity) == "0.001"
    with pytest.raises(ValueError):
        first.total_amount = Decimal("0.00")
    trusted = anchor(kind)
    with pytest.raises(ValueError):
        trusted.sequence = 9


@pytest.mark.parametrize("kind", ["physical", "mixed"])
def test_outer_json_formatting_and_key_order_are_not_authority(kind: Kind) -> None:
    reordered = dict(reversed(tuple(payload(kind).items())))
    alternate = json.dumps(reordered, indent=2, ensure_ascii=True).encode()
    assert alternate != record(kind)
    assert decode_retail_sale_event(alternate, anchor=anchor(kind)) == result(kind)


@pytest.mark.parametrize("kind", ["physical", "mixed"])
def test_test_flag_survives_storage_without_conversion_to_a_real_sale(kind: Kind) -> None:
    original = result(kind).model_copy(update={"is_test": True})
    envelope = build_retail_sale_event(original, position=position())
    trusted = anchor(envelope=envelope)
    saved = encode_retail_sale_event(envelope, anchor=trusted)
    assert decode_retail_sale_event(saved, anchor=trusted) == original
    assert decode_retail_sale_event(saved, anchor=trusted).is_test is True


def test_input_mutation_cannot_change_previously_encoded_or_decoded_facts() -> None:
    envelope = event()
    trusted = anchor(envelope=envelope)
    saved = encode_retail_sale_event(envelope, anchor=trusted)
    envelope.payload["total_amount"] = "999.00"
    assert decode_retail_sale_event(saved, anchor=trusted) == result("mixed")
    with pytest.raises(ValueError):
        encode_retail_sale_event(envelope, anchor=trusted)
    restored = decode_retail_sale_event(saved, anchor=trusted)
    exported = restored.model_dump(mode="json")
    rows(exported)[1]["quantity"] = 1
    assert decode_retail_sale_event(saved, anchor=trusted) == restored


@pytest.mark.parametrize(
    "field", ["tenant_id", "branch_id", "origin_node_id", "writer_epoch", "sequence"]
)
def test_anchor_identity_is_checked_on_encode_and_decode(field: str) -> None:
    value: object = 9 if field in {"writer_epoch", "sequence"} else UUID(int=99)
    changed = anchor().model_copy(update={field: value})
    with pytest.raises(ValueError):
        encode_retail_sale_event(event(), anchor=changed)
    with pytest.raises(ValueError):
        decode_retail_sale_event(record(), anchor=changed)


@pytest.mark.parametrize(
    "field", ["previous_source_checksum", "previous_projection_checksum", "event_fingerprint"]
)
def test_anchor_roots_and_fingerprint_are_independent_of_stored_metadata(field: str) -> None:
    changed = anchor().model_copy(update={field: "c" * 64})
    with pytest.raises(ValueError):
        encode_retail_sale_event(event(), anchor=changed)
    with pytest.raises(ValueError):
        decode_retail_sale_event(record(), anchor=changed)
    stored = payload()
    stored[field] = "c" * 64
    with pytest.raises(ValueError):
        decode_retail_sale_event(canonical_json_bytes(stored), anchor=anchor())


@pytest.mark.parametrize("kind", ["physical", "mixed"])
def test_valid_fully_rehashed_retail_substitution_cannot_replace_independent_original_anchor(
    kind: Kind,
) -> None:
    original = result(kind)
    replacement = original.model_copy(update={"receipt_number": "A-43"})
    other = build_retail_sale_event(replacement, position=position())
    other_anchor = anchor(envelope=other)
    rewritten = encode_retail_sale_event(other, anchor=other_anchor)
    assert other_anchor.event_fingerprint != anchor(kind).event_fingerprint
    assert decode_retail_sale_event(rewritten, anchor=other_anchor) == replacement
    with pytest.raises(ValueError):
        decode_retail_sale_event(rewritten, anchor=anchor(kind))
    with pytest.raises(ValueError):
        encode_retail_sale_event(other, anchor=anchor(kind))


def test_rewritten_origin_position_and_chain_roots_cannot_replace_old_anchor() -> None:
    changed_position = position().model_copy(
        update={
            "origin_node_id": UUID(int=99),
            "writer_epoch": 4,
            "sequence": 5,
            "previous_source_checksum": "c" * 64,
            "previous_projection_checksum": "d" * 64,
        }
    )
    other = build_retail_sale_event(result("mixed"), position=changed_position)
    other_anchor = build_retail_sale_event_anchor(
        other, previous_source_checksum="c" * 64, previous_projection_checksum="d" * 64
    )
    rewritten = encode_retail_sale_event(other, anchor=other_anchor)
    assert decode_retail_sale_event(rewritten, anchor=other_anchor) == result("mixed")
    with pytest.raises(ValueError):
        decode_retail_sale_event(rewritten, anchor=anchor())


@pytest.mark.parametrize(
    "raw",
    [b"", b"{", b"[]", b"null", b"\xff", b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}'],
    ids=["empty", "truncated", "array", "null", "utf8", "nan", "infinity", "negative-infinity"],
)
def test_malformed_or_nonfinite_json_is_rejected(raw: bytes) -> None:
    with pytest.raises(ValueError):
        decode_retail_sale_event(raw, anchor=anchor())


@pytest.mark.parametrize(
    "value",
    ["text", bytearray(b"{}"), memoryview(b"{}"), None],
    ids=["str", "bytearray", "memoryview", "none"],
)
def test_record_must_be_exact_bytes(value: object) -> None:
    with pytest.raises(ValueError):
        decode_retail_sale_event(cast(bytes, value), anchor=anchor())


def test_real_one_mebibyte_decode_boundary_and_deep_nesting_are_bounded() -> None:
    saved = record()
    at_limit = saved + b" " * (1024 * 1024 - len(saved))
    assert len(at_limit) == 1024 * 1024
    assert decode_retail_sale_event(at_limit, anchor=anchor()) == result("mixed")
    with pytest.raises(ValueError):
        decode_retail_sale_event(at_limit + b" ", anchor=anchor())
    with pytest.raises(ValueError):
        decode_retail_sale_event(b"[" * 10000 + b"0" + b"]" * 10000, anchor=anchor())


def test_encoder_enforces_its_record_limit_without_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved = record()
    assert storage.MAX_RECORD_BYTES == 1024 * 1024
    monkeypatch.setattr(storage, "MAX_RECORD_BYTES", len(saved))
    assert record() == saved
    monkeypatch.setattr(storage, "MAX_RECORD_BYTES", len(saved) - 1)
    with pytest.raises(ValueError):
        record()


@pytest.mark.parametrize(
    "field,value",
    [
        ("record_kind", '"retail_sale_event"'),
        ("storage_version", "1"),
        ("previous_source_checksum", json.dumps(SOURCE)),
        ("quantity", '"0.001"'),
        ("schema_version", "4"),
        ("units_per_pack", "30"),
    ],
    ids=["kind", "storage-version", "root", "line", "event-version", "packaging"],
)
def test_duplicate_json_fields_are_rejected_at_every_depth(field: str, value: str) -> None:
    text = json.dumps(payload())
    needle = f'"{field}": {value}'
    assert needle in text
    raw = text.replace(needle, f"{needle}, {needle}", 1).encode()
    with pytest.raises(ValueError):
        decode_retail_sale_event(raw, anchor=anchor())


@pytest.mark.parametrize(
    "version", [True, "1", 1.0, 0, 2], ids=["bool", "str", "float", "zero", "future"]
)
def test_storage_version_is_explicit_exact_integer_one(version: object) -> None:
    stored = payload()
    stored["storage_version"] = version
    with pytest.raises(ValueError):
        decode_retail_sale_event(canonical_json_bytes(stored), anchor=anchor())


@pytest.mark.parametrize("change", ["missing-kind", "wrong-kind", "missing-version", "extra"])
def test_storage_kind_and_version_are_mandatory_and_unknown_fields_are_rejected(
    change: str,
) -> None:
    stored = payload()
    if change == "missing-kind":
        del stored["record_kind"]
    elif change == "wrong-kind":
        stored["record_kind"] = "prepared_unit_event"
    elif change == "missing-version":
        del stored["storage_version"]
    else:
        stored["unexpected"] = True
    with pytest.raises(ValueError):
        decode_retail_sale_event(canonical_json_bytes(stored), anchor=anchor())


@pytest.mark.parametrize("change", ["quantity", "money", "cost", "prescription", "packaging-extra"])
def test_matching_forged_fingerprint_never_bypasses_inner_schema_or_canonicality(
    change: str,
) -> None:
    envelope = event()
    data = deepcopy(envelope.payload)
    physical = rows(data)[1]
    if change == "quantity":
        physical["quantity"] = "2"
    elif change == "money":
        data["total_amount"] = "23.520"
    elif change == "cost":
        physical["original_cost_amount"] = "FORBIDDEN-SYNTHETIC"
    elif change == "prescription":
        data["prescription"] = "FORBIDDEN-SYNTHETIC"
    else:
        cast(dict[str, object], physical["packaging"])["unexpected"] = True
    forged = rehash(envelope.model_copy(update={"payload": data}))
    with pytest.raises(ValueError):
        anchor(envelope=forged)
    digest = canonical_json_hash(
        {
            "domain": "aurum-pos-retail-sale-event",
            "schema_version": forged.schema_version,
            "envelope": forged.model_dump(mode="json"),
        }
    )
    forged_anchor = anchor().model_copy(update={"event_fingerprint": digest})
    stored = payload()
    stored.update(envelope=forged.model_dump(mode="json"), event_fingerprint=digest)
    with pytest.raises(ValueError):
        encode_retail_sale_event(forged, anchor=forged_anchor)
    with pytest.raises(ValueError):
        decode_retail_sale_event(canonical_json_bytes(stored), anchor=forged_anchor)


@pytest.mark.parametrize("field", ["writer_epoch", "sequence"])
@pytest.mark.parametrize(
    "value", [True, "2", 2.0, 0, 2**63], ids=["bool", "str", "float", "zero", "large"]
)
def test_constructed_anchor_cannot_bypass_strict_stream_integer_validation(
    field: str, value: object
) -> None:
    values = anchor().model_dump(mode="python") | {field: value}
    with pytest.raises(ValueError):
        RetailSaleEventAnchor.model_validate(values)
    forged = RetailSaleEventAnchor.model_construct(**values)
    with pytest.raises(ValueError):
        encode_retail_sale_event(event(), anchor=forged)
    with pytest.raises(ValueError):
        decode_retail_sale_event(record(), anchor=forged)


@pytest.mark.parametrize(
    "field", ["previous_source_checksum", "previous_projection_checksum", "event_fingerprint"]
)
@pytest.mark.parametrize(
    "value", ["A" * 64, "a" * 63, 1, None], ids=["uppercase", "short", "int", "null"]
)
def test_copied_anchor_cannot_bypass_strict_checksum_validation(field: str, value: object) -> None:
    forged = anchor().model_copy(update={field: value})
    with pytest.raises(ValueError):
        encode_retail_sale_event(event(), anchor=forged)
    with pytest.raises(ValueError):
        decode_retail_sale_event(record(), anchor=forged)


@pytest.mark.parametrize("kind", ["physical", "mixed"])
def test_version_families_stay_separate_and_restoration_does_not_enable_live_apply(
    kind: Kind,
) -> None:
    old_bytes = prepared_record()
    saved = record(kind)
    with pytest.raises(ValueError):
        decode_retail_sale_event(old_bytes, anchor=anchor(kind))
    with pytest.raises(ValueError):
        decode_unit_sale_event(saved, anchor=prepared_anchor())
    assert prepared_record() == old_bytes
    assert decode_retail_sale_event(saved, anchor=anchor(kind)) == result(kind)
    with pytest.raises(_EnvelopeError, match="unsupported_event"):
        SyncEdgeApplyService._validate_event(
            event(kind), previous_source_checksum=SOURCE, previous_projection_checksum=PROJECTION
        )


def test_retagged_v1_or_prepared_v2_envelopes_cannot_be_read_as_new_retail_events() -> None:
    stored = payload()
    envelope = cast(dict[str, object], stored["envelope"])
    for version in (1, 2):
        envelope.update(schema_version=version, event_type=f"pos.sale.completed.v{version}")
        with pytest.raises(ValueError):
            decode_retail_sale_event(canonical_json_bytes(stored), anchor=anchor())


def test_wire_envelope_integers_remain_strict_under_matching_outer_metadata() -> None:
    stored = payload()
    envelope = cast(dict[str, object], stored["envelope"])
    envelope["writer_epoch"] = True
    with pytest.raises(ValueError):
        decode_retail_sale_event(canonical_json_bytes(stored), anchor=anchor())
