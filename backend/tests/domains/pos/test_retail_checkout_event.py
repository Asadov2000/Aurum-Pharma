"""Source-bound candidate bytes do not reserve, publish or authenticate a stream."""

import json
from dataclasses import FrozenInstanceError, fields, replace
from typing import cast
from uuid import UUID

import pytest

from app.core.errors import AurumError, ConflictError, PermissionDeniedError
from app.domains.pos import retail_sale_event_storage as storage
from app.domains.pos.retail_checkout_event_service import (
    RetailCheckoutEventCandidate,
    RetailCheckoutEventService,
)
from app.domains.pos.retail_checkout_origin_repository import RetailCheckoutOriginRepository
from app.domains.pos.retail_checkout_origin_service import RetailCheckoutOriginService
from app.domains.pos.retail_sale_event_storage import decode_retail_sale_event
from app.domains.pos.retail_sale_events import RetailSaleStreamPosition
from app.domains.sync.integrity import canonical_json_bytes
from tests.domains.pos.test_retail_checkout_origin import (
    Repository,
    install_source,
    origin,
)
from tests.domains.pos.test_retail_checkout_origin import read as read_origin
from tests.domains.pos.test_retail_checkout_source import OPERATION, result
from tests.domains.pos.test_retail_sale_events import Kind


def position() -> RetailSaleStreamPosition:
    captured = origin()
    return RetailSaleStreamPosition(
        origin_node_id=captured.origin_node_id,
        writer_epoch=captured.writer_epoch,
        sequence=11,
        previous_source_checksum="a" * 64,
        previous_projection_checksum="b" * 64,
    )


async def prepare(
    repository: Repository, supplied: RetailSaleStreamPosition | None = None
) -> RetailCheckoutEventCandidate:
    source = result("physical")
    return await RetailCheckoutEventService(
        cast(RetailCheckoutOriginRepository, repository)
    ).prepare(
        tenant_id=source.tenant_id,
        sale_id=source.sale_id,
        operation_id=OPERATION,
        actor_id=source.cashier_id,
        allowed_branch_ids={source.branch_id},
        position=position() if supplied is None else supplied,
    )


@pytest.mark.parametrize("kind", ["physical", "mixed"])
async def test_candidate_preserves_the_authorized_source_and_is_repeatable_bounded_bytes(
    monkeypatch: pytest.MonkeyPatch, kind: Kind
) -> None:
    repository = Repository(origin(kind))
    install_source(monkeypatch, repository, kind)
    source = result(kind)
    original = source.model_dump(mode="json")
    candidate = await prepare(repository)
    assert repository.session.events == ["source", "origin", "authority"]
    assert repository.calls == [(source.tenant_id, source.sale_id, OPERATION)]
    assert type(candidate.record) is bytes
    assert 0 < len(candidate.record) <= storage.MAX_RECORD_BYTES
    stored = json.loads(candidate.record)
    assert candidate.record == canonical_json_bytes(stored)
    envelope = stored["envelope"]
    assert stored["record_kind"] == "retail_sale_event"
    assert envelope["schema_version"] == (3 if kind == "physical" else 4)
    assert envelope["event_type"] == f"pos.sale.completed.v{envelope['schema_version']}"
    assert envelope["event_id"] == envelope["operation_id"] == str(OPERATION)
    assert envelope["aggregate_id"] == str(source.sale_id)
    assert envelope["payload"] == original
    assert decode_retail_sale_event(candidate.record, anchor=candidate.anchor) == source
    assert candidate.anchor.origin_node_id == origin(kind).origin_node_id
    assert candidate.anchor.writer_epoch == origin(kind).writer_epoch
    assert candidate.anchor.sequence == 11
    assert await prepare(repository) == candidate
    assert repository.session.events == ["source", "origin", "authority"] * 2
    assert source.model_dump(mode="json") == original
    assert not repository.session.autoflush_suppressed


async def test_candidate_exposes_only_frozen_anchor_and_immutable_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository(origin())
    install_source(monkeypatch, repository)
    candidate = await prepare(repository)
    assert {field.name for field in fields(candidate)} == {"anchor", "record"}
    with pytest.raises(FrozenInstanceError):
        candidate.record = b"changed"
    with pytest.raises(ValueError):
        candidate.anchor.sequence = 12
    exported = candidate.anchor.model_dump(mode="json")
    exported["sequence"] = 12
    assert candidate.anchor.sequence == 11
    assert decode_retail_sale_event(candidate.record, anchor=candidate.anchor) == result("physical")


async def test_supplied_sequence_and_checksums_remain_unreserved_candidate_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository(origin())
    install_source(monkeypatch, repository)
    first = await prepare(repository)
    supplied = position().model_copy(
        update={
            "sequence": 27,
            "previous_source_checksum": "c" * 64,
            "previous_projection_checksum": "d" * 64,
        }
    )
    second = await prepare(repository, supplied)
    assert second.record != first.record and second.anchor != first.anchor
    assert second.anchor.sequence == 27
    assert second.anchor.previous_source_checksum == "c" * 64
    assert second.anchor.previous_projection_checksum == "d" * 64
    assert decode_retail_sale_event(second.record, anchor=second.anchor) == result("physical")
    assert repository.session.events == ["source", "origin", "authority"] * 2


@pytest.mark.parametrize("field", ["origin_node_id", "writer_epoch"])
async def test_candidate_writer_must_match_the_captured_historical_origin(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    repository = Repository(origin())
    install_source(monkeypatch, repository)
    wrong: object = UUID(int=99) if field == "origin_node_id" else 8
    with pytest.raises(ConflictError, match="captured checkout writer"):
        await prepare(repository, position().model_copy(update={field: wrong}))
    assert repository.session.events == ["source", "origin", "authority"]


async def test_missing_historical_origin_cannot_be_replaced_with_a_supplied_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository(None)
    install_source(monkeypatch, repository)
    with pytest.raises(ConflictError, match="captured checkout origin"):
        await prepare(repository)
    assert repository.session.events == ["source", "origin", "authority"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param("sequence", True, id="boolean-sequence"),
        pytest.param("writer_epoch", True, id="boolean-epoch"),
        pytest.param("sequence", "11", id="string-sequence"),
        pytest.param("sequence", 0, id="zero-sequence"),
        pytest.param("sequence", 2**63, id="overflow-sequence"),
        pytest.param("origin_node_id", "not-a-uuid", id="invalid-node"),
        pytest.param("previous_source_checksum", "A" * 64, id="uppercase-source-checksum"),
        pytest.param("previous_projection_checksum", "bad", id="invalid-projection-checksum"),
    ],
)
async def test_unchecked_copies_are_revalidated_before_event_construction(
    monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    repository = Repository(origin())
    install_source(monkeypatch, repository)
    invalid = position().model_copy(update={field: value})
    with pytest.raises(ValueError):
        await prepare(repository, invalid)
    assert repository.session.events == ["source", "origin", "authority"]


async def test_unchecked_construct_cannot_smuggle_a_boolean_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository(origin())
    install_source(monkeypatch, repository)
    invalid = RetailSaleStreamPosition.model_construct(
        **{**position().model_dump(mode="python"), "sequence": True}
    )
    with pytest.raises(ValueError):
        await prepare(repository, invalid)


@pytest.mark.parametrize("stage", ["source", "final-authority"])
async def test_access_is_checked_before_parsing_an_invalid_candidate_position(
    monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    repository = Repository(origin())
    install_source(
        monkeypatch,
        repository,
        denied=stage == "source",
        revoked=stage == "final-authority",
    )
    invalid = position().model_copy(update={"sequence": False})
    with pytest.raises(PermissionDeniedError):
        await prepare(repository, invalid)
    assert bool(repository.calls) is (stage != "source")


async def test_invalid_origin_identity_is_rejected_before_candidate_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository(replace(origin(), created_by=UUID(int=99)))
    install_source(monkeypatch, repository)
    with pytest.raises(AurumError, match="differs from its source"):
        await prepare(repository)


@pytest.mark.parametrize("pending", ["new", "dirty", "deleted"])
async def test_pending_caller_objects_are_preserved_without_attempting_queries(
    pending: str,
) -> None:
    repository = Repository(origin())
    marker = object()
    pending_objects = cast(set[object], getattr(repository.session, pending))
    pending_objects.add(marker)
    with pytest.raises(ConflictError, match="clean session"):
        await prepare(repository)
    assert pending_objects == {marker}
    assert repository.calls == [] and repository.session.events == []


async def test_record_size_limit_is_preserved_by_the_source_backed_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository(origin())
    install_source(monkeypatch, repository)
    monkeypatch.setattr(storage, "MAX_RECORD_BYTES", 1)
    with pytest.raises(ValueError, match="size limit"):
        await prepare(repository)
    assert repository.session.events == ["source", "origin", "authority"]


@pytest.mark.parametrize("captured", [True, False], ids=["captured", "historical-absent"])
async def test_evidence_returns_one_authorized_source_and_keeps_legacy_read_compatible(
    monkeypatch: pytest.MonkeyPatch, captured: bool
) -> None:
    saved = origin() if captured else None
    repository = Repository(saved)
    install_source(monkeypatch, repository)
    source = result("physical")
    evidence = await RetailCheckoutOriginService(
        cast(RetailCheckoutOriginRepository, repository)
    ).read_evidence(
        tenant_id=source.tenant_id,
        sale_id=source.sale_id,
        operation_id=OPERATION,
        actor_id=source.cashier_id,
        allowed_branch_ids={source.branch_id},
    )
    assert evidence.result == source and evidence.origin == saved
    assert repository.session.events == ["source", "origin", "authority"]
    assert await read_origin(repository) == saved
    assert repository.session.events == ["source", "origin", "authority"] * 2
