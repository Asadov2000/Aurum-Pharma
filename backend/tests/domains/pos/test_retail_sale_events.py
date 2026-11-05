"""Unsigned physical/mixed event integrity without enabling live Edge apply."""

from copy import deepcopy
from datetime import UTC, timedelta
from decimal import ROUND_UP, localcontext
from typing import Literal, cast
from uuid import UUID

import pytest

from app.domains.pos.mixed_documents import MixedCheckoutResultV4
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.retail_sale_events import (
    RetailSaleStreamPosition,
    build_retail_sale_event,
    retail_sale_event_fingerprint,
    validate_retail_sale_event,
)
from app.domains.pos.unit_events import validate_unit_sale_event
from app.domains.sync.integrity import (
    canonical_json_hash,
    projection_stream_checksum,
    sale_projection_hash,
    source_stream_checksum,
)
from app.domains.sync.schemas import SyncEventEnvelope
from app.domains.sync.service import SyncEdgeApplyService, _EnvelopeError
from tests.domains.pos.test_mixed_documents import mixed_payload
from tests.domains.pos.test_physical_documents import result_payload
from tests.domains.pos.test_unit_events import event as prepared_event

Kind = Literal["physical", "mixed"]
RetailResult = PhysicalCheckoutResultV3 | MixedCheckoutResultV4
SOURCE = "a" * 64
PROJECTION = "b" * 64


def position() -> RetailSaleStreamPosition:
    return RetailSaleStreamPosition(
        origin_node_id=UUID(int=90),
        writer_epoch=2,
        sequence=3,
        previous_source_checksum=SOURCE,
        previous_projection_checksum=PROJECTION,
    )


def result(kind: Kind) -> RetailResult:
    if kind == "physical":
        return PhysicalCheckoutResultV3.model_validate(result_payload(is_test=False))
    return MixedCheckoutResultV4.model_validate(mixed_payload(is_test=False))


def event(kind: Kind = "mixed") -> SyncEventEnvelope:
    return build_retail_sale_event(result(kind), position=position())


def validate(envelope: SyncEventEnvelope) -> RetailResult:
    return validate_retail_sale_event(
        envelope, previous_source_checksum=SOURCE, previous_projection_checksum=PROJECTION
    )


def fingerprint(envelope: SyncEventEnvelope) -> str:
    return retail_sale_event_fingerprint(
        envelope, previous_source_checksum=SOURCE, previous_projection_checksum=PROJECTION
    )


def rehash(envelope: SyncEventEnvelope) -> SyncEventEnvelope:
    """Unsigned hashes can be rewritten; schema and context must still hold."""
    payload_hash = canonical_json_hash(envelope.payload)
    projection_hash = sale_projection_hash(envelope.payload)
    return envelope.model_copy(
        update={
            "payload_hash": payload_hash,
            "projection_hash": projection_hash,
            "stream_checksum": source_stream_checksum(
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
                payload_hash=payload_hash,
            ),
            "projection_checksum": projection_stream_checksum(
                previous_checksum=PROJECTION,
                origin_node_id=envelope.origin_node_id,
                writer_epoch=envelope.writer_epoch,
                sequence=envelope.sequence,
                sale_id=envelope.aggregate_id,
                projection_hash=projection_hash,
            ),
        }
    )


def rows(payload: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], payload["lines"])


@pytest.mark.parametrize("kind", ["physical", "mixed"])
def test_roundtrip_preserves_original_result_and_separate_event_version(kind: Kind) -> None:
    original = result(kind)
    before = original.model_dump(mode="json")
    envelope = build_retail_sale_event(original, position=position())
    version = 3 if kind == "physical" else 4
    assert envelope.schema_version == version
    assert envelope.event_type == f"pos.sale.completed.v{version}"
    assert envelope.aggregate_type == "sale"
    assert envelope.payload == before
    assert original.model_dump(mode="json") == before
    assert validate(envelope) == original
    restored = SyncEventEnvelope.model_validate_json(envelope.model_dump_json())
    assert validate(restored) == original
    assert fingerprint(restored) == fingerprint(envelope)
    assert envelope.occurred_at.tzinfo == UTC
    assert envelope.payload["completed_at"] == "2026-10-07T05:01:00Z"
    assert envelope.payload["is_test"] is False
    physical = rows(envelope.payload)[-1]
    assert physical["quantity"] == 2 and type(physical["quantity"]) is int
    assert physical["base_units"] == 20 and physical["sale_format"] == "blister"
    if kind == "mixed":
        assert rows(envelope.payload)[0]["quantity"] == "0.001"
        assert envelope.payload["total_amount"] == "23.52"
    assert envelope.payload == before


@pytest.mark.parametrize("kind", ["physical", "mixed"])
@pytest.mark.parametrize("sale_format,base_units", [("pack", 60), ("blister", 20), ("unit", 2)])
def test_each_display_format_keeps_its_historical_physical_scale(
    kind: Kind, sale_format: str, base_units: int
) -> None:
    payload = result(kind).model_dump(mode="json")
    physical = rows(payload)[-1]
    physical.update(sale_format=sale_format, base_units=base_units)
    model = PhysicalCheckoutResultV3 if kind == "physical" else MixedCheckoutResultV4
    original = model.model_validate(payload)
    envelope = build_retail_sale_event(original, position=position())
    assert validate(envelope) == original
    assert rows(envelope.payload)[-1]["base_units"] == base_units
    assert rows(envelope.payload)[-1]["quantity"] == 2


def test_canonical_event_and_fingerprint_ignore_ambient_decimal_context() -> None:
    expected = event()
    with localcontext() as context:
        context.prec = 2
        context.rounding = ROUND_UP
        actual = event()
        assert validate(actual) == result("mixed")
        assert fingerprint(actual) == fingerprint(expected)
    assert actual.model_dump_json() == expected.model_dump_json()


@pytest.mark.parametrize("kind", ["physical", "mixed"])
def test_test_flag_is_preserved_without_becoming_permission_or_a_real_sale(kind: Kind) -> None:
    original = result(kind)
    test_result = original.model_copy(update={"is_test": True})
    envelope = build_retail_sale_event(test_result, position=position())
    assert validate(envelope).is_test is True
    assert fingerprint(envelope) != fingerprint(
        build_retail_sale_event(original, position=position())
    )
    with pytest.raises(_EnvelopeError, match="unsupported_event"):
        SyncEdgeApplyService._validate_event(
            envelope, previous_source_checksum=SOURCE, previous_projection_checksum=PROJECTION
        )


@pytest.mark.parametrize(
    "field", ["event_id", "tenant_id", "branch_id", "operation_id", "aggregate_id", "occurred_at"]
)
def test_recomputed_unsigned_hashes_do_not_hide_payload_envelope_identity_mismatch(
    field: str,
) -> None:
    envelope = event()
    value = envelope.occurred_at + timedelta(seconds=1) if field == "occurred_at" else UUID(int=99)
    forged = rehash(envelope.model_copy(update={field: value}))
    with pytest.raises(ValueError):
        validate(forged)
    with pytest.raises(ValueError):
        fingerprint(forged)


@pytest.mark.parametrize(
    "field", ["payload_hash", "stream_checksum", "projection_hash", "projection_checksum"]
)
def test_each_payload_and_stream_integrity_component_is_checked(field: str) -> None:
    forged = event().model_copy(update={field: "c" * 64})
    with pytest.raises(ValueError):
        validate(forged)


@pytest.mark.parametrize("chain", ["source", "projection"])
def test_previous_checksums_come_from_the_independent_caller(chain: str) -> None:
    with pytest.raises(ValueError):
        validate_retail_sale_event(
            event(),
            previous_source_checksum="c" * 64 if chain == "source" else SOURCE,
            previous_projection_checksum="c" * 64 if chain == "projection" else PROJECTION,
        )


@pytest.mark.parametrize("field", ["register_id", "shift_id", "cashier_id", "stock_command_id"])
def test_fingerprint_binds_retail_identity_absent_from_outer_envelope(field: str) -> None:
    original = result("mixed")
    changed = original.model_copy(update={field: UUID(int=99)})
    other = build_retail_sale_event(changed, position=position())
    assert validate(other) == changed
    assert fingerprint(other) != fingerprint(event())


@pytest.mark.parametrize("field", ["origin_node_id", "writer_epoch", "sequence"])
def test_fingerprint_binds_the_transport_origin_and_position(field: str) -> None:
    value = UUID(int=99) if field == "origin_node_id" else 5
    other = rehash(event().model_copy(update={field: value}))
    assert validate(other) == result("mixed")
    assert fingerprint(other) != fingerprint(event())


@pytest.mark.parametrize("level", ["root", "legacy", "physical", "packaging", "payment"])
@pytest.mark.parametrize("field", ["original_cost_amount", "prescription"])
def test_sensitive_extras_remain_forbidden_after_attacker_recomputes_all_hashes(
    level: str, field: str
) -> None:
    envelope = event()
    payload = deepcopy(envelope.payload)
    target = payload
    if level in {"legacy", "physical", "packaging"}:
        target = rows(payload)[0 if level == "legacy" else 1]
        if level == "packaging":
            target = cast(dict[str, object], target["packaging"])
    elif level == "payment":
        target = cast(list[dict[str, object]], payload["payments"])[0]
    target[field] = "FORBIDDEN-SYNTHETIC"
    with pytest.raises(ValueError):
        validate(rehash(envelope.model_copy(update={"payload": payload})))


@pytest.mark.parametrize(
    "mutation", ["quantity", "base_units", "packaging", "order", "total", "money", "legacy", "kind"]
)
def test_invalid_facts_or_noncanonical_json_are_not_authenticated_by_unsigned_hashes(
    mutation: str,
) -> None:
    envelope = event()
    payload = deepcopy(envelope.payload)
    physical = rows(payload)[1]
    if mutation in {"quantity", "base_units"}:
        physical[mutation] = True
    elif mutation == "packaging":
        cast(dict[str, object], physical["packaging"])["units_per_pack"] = 31
    elif mutation == "order":
        payload["lines"] = list(reversed(rows(payload)))
    elif mutation == "total":
        payload["total_amount"] = "23.53"
    elif mutation == "money":
        payload["total_amount"] = "23.520"
    elif mutation == "legacy":
        rows(payload)[0]["quantity"] = "0.0010"
    else:
        physical["inventory_kind"] = "prepared_unit"
    with pytest.raises(ValueError):
        validate(rehash(envelope.model_copy(update={"payload": payload})))


@pytest.mark.parametrize("field", ["writer_epoch", "sequence"])
@pytest.mark.parametrize(
    "value", [True, "1", 1.0, 0, 2**63], ids=["bool", "str", "float", "zero", "large"]
)
def test_stream_integer_checks_survive_unchecked_model_copy(field: str, value: object) -> None:
    with pytest.raises(ValueError):
        RetailSaleStreamPosition.model_validate(position().model_dump() | {field: value})
    forged_position = position().model_copy(update={field: value})
    with pytest.raises(ValueError):
        build_retail_sale_event(result("physical"), position=forged_position)
    with pytest.raises(ValueError):
        validate(event().model_copy(update={field: value}))


@pytest.mark.parametrize("field", ["previous_source_checksum", "previous_projection_checksum"])
@pytest.mark.parametrize(
    "value", ["A" * 64, "a" * 63, 1, None], ids=["uppercase", "short", "int", "null"]
)
def test_stream_checksum_fields_do_not_coerce_or_accept_malformed_values(
    field: str, value: object
) -> None:
    with pytest.raises(ValueError):
        RetailSaleStreamPosition.model_validate(position().model_dump() | {field: value})
    with pytest.raises(ValueError):
        build_retail_sale_event(
            result("physical"), position=position().model_copy(update={field: value})
        )


@pytest.mark.parametrize("kind", ["physical", "mixed"])
def test_builder_revalidates_constructed_nested_result_and_naive_outer_timestamp(
    kind: Kind,
) -> None:
    original = result(kind)
    physical = original.lines[-1].model_copy(update={"quantity": True})
    data = original.model_dump(mode="python") | {"lines": (*original.lines[:-1], physical)}
    model = PhysicalCheckoutResultV3 if kind == "physical" else MixedCheckoutResultV4
    with pytest.raises(ValueError):
        build_retail_sale_event(model.model_construct(**data), position=position())
    envelope = event(kind)
    values = envelope.model_dump(mode="python") | {
        "occurred_at": envelope.occurred_at.replace(tzinfo=None)
    }
    with pytest.raises(ValueError):
        validate(SyncEventEnvelope.model_construct(**values))


@pytest.mark.parametrize(
    "changes",
    [
        {"schema_version": True},
        {"schema_version": 4.0},
        {"schema_version": 1, "event_type": "pos.sale.completed.v1"},
        {"schema_version": 2, "event_type": "pos.sale.completed.v2"},
        {"event_type": "pos.sale.completed.v3"},
        {"aggregate_type": "stock"},
    ],
    ids=["bool-version", "float-version", "v1", "v2", "mismatched-v3", "stock"],
)
def test_unknown_or_mismatched_contracts_are_rejected(changes: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        validate(event().model_copy(update=changes))


@pytest.mark.parametrize("kind", ["physical", "mixed"])
def test_existing_v2_parser_and_live_handler_stay_closed_to_new_retail_versions(kind: Kind) -> None:
    envelope = event(kind)
    with pytest.raises(ValueError):
        validate_unit_sale_event(
            envelope, previous_source_checksum=SOURCE, previous_projection_checksum=PROJECTION
        )
    with pytest.raises(_EnvelopeError, match="unsupported_event"):
        SyncEdgeApplyService._validate_event(
            envelope, previous_source_checksum=SOURCE, previous_projection_checksum=PROJECTION
        )
    with pytest.raises(ValueError):
        validate(prepared_event())
