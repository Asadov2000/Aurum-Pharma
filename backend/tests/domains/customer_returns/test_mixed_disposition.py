"""Pure mixed disposition contracts never authorize, post stock, or accept quantities."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Literal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.core.errors import ConflictError
from app.domains.customer_returns.mixed_disposition import (
    MixedDispositionRequest,
    MixedLegacyDispositionSnapshot,
    MixedPhysicalDispositionSnapshot,
    PreparedMixedDisposition,
    prepare_mixed_disposition,
)
from app.domains.customer_returns.mixed_schemas import MixedCustomerReturnRead

InventoryKind = Literal["legacy_pack", "physical_unit"]
IntakeKind = Literal["cash_refund", "zero_return"]
TENANT = UUID(int=1)


def source(
    *, kind: InventoryKind = "legacy_pack", intake_kind: IntakeKind = "cash_refund"
) -> MixedCustomerReturnRead:
    return MixedCustomerReturnRead(
        operation_id=UUID(int=2),
        line_id=UUID(int=3),
        inventory_kind=kind,
        batch_id=UUID(int=4) if kind == "legacy_pack" else None,
        lot_id=UUID(int=5) if kind == "physical_unit" else None,
        branch_id=UUID(int=6),
        branch_name="Historical branch",
        report_timezone="Asia/Dushanbe",
        parent_sale_id=UUID(int=7),
        parent_receipt_number="Historical original",
        return_sale_id=UUID(int=8) if intake_kind == "cash_refund" else None,
        return_receipt_number="Historical return" if intake_kind == "cash_refund" else None,
        catalog_id=UUID(int=9),
        catalog_name="Historical medicine",
        legacy_quantity=Decimal("0.125") if kind == "legacy_pack" else None,
        base_units=31 if kind == "physical_unit" else None,
        unit_label="уп." if kind == "legacy_pack" else "капс.",
        batch_number="Historical batch",
        expires_at=date(2028, 1, 1),
        received_at=datetime(2026, 10, 1, tzinfo=UTC),
        refund_reason=None,
        refund_comment=None,
        intake_kind=intake_kind,
    )


def request_body(original: MixedCustomerReturnRead) -> dict[str, object]:
    target: dict[str, object] = {
        "inventory_kind": original.inventory_kind,
        "intake_operation_id": original.operation_id,
        "line_id": original.line_id,
    }
    if original.inventory_kind == "legacy_pack":
        target["batch_id"] = original.batch_id
    else:
        target["lot_id"] = original.lot_id
    return {
        "decision_operation_id": UUID(int=10),
        "target": target,
        "disposition_type": "disposed",
        "reason_code": "damaged",
        "comment": None,
    }


def request(original: MixedCustomerReturnRead, **updates: object) -> MixedDispositionRequest:
    return MixedDispositionRequest.model_validate({**request_body(original), **updates})


@pytest.mark.parametrize("kind", ["legacy_pack", "physical_unit"])
@pytest.mark.parametrize("intake_kind", ["cash_refund", "zero_return"])
def test_preparation_preserves_full_historical_quantity_without_recording_action(
    kind: InventoryKind, intake_kind: IntakeKind
) -> None:
    original = source(kind=kind, intake_kind=intake_kind)
    before = original.model_dump(mode="json")
    prepared = prepare_mixed_disposition(request(original), original, tenant_id=TENANT)
    assert prepared.state == "prepared"
    assert prepared.version == 1
    assert prepared.tenant_id == TENANT
    assert prepared.branch_id == original.branch_id
    assert prepared.parent_sale_id == original.parent_sale_id
    assert prepared.intake_kind == intake_kind
    assert prepared.target.intake_operation_id == original.operation_id
    assert prepared.target.line_id == original.line_id
    assert prepared.target.unit_label == original.unit_label
    if isinstance(prepared.target, MixedLegacyDispositionSnapshot):
        assert prepared.target.legacy_quantity == Decimal("0.125")
        assert prepared.target.model_dump(mode="json")["legacy_quantity"] == "0.125"
        assert prepared.target.batch_id == original.batch_id
        assert "base_units" not in prepared.target.model_dump()
    else:
        assert isinstance(prepared.target, MixedPhysicalDispositionSnapshot)
        assert prepared.target.base_units == 31  # Whole grouped lot, not an individual slice.
        assert prepared.target.lot_id == original.lot_id
        assert "legacy_quantity" not in prepared.target.model_dump()
    assert original.model_dump(mode="json") == before
    payload = prepared.model_dump(mode="json")
    for forbidden in ("actor_id", "resolved_at", "resolved_by", "cost", "payment", "receipt"):
        assert forbidden not in payload
    with pytest.raises(ValidationError, match="frozen"):
        prepared.state = "resolved"
    with pytest.raises(ValidationError, match="frozen"):
        prepared.target.line_id = uuid4()


@pytest.mark.parametrize("kind", ["legacy_pack", "physical_unit"])
@pytest.mark.parametrize("field", ["intake_operation_id", "line_id", "stock_id", "kind"])
def test_target_requires_exact_confirmed_intake_line_kind_and_batch_or_lot(
    kind: InventoryKind, field: str
) -> None:
    original = source(kind=kind)
    if field == "kind":
        changed = request(source(kind="physical_unit" if kind == "legacy_pack" else "legacy_pack"))
    else:
        target = request(original).target.model_dump()
        key = ("batch_id" if kind == "legacy_pack" else "lot_id") if field == "stock_id" else field
        target[key] = uuid4()
        changed = request(original, target=target)
    with pytest.raises(ConflictError, match="confirmed source"):
        prepare_mixed_disposition(changed, original, tenant_id=TENANT)


@pytest.mark.parametrize("location", ["request", "target"])
@pytest.mark.parametrize(
    "field",
    [
        "quantity",
        "legacy_quantity",
        "base_units",
        "tenant_id",
        "branch_id",
        "actor_id",
        "status",
        "cost",
    ],
)
def test_client_cannot_supply_quantity_authority_or_execution_facts(
    location: str, field: str
) -> None:
    original = source()
    body = request_body(original)
    if location == "target":
        body["target"] = {**request(original).target.model_dump(), field: 1}
    else:
        body[field] = 1
    with pytest.raises(ValidationError, match="extra_forbidden"):
        MixedDispositionRequest.model_validate(body)


@pytest.mark.parametrize("decision", ["disposed", "supplier_claim", "regulatory_transfer"])
@pytest.mark.parametrize("reason", ["damaged", "quality_issue", "wrong_item", "expired", "other"])
def test_only_existing_final_decisions_and_controlled_reasons_are_accepted(
    decision: str, reason: str
) -> None:
    original = source()
    result = prepare_mixed_disposition(
        request(original, disposition_type=decision, reason_code=reason), original, tenant_id=TENANT
    )
    assert result.disposition_type == decision and result.reason_code == reason


@pytest.mark.parametrize("invalid", ["restock", "partial", "resolved", "unknown", 1, True])
def test_unknown_or_non_final_decisions_are_rejected(invalid: object) -> None:
    with pytest.raises(ValidationError):
        request(source(), disposition_type=invalid)


@pytest.mark.parametrize("invalid", ["free text", "", 1, True, None])
def test_unknown_or_non_string_reason_is_rejected(invalid: object) -> None:
    with pytest.raises(ValidationError):
        request(source(), reason_code=invalid)


@pytest.mark.parametrize("invalid", [1, True, [], {}], ids=["integer", "bool", "list", "dict"])
def test_comment_is_strict_text(invalid: object) -> None:
    with pytest.raises(ValidationError):
        request(source(), comment=invalid)


@pytest.mark.parametrize("invalid", ["\x00", "text\x00text", "\ud800", "\udfff"])
def test_comment_rejects_text_unrepresentable_in_postgresql_utf8(invalid: str) -> None:
    with pytest.raises(ValidationError):
        request(source(), comment=invalid)


def test_comment_normalization_limits_and_unicode_are_explicit() -> None:
    original = source()
    assert request(original, comment="\t \n\u2003").comment is None
    text = "Претензия №1 — сифат"
    assert request(original, comment=f"\u2003 {text} \n").comment == text
    assert request(original, comment=" " + "я" * 2000 + " ").comment == "я" * 2000
    with pytest.raises(ValidationError):
        request(original, comment="я" * 2001)


def test_request_json_boundary_and_separate_operation_namespace() -> None:
    original = source()
    body = request_body(original)
    validated = MixedDispositionRequest.model_validate_json(json.dumps(body, default=str))
    assert validated == request(original)
    restored = MixedDispositionRequest.model_validate(json.loads(validated.model_dump_json()))
    assert restored == validated
    with pytest.raises(ValidationError):
        request(original, decision_operation_id="invalid")
    with pytest.raises(ValidationError):
        request(original, decision_operation_id=original.operation_id)
    for invalid in ({"inventory_kind": "legacy_pack"}, {"inventory_kind": "prepared_unit"}):
        with pytest.raises(ValidationError):
            request(original, target=invalid)


@pytest.mark.parametrize(
    "invalid", ["request", "source", "nested_target", "cash_shape", "zero_shape"]
)
def test_preparation_revalidates_instances_made_invalid_by_model_copy(invalid: str) -> None:
    original = source()
    proposed = request(original)
    if invalid == "request":
        proposed = proposed.model_copy(update={"disposition_type": "restock"})
    elif invalid == "source":
        original = original.model_copy(update={"legacy_quantity": Decimal("0")})
    elif invalid == "nested_target":
        proposed = proposed.model_copy(
            update={"target": proposed.target.model_copy(update={"line_id": "invalid"})}
        )
    elif invalid == "cash_shape":
        original = original.model_copy(update={"return_sale_id": None})
    else:
        original = original.model_copy(update={"intake_kind": "zero_return"})
    with pytest.raises(ValidationError):
        prepare_mixed_disposition(proposed, original, tenant_id=TENANT)


def test_normalized_payload_hash_ignores_separate_decision_uuid() -> None:
    original = source()
    first = prepare_mixed_disposition(
        request(original, comment="\n Претензия \t"), original, tenant_id=TENANT
    )
    replay = prepare_mixed_disposition(
        request(original, comment="Претензия", decision_operation_id=uuid4()),
        original,
        tenant_id=TENANT,
    )
    assert first.request_hash == replay.request_hash
    assert first.decision_operation_id != replay.decision_operation_id
    empty = prepare_mixed_disposition(request(original, comment=" \n "), original, tenant_id=TENANT)
    absent = prepare_mixed_disposition(request(original), original, tenant_id=TENANT)
    assert empty.request_hash == absent.request_hash


@pytest.mark.parametrize("kind", ["legacy_pack", "physical_unit"])
@pytest.mark.parametrize(
    "changed", ["tenant", "intake", "line", "stock", "decision", "reason", "comment"]
)
def test_tenant_target_or_normalized_decision_changes_hash(
    kind: InventoryKind, changed: str
) -> None:
    original = source(kind=kind)
    before = prepare_mixed_disposition(request(original), original, tenant_id=TENANT)
    tenant = uuid4() if changed == "tenant" else TENANT
    source_field = {
        "intake": "operation_id",
        "line": "line_id",
        "stock": "batch_id" if kind == "legacy_pack" else "lot_id",
    }
    if changed in source_field:
        original = original.model_copy(update={source_field[changed]: uuid4()})
    updates: dict[str, object] = {}
    if changed == "decision":
        updates["disposition_type"] = "supplier_claim"
    elif changed == "reason":
        updates["reason_code"] = "other"
    elif changed == "comment":
        updates["comment"] = "Сохранить Unicode"
    after = prepare_mixed_disposition(request(original, **updates), original, tenant_id=tenant)
    assert after.request_hash != before.request_hash


def test_hash_is_versioned_canonical_utf8_with_only_comment_digest() -> None:
    original = source()
    prepared = prepare_mixed_disposition(
        request(original, comment="Претензия"), original, tenant_id=TENANT
    )
    canonical = (
        b'{"comment_hash":"427b008d41ef0db2a0cccc3699007c38e86b49f9af6645b7043655a3513e5a4d",'
        b'"disposition_type":"disposed","domain":"mixed_customer_return_disposition",'
        b'"reason_code":"damaged","target":{"batch_id":"00000000-0000-0000-0000-000000000004",'
        b'"intake_operation_id":"00000000-0000-0000-0000-000000000002",'
        b'"inventory_kind":"legacy_pack","line_id":"00000000-0000-0000-0000-000000000003"},'
        b'"tenant_id":"00000000-0000-0000-0000-000000000001","version":1}'
    )
    expected = "57f7d0bd0fd33cfb5ae3eb5a681ae2b93df6c2ee46fa43a46a8ddf29606dbcc9"
    assert sha256(canonical).hexdigest() == expected
    assert prepared.request_hash == expected
    assert "Претензия".encode() not in canonical


def test_trusted_snapshot_quantity_is_not_a_client_hash_field() -> None:
    original = source(kind="physical_unit")
    proposed = request(original)
    first = prepare_mixed_disposition(proposed, original, tenant_id=TENANT)
    second = prepare_mixed_disposition(
        proposed, original.model_copy(update={"base_units": 62}), tenant_id=TENANT
    )
    assert first.request_hash == second.request_hash
    assert isinstance(first.target, MixedPhysicalDispositionSnapshot)
    assert isinstance(second.target, MixedPhysicalDispositionSnapshot)
    assert (first.target.base_units, second.target.base_units) == (31, 62)


def test_physical_snapshot_rejects_pack_label() -> None:
    original = source(kind="physical_unit")
    with pytest.raises(ValidationError, match="base-unit label"):
        prepare_mixed_disposition(
            request(original), original.model_copy(update={"unit_label": "уп."}), tenant_id=TENANT
        )


@pytest.mark.parametrize("invalid", [True, 1.0, "1"], ids=["bool", "float", "string"])
def test_prepared_version_is_a_strict_integer(invalid: object) -> None:
    original = source()
    prepared = prepare_mixed_disposition(request(original), original, tenant_id=TENANT)
    with pytest.raises(ValidationError, match="version must be an integer"):
        PreparedMixedDisposition.model_validate({**prepared.model_dump(), "version": invalid})


@pytest.mark.parametrize("invalid", [True, 1.0, "31"], ids=["bool", "float", "string"])
def test_physical_snapshot_base_units_is_a_strict_integer(invalid: object) -> None:
    original = source(kind="physical_unit")
    prepared = prepare_mixed_disposition(request(original), original, tenant_id=TENANT)
    with pytest.raises(ValidationError):
        MixedPhysicalDispositionSnapshot.model_validate(
            {**prepared.target.model_dump(), "base_units": invalid}
        )


@pytest.mark.parametrize("kind", ["legacy_pack", "physical_unit"])
def test_prepared_document_roundtrips_through_decoded_json(kind: InventoryKind) -> None:
    original = source(kind=kind)
    prepared = prepare_mixed_disposition(request(original), original, tenant_id=TENANT)
    restored = PreparedMixedDisposition.model_validate(json.loads(prepared.model_dump_json()))
    assert restored == prepared
