"""Unsigned historical return integrity without payment, quarantine or database writes."""

from contextlib import nullcontext
from copy import deepcopy
from datetime import timedelta, timezone
from decimal import ROUND_UP, Decimal, localcontext
from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domains.pos.mixed_cash_refund_schemas import MixedCashRefundResultV4
from app.domains.pos.mixed_electronic_refund_posting_schemas import PostedMixedElectronicRefund
from app.domains.pos.physical_cash_refund_schemas import (
    PhysicalCashRefundLineV3,
    PhysicalCashRefundResultV3,
)
from app.domains.pos.retail_refund_events import (
    MAX_REFUND_EVENT_BYTES,
    RetailRefundEventResult,
    RetailRefundResult,
    RetailRefundSourceKind,
    build_retail_refund_event,
    retail_refund_event_fingerprint,
    validate_retail_refund_event,
)
from app.domains.pos.retail_sale_events import RetailSaleStreamPosition
from app.domains.sync.integrity import (
    canonical_json_bytes,
    canonical_json_hash,
    projection_stream_checksum,
    source_stream_checksum,
)
from app.domains.sync.schemas import SyncEventEnvelope
from tests.domains.pos.test_mixed_cash_refund_contracts import receipt_payload as legacy_receipt
from tests.domains.pos.test_mixed_electronic_refund_posting_contracts import posted as mixed_posted
from tests.domains.pos.test_unit_electronic_refund_posting_contracts import posted

KINDS: tuple[RetailRefundSourceKind, ...] = (
    "physical_cash",
    "mixed_cash",
    "physical_electronic",
    "mixed_electronic",
)
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


def _mixed_result() -> PostedMixedElectronicRefund:
    original = mixed_posted()
    receipt = original.receipt.model_dump(mode="json")
    physical = original.receipt.lines[0].model_dump(mode="json")
    physical["position"] = 2
    legacy = cast(list[dict[str, object]], legacy_receipt()["lines"])[0]
    legacy.update(position=1, original_position=1)
    receipt["lines"] = [legacy, physical]
    receipt["total_amount"] = "4.00"
    payments = cast(list[dict[str, object]], receipt["payments"])
    payments[0]["amount"] = "4.00"
    data = original.model_dump(mode="json")
    data.update(receipt=receipt, total_amount="4.00")
    return PostedMixedElectronicRefund.model_validate(data)


def _result(kind: RetailRefundSourceKind) -> RetailRefundResult:
    electronic = _mixed_result() if kind.startswith("mixed") else posted()
    if kind.endswith("electronic"):
        return electronic
    data = electronic.model_dump(mode="json")
    receipt = electronic.receipt.model_dump(mode="json")
    del receipt["payments"]
    receipt["document_kind"] = (
        "mixed_cash_refund_receipt" if kind == "mixed_cash" else "physical_cash_refund_receipt"
    )
    receipt["payment_method"] = "cash"
    del data["state"]
    data.update(payment_id=electronic.operation_id, receipt=receipt)
    model = MixedCashRefundResultV4 if kind == "mixed_cash" else PhysicalCashRefundResultV3
    return model.model_validate(data)


def payload(kind: RetailRefundSourceKind = "physical_cash") -> RetailRefundEventResult:
    result = _result(kind)
    mixed = isinstance(result, MixedCashRefundResultV4 | PostedMixedElectronicRefund)
    lines: list[dict[str, object]] = []
    for line in result.receipt.lines:
        received: dict[str, object] = {
            "line_id": line.original_line_id,
            "offset": line.offset,
            "gross_amount": line.gross_amount,
            "discount_amount": line.discount_amount,
            "net_amount": line.net_amount,
        }
        if mixed:
            mixed_line = line.model_dump(mode="python")
            received.update(
                inventory_kind=mixed_line["inventory_kind"],
                position=mixed_line["original_position"],
                quantity=line.quantity,
            )
        if isinstance(line, PhysicalCashRefundLineV3):
            received.update(
                base_units=line.base_units,
                cost_amount="1.00",
                slices=[
                    {
                        "source_movement_id": UUID(int=500 + line.position),
                        "lot_id": UUID(int=600 + line.position),
                        "source_offset": line.offset,
                        "base_units": line.base_units,
                        "cost_amount": "1.00",
                    }
                ],
            )
            if mixed:
                received["sale_format"] = line.sale_format
        else:
            received["batch_id"] = UUID(int=700 + line.position)
        lines.append(received)
    journal: dict[str, object] = {
        "operation_id": result.operation_id,
        "parent_sale_id": result.parent_sale_id,
        "tenant_id": result.tenant_id,
        "branch_id": result.branch_id,
        "actor_id": result.actor_id,
        "total_amount": result.total_amount,
        "lines": lines,
        "physical_cost_amount" if mixed else "total_cost_amount": "1.00",
    }
    return RetailRefundEventResult.model_validate(
        {"source_kind": kind, "result": result, "quarantine": journal}
    )


def event(kind: RetailRefundSourceKind = "physical_cash") -> SyncEventEnvelope:
    return build_retail_refund_event(payload(kind), position=position())


def validate(envelope: SyncEventEnvelope) -> RetailRefundEventResult:
    return validate_retail_refund_event(
        envelope, previous_source_checksum=SOURCE, previous_projection_checksum=PROJECTION
    )


def rehash(envelope: SyncEventEnvelope) -> SyncEventEnvelope:
    digest = canonical_json_hash(envelope.payload)
    return envelope.model_copy(
        update={
            "payload_hash": digest,
            "projection_hash": digest,
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
                payload_hash=digest,
            ),
            "projection_checksum": projection_stream_checksum(
                previous_checksum=PROJECTION,
                origin_node_id=envelope.origin_node_id,
                writer_epoch=envelope.writer_epoch,
                sequence=envelope.sequence,
                sale_id=envelope.aggregate_id,
                projection_hash=digest,
            ),
        }
    )


@pytest.mark.parametrize("kind", KINDS)
def test_all_refund_families_keep_exact_historical_documents(kind: RetailRefundSourceKind) -> None:
    source = payload(kind)
    before = source.model_dump(mode="json")
    envelope = build_retail_refund_event(source, position=position())
    restored = validate(envelope)
    assert restored == source
    assert restored.result.receipt == source.result.receipt
    assert source.model_dump(mode="json") == before
    assert envelope.event_type == "pos.sale.refunded.v6" and envelope.schema_version == 6
    assert envelope.event_id == envelope.operation_id == source.result.operation_id
    assert envelope.aggregate_id == source.result.sale_id != source.result.parent_sale_id
    assert envelope.projection_hash == envelope.payload_hash == canonical_json_hash(before)
    assert retail_refund_event_fingerprint(envelope) == canonical_json_hash(
        {
            "domain": "aurum-retail-refund-event",
            "schema_version": 6,
            "envelope": envelope.model_dump(mode="json"),
        }
    )


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "field", ["operation_id", "tenant_id", "branch_id", "parent_sale_id", "actor_id"]
)
def test_quarantine_cannot_be_rebound_to_another_refund(
    kind: RetailRefundSourceKind, field: str
) -> None:
    data = payload(kind).model_dump(mode="json")
    cast(dict[str, object], data["quarantine"])[field] = str(UUID(int=999))
    with pytest.raises(ValidationError, match="scope"):
        RetailRefundEventResult.model_validate(data)


@pytest.mark.parametrize("kind", KINDS)
def test_wrong_document_family_and_payment_kind_are_rejected(kind: RetailRefundSourceKind) -> None:
    data = payload(kind).model_dump(mode="json")
    data["source_kind"] = KINDS[(KINDS.index(kind) + 1) % len(KINDS)]
    with pytest.raises(ValidationError, match="source kind"):
        RetailRefundEventResult.model_validate(data)


@pytest.mark.parametrize("field", ["base_units", "offset", "line_id", "cost_amount"])
def test_quarantine_allocations_and_known_cost_cannot_change(field: str) -> None:
    data = payload().model_dump(mode="json")
    journal = cast(dict[str, object], data["quarantine"])
    lines = cast(list[dict[str, object]], journal["lines"])
    lines[0][field] = str(UUID(int=999)) if field == "line_id" else 3
    with pytest.raises(ValidationError):
        RetailRefundEventResult.model_validate(data)


@pytest.mark.parametrize("kind", ["mixed_cash", "mixed_electronic"])
@pytest.mark.parametrize(
    "field,value", [("position", 3), ("quantity", "0.002"), ("offset", "0.003")]
)
def test_legacy_quantity_and_source_position_remain_historical(
    kind: RetailRefundSourceKind, field: str, value: object
) -> None:
    data = payload(kind).model_dump(mode="json")
    journal = cast(dict[str, object], data["quarantine"])
    cast(list[dict[str, object]], journal["lines"])[0][field] = value
    with pytest.raises(ValidationError):
        RetailRefundEventResult.model_validate(data)


@pytest.mark.parametrize(
    "field,value",
    [
        ("event_id", UUID(int=999)),
        ("operation_id", UUID(int=999)),
        ("aggregate_id", UUID(int=999)),
        ("tenant_id", UUID(int=999)),
        ("branch_id", UUID(int=999)),
        ("schema_version", 3),
        ("schema_version", True),
        ("sequence", True),
        ("writer_epoch", "2"),
        ("aggregate_type", "inventory"),
        ("event_type", "pos.sale.completed.v6"),
        ("stream_checksum", "0" * 64),
        ("projection_checksum", "0" * 64),
    ],
)
def test_envelope_identity_and_chain_are_checked(field: str, value: object) -> None:
    warning = (
        pytest.warns(UserWarning, match="Pydantic serializer warnings")
        if field == "writer_epoch" and value == "2"
        else nullcontext()
    )
    with warning, pytest.raises(ValueError):
        validate(event().model_copy(update={field: value}))


def test_raw_tampering_and_noncanonical_money_are_rejected_even_after_rehash() -> None:
    original = event()
    data = deepcopy(original.payload)
    result = cast(dict[str, object], data["result"])
    result["total_amount"] = "3.990"
    changed = original.model_copy(update={"payload": data})
    with pytest.raises(ValueError):
        validate(changed)
    with pytest.raises(ValueError, match="canonical"):
        validate(rehash(changed))


def test_model_copy_and_construct_do_not_bypass_nested_contracts() -> None:
    source = payload()
    invalid = source.result.model_copy(update={"sale_id": source.result.parent_sale_id})
    with pytest.raises(ValidationError):
        build_retail_refund_event(
            source.model_copy(update={"result": invalid}), position=position()
        )
    invalid_position = position().model_copy(update={"sequence": True})
    with pytest.raises(ValidationError):
        build_retail_refund_event(source, position=invalid_position)


def test_refund_timestamp_is_aware_and_envelope_utc() -> None:
    envelope = event()
    shifted = envelope.occurred_at.astimezone(timezone(timedelta(hours=3)))
    assert validate(envelope.model_copy(update={"occurred_at": shifted})) == validate(envelope)
    with pytest.raises(ValueError):
        validate(
            envelope.model_copy(update={"occurred_at": envelope.occurred_at.replace(tzinfo=None)})
        )


def test_hashing_ignores_decimal_context_and_does_not_modify_source() -> None:
    source = payload("mixed_electronic")
    before = source.model_dump(mode="json")
    normal = build_retail_refund_event(source, position=position())
    with localcontext() as context:
        context.prec, context.rounding = 3, ROUND_UP
        adjusted = build_retail_refund_event(source, position=position())
        assert validate(adjusted) == source
    assert canonical_json_bytes(normal.model_dump(mode="json")) == canonical_json_bytes(
        adjusted.model_dump(mode="json")
    )
    assert source.model_dump(mode="json") == before


def test_event_size_is_bounded_before_fingerprinting() -> None:
    envelope = event()
    oversized = envelope.model_copy(update={"payload": {"oversized": "x" * MAX_REFUND_EVENT_BYTES}})
    with pytest.raises(ValueError, match="size limit"):
        retail_refund_event_fingerprint(oversized)


def test_builder_rejects_oversized_but_otherwise_balanced_source_history() -> None:
    source = payload().model_dump(mode="json")
    result = cast(dict[str, object], source["result"])
    receipt = cast(dict[str, object], result["receipt"])
    cast(list[dict[str, object]], receipt["lines"])[0].update(quantity=6500, base_units=6500)
    journal = cast(dict[str, object], source["quarantine"])
    line = cast(list[dict[str, object]], journal["lines"])[0]
    line.update(
        base_units=6500,
        slices=[
            {
                "source_movement_id": str(UUID(int=10000 + index)),
                "lot_id": str(UUID(int=20000 + index)),
                "source_offset": 0,
                "base_units": 1,
                "cost_amount": "1.00" if index == 0 else "0.00",
            }
            for index in range(6500)
        ],
    )
    validated = RetailRefundEventResult.model_validate(source)
    with pytest.raises(ValueError, match="size limit"):
        build_retail_refund_event(validated, position=position())


def test_one_source_movement_cannot_be_claimed_by_two_refund_lines() -> None:
    data = payload().model_dump(mode="json")
    result = cast(dict[str, object], data["result"])
    result.update(total_amount="7.98", total_cost_amount="2.00")
    receipt = cast(dict[str, object], result["receipt"])
    receipt["total_amount"] = "7.98"
    lines = cast(list[dict[str, object]], receipt["lines"])
    second = deepcopy(lines[0])
    second.update(original_line_id=str(UUID(int=800)), position=2)
    lines.append(second)
    journal = cast(dict[str, object], data["quarantine"])
    journal.update(total_amount="7.98", total_cost_amount="2.00")
    received = cast(list[dict[str, object]], journal["lines"])
    second_received = deepcopy(received[0])
    second_received["line_id"] = str(UUID(int=800))
    received.append(second_received)
    with pytest.raises(ValidationError, match="source movements"):
        RetailRefundEventResult.model_validate(data)


def test_payload_requires_utc_without_rewriting_historical_receipt() -> None:
    original = payload()
    result = original.result
    changed = result.receipt.model_copy(
        update={
            "completed_at": result.receipt.completed_at.astimezone(timezone(timedelta(hours=3)))
        }
    )
    with pytest.raises(ValidationError, match="UTC"):
        RetailRefundEventResult.model_validate(
            original.model_copy(update={"result": result.model_copy(update={"receipt": changed})})
        )
    assert original.result.receipt.completed_at.utcoffset() == timedelta(0)


def test_independent_anchor_is_required_to_authenticate_consistently_rewritten_history() -> None:
    envelope = event()
    data = deepcopy(envelope.payload)
    receipt = cast(dict[str, object], cast(dict[str, object], data["result"])["receipt"])
    cast(list[dict[str, object]], receipt["lines"])[0]["name"] = "Another historical label"
    changed = rehash(envelope.model_copy(update={"payload": data}))
    assert validate(changed).result.total_amount == Decimal("3.99")
    assert retail_refund_event_fingerprint(changed) != retail_refund_event_fingerprint(envelope)
