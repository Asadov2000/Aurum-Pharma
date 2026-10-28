"""Atomic producer trust boundaries, independently of a database or live sync."""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import replace
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AurumError, ConflictError, PermissionDeniedError
from app.domains.pos import retail_checkout_event_producer as producer_module
from app.domains.pos.retail_checkout_event_models import (
    RetailCheckoutEventSnapshot,
    RetailEventCaptureState,
)
from app.domains.pos.retail_checkout_event_producer import RetailCheckoutEventProducer
from app.domains.pos.retail_checkout_event_repository import RetailCheckoutEventRepository
from app.domains.pos.retail_checkout_event_service import RetailCheckoutEventCandidate
from app.domains.pos.retail_checkout_origin_service import RetailCheckoutOriginEvidence
from app.domains.pos.retail_sale_event_storage import decode_retail_sale_event
from app.domains.sync.repository import ReservedStreamPosition
from tests.domains.pos.test_retail_checkout_origin import origin
from tests.domains.pos.test_retail_checkout_source import OPERATION, result
from tests.domains.pos.test_retail_sale_events import Kind


class Session:
    def __init__(self) -> None:
        self.new: set[object] = set()
        self.dirty: set[object] = set()
        self.deleted: set[object] = set()
        self.depth = 0
        self.events: list[str] = []

    @property
    @contextmanager
    def no_autoflush(self) -> Iterator[None]:
        self.depth += 1
        try:
            yield
        finally:
            self.depth -= 1

    @asynccontextmanager
    async def begin_nested(self) -> AsyncIterator[None]:
        self.events.append("savepoint")
        try:
            yield
        except Exception:
            self.events.append("rollback")
            raise
        else:
            self.events.append("release")


class Repository:
    def __init__(self) -> None:
        self.session = Session()
        self.saved: RetailCheckoutEventSnapshot | None = None
        self.state: RetailEventCaptureState | None = RetailEventCaptureState(True, True)
        self.reserved = ReservedStreamPosition(
            stream_id=UUID(int=94),
            origin_node_id=origin().origin_node_id,
            writer_epoch=origin().writer_epoch,
            sequence=11,
            previous_checksum="a" * 64,
            previous_projection_checksum="b" * 64,
        )
        self.stored: dict[str, object] | None = None
        self.failure: str | None = None

    def checked(self, tenant_id: UUID, sale_id: UUID, operation_id: UUID) -> None:
        source = result("physical")
        assert (tenant_id, sale_id, operation_id) == (source.tenant_id, source.sale_id, OPERATION)
        assert self.session.depth > 0

    async def read(
        self, tenant_id: UUID, sale_id: UUID, operation_id: UUID
    ) -> RetailCheckoutEventSnapshot | None:
        self.checked(tenant_id, sale_id, operation_id)
        self.session.events.append("read")
        return self.saved

    async def capture_state(
        self, tenant_id: UUID, sale_id: UUID, operation_id: UUID
    ) -> RetailEventCaptureState | None:
        self.checked(tenant_id, sale_id, operation_id)
        self.session.events.append("capture")
        return self.state

    async def reserve(
        self, tenant_id: UUID, sale_id: UUID, operation_id: UUID
    ) -> ReservedStreamPosition:
        self.checked(tenant_id, sale_id, operation_id)
        self.session.events.append("reserve")
        return self.reserved

    async def store(
        self, tenant_id: UUID, sale_id: UUID, operation_id: UUID, **values: object
    ) -> None:
        self.checked(tenant_id, sale_id, operation_id)
        self.session.events.append("store")
        if self.failure == "store":
            raise RuntimeError("Injected store failure")
        self.stored = values

    async def validate_completion(self) -> None:
        self.session.events.append("validate")
        if self.failure == "validate":
            raise RuntimeError("Injected completion validation failure")


def install_evidence(
    monkeypatch: pytest.MonkeyPatch,
    repository: Repository,
    kind: Kind = "physical",
    *,
    missing_origin: bool = False,
    denied: bool = False,
    revoked: bool = False,
) -> None:
    async def evidence(_self: object, **arguments: object) -> RetailCheckoutOriginEvidence:
        source = result(kind)
        assert arguments == {
            "tenant_id": source.tenant_id,
            "sale_id": source.sale_id,
            "operation_id": OPERATION,
            "actor_id": source.cashier_id,
            "allowed_branch_ids": {source.branch_id},
        }
        assert repository.session.depth > 0
        repository.session.events.append("evidence")
        if denied:
            raise PermissionDeniedError("Source authority denied")
        return RetailCheckoutOriginEvidence(source, None if missing_origin else origin(kind))

    async def actor(_self: object, tenant_id: UUID, actor_id: UUID, branch_id: UUID) -> None:
        source = result(kind)
        assert (tenant_id, actor_id, branch_id) == (
            source.tenant_id,
            source.cashier_id,
            source.branch_id,
        )
        repository.session.events.append("authority")
        if revoked:
            raise PermissionDeniedError("Authority revoked before completion")

    monkeypatch.setattr(producer_module.RetailCheckoutOriginService, "read_evidence", evidence)
    monkeypatch.setattr(producer_module.UnitCartRepository, "assert_actor", actor)


async def produce(repository: Repository) -> RetailCheckoutEventCandidate | None:
    source = result("physical")
    return await RetailCheckoutEventProducer(
        cast(RetailCheckoutEventRepository, repository)
    ).produce(
        tenant_id=source.tenant_id,
        sale_id=source.sale_id,
        operation_id=OPERATION,
        actor_id=source.cashier_id,
        allowed_branch_ids={source.branch_id},
    )


def snapshot(candidate: RetailCheckoutEventCandidate, kind: Kind) -> RetailCheckoutEventSnapshot:
    source = result(kind)
    return RetailCheckoutEventSnapshot(
        tenant_id=source.tenant_id,
        sale_id=source.sale_id,
        operation_id=OPERATION,
        event_id=OPERATION,
        source_kind=kind,
        branch_id=source.branch_id,
        register_id=source.register_id,
        cashier_id=source.cashier_id,
        origin_node_id=candidate.anchor.origin_node_id,
        writer_epoch=candidate.anchor.writer_epoch,
        sequence=candidate.anchor.sequence,
        previous_source_checksum=candidate.anchor.previous_source_checksum,
        previous_projection_checksum=candidate.anchor.previous_projection_checksum,
        event_fingerprint=candidate.anchor.event_fingerprint,
        record=candidate.record,
    )


@pytest.mark.parametrize("kind", ["physical", "mixed"])
async def test_current_required_checkout_reserves_builds_and_stores_once_inside_savepoint(
    monkeypatch: pytest.MonkeyPatch, kind: Kind
) -> None:
    repository = Repository()
    install_evidence(monkeypatch, repository, kind)
    candidate = await produce(repository)
    assert candidate is not None
    assert decode_retail_sale_event(candidate.record, anchor=candidate.anchor) == result(kind)
    assert candidate.anchor.sequence == repository.reserved.sequence
    assert candidate.anchor.previous_source_checksum == repository.reserved.previous_checksum
    assert repository.stored == {
        "record": candidate.record,
        "previous_source_checksum": candidate.anchor.previous_source_checksum,
        "previous_projection_checksum": candidate.anchor.previous_projection_checksum,
        "event_fingerprint": candidate.anchor.event_fingerprint,
    }
    events = repository.session.events
    assert events.count("evidence") == events.count("reserve") == events.count("store") == 1
    assert events.index("read") < events.index("capture") < events.index("reserve")
    assert events.index("savepoint") < events.index("reserve")
    assert events.index("store") < events.index("validate") < events.index("authority")
    assert events[-1] == "release"


async def test_default_off_checkout_never_reserves_or_creates_an_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository()
    repository.state = RetailEventCaptureState(False, True)
    install_evidence(monkeypatch, repository)
    assert await produce(repository) is None
    assert "reserve" not in repository.session.events and repository.stored is None


async def test_dormant_historical_checkout_is_not_backfilled_after_a_later_enable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository()
    repository.state = RetailEventCaptureState(False, False)
    install_evidence(monkeypatch, repository)
    assert await produce(repository) is None
    assert "reserve" not in repository.session.events


async def test_missing_required_history_cannot_be_rebuilt_in_another_transaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository()
    repository.state = RetailEventCaptureState(True, False)
    install_evidence(monkeypatch, repository)
    with pytest.raises(ConflictError):
        await produce(repository)
    assert "reserve" not in repository.session.events and repository.stored is None


@pytest.mark.parametrize("kind", ["physical", "mixed"])
async def test_stored_replay_uses_authorized_evidence_before_current_transaction_checks(
    monkeypatch: pytest.MonkeyPatch, kind: Kind
) -> None:
    repository = Repository()
    install_evidence(monkeypatch, repository, kind)
    original = await produce(repository)
    assert original is not None
    repository.saved = snapshot(original, kind)
    repository.state = RetailEventCaptureState(True, False)
    repository.reserved = replace(repository.reserved, origin_node_id=UUID(int=99), writer_epoch=8)
    repository.session.events.clear()
    assert await produce(repository) == original
    assert repository.session.events[0] == "evidence"
    assert "read" in repository.session.events
    assert not {"capture", "reserve", "store", "validate"} & set(repository.session.events)


async def test_corrupt_stored_bytes_are_rejected_without_new_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository()
    install_evidence(monkeypatch, repository)
    candidate = await produce(repository)
    assert candidate is not None
    repository.saved = replace(snapshot(candidate, "physical"), record=b"not JSON")
    repository.session.events.clear()
    with pytest.raises(AurumError, match="invalid"):
        await produce(repository)
    assert "reserve" not in repository.session.events


@pytest.mark.parametrize(
    "field", ["tenant_id", "sale_id", "operation_id", "event_id", "cashier_id"]
)
async def test_stored_record_metadata_cannot_be_detached_from_its_authorized_source(
    monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    repository = Repository()
    install_evidence(monkeypatch, repository)
    candidate = await produce(repository)
    assert candidate is not None
    repository.saved = replace(snapshot(candidate, "physical"), **{field: UUID(int=99)})
    repository.session.events.clear()
    with pytest.raises(AurumError):
        await produce(repository)
    assert "reserve" not in repository.session.events


@pytest.mark.parametrize("failure", ["store", "validate", "authority"])
async def test_store_validation_or_final_authority_failure_propagates_and_rolls_back_savepoint(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    repository = Repository()
    repository.failure = failure
    install_evidence(monkeypatch, repository, revoked=failure == "authority")
    expected = PermissionDeniedError if failure == "authority" else RuntimeError
    with pytest.raises(expected):
        await produce(repository)
    assert repository.session.events[-1] == "rollback"
    assert "release" not in repository.session.events


async def test_source_denial_precedes_any_stored_record_or_capture_state_observation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository()
    install_evidence(monkeypatch, repository, denied=True)
    with pytest.raises(PermissionDeniedError):
        await produce(repository)
    assert repository.session.events == ["evidence"]


@pytest.mark.parametrize("pending", ["new", "dirty", "deleted"])
async def test_pending_caller_state_is_preserved_and_never_queried(pending: str) -> None:
    repository = Repository()
    marker = object()
    collection = cast(set[object], getattr(repository.session, pending))
    collection.add(marker)
    with pytest.raises(ConflictError, match="clean session"):
        await produce(repository)
    assert collection == {marker}
    assert repository.session.events == []


def install_hook_repository(monkeypatch: pytest.MonkeyPatch, repository: Repository) -> None:
    def factory(session: AsyncSession) -> Repository:
        assert session is repository.session
        return repository

    monkeypatch.setattr(producer_module, "RetailCheckoutEventRepository", factory)


async def hook(repository: Repository) -> RetailCheckoutEventCandidate | None:
    source = result("physical")
    return await producer_module.produce_retail_checkout_event(
        cast(AsyncSession, repository.session),
        source,
        actor_id=source.cashier_id,
        allowed_branch_ids={source.branch_id},
    )


@pytest.mark.parametrize(
    "capture",
    [
        pytest.param(None, id="no-origin-capture"),
        pytest.param(RetailEventCaptureState(False, True), id="dormant-current-checkout"),
        pytest.param(RetailEventCaptureState(False, False), id="dormant-historical-checkout"),
    ],
)
async def test_convenience_hook_default_off_only_reads_capture_without_observing_financial_source(
    monkeypatch: pytest.MonkeyPatch, capture: RetailEventCaptureState | None
) -> None:
    repository = Repository()
    repository.state = capture
    install_hook_repository(monkeypatch, repository)
    # A denied evidence stub ensures no source/receipt/payment recovery runs
    # before the dormant hook returns. Standalone produce remains auth-first.
    install_evidence(monkeypatch, repository, denied=True)
    assert await hook(repository) is None
    assert repository.session.events == ["capture"]
    assert repository.stored is None
    assert repository.session.depth == 0


async def test_required_hook_delegates_authorized_atomic_production_and_final_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository()
    install_hook_repository(monkeypatch, repository)
    install_evidence(monkeypatch, repository)
    candidate = await hook(repository)
    assert candidate is not None
    assert decode_retail_sale_event(candidate.record, anchor=candidate.anchor) == result("physical")
    events = repository.session.events
    assert events[0] == "capture"
    assert events.count("evidence") == events.count("reserve") == events.count("store") == 1
    assert events.index("evidence") < events.index("read") < events.index("reserve")
    assert events.index("store") < events.index("validate") < events.index("authority")
    assert events[-1] == "release"


async def test_required_hook_cannot_bypass_current_source_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository()
    install_hook_repository(monkeypatch, repository)
    install_evidence(monkeypatch, repository, denied=True)
    with pytest.raises(PermissionDeniedError):
        await hook(repository)
    assert repository.session.events == ["capture", "evidence"]
    assert repository.stored is None


async def test_required_hook_preserves_committed_replay_before_current_transaction_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository()
    install_evidence(monkeypatch, repository)
    candidate = await produce(repository)
    assert candidate is not None
    repository.saved = snapshot(candidate, "physical")
    repository.state = RetailEventCaptureState(True, False)
    repository.reserved = replace(repository.reserved, origin_node_id=UUID(int=99), writer_epoch=8)
    repository.session.events.clear()
    install_hook_repository(monkeypatch, repository)
    assert await hook(repository) == candidate
    assert repository.session.events == ["capture", "evidence", "read"]
    assert not {"reserve", "store", "validate"} & set(repository.session.events)


async def test_required_hook_cannot_backfill_a_missing_committed_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = Repository()
    repository.state = RetailEventCaptureState(True, False)
    install_hook_repository(monkeypatch, repository)
    install_evidence(monkeypatch, repository)
    with pytest.raises(ConflictError):
        await hook(repository)
    assert repository.session.events[:3] == ["capture", "evidence", "read"]
    assert "reserve" not in repository.session.events


@pytest.mark.parametrize("pending", ["new", "dirty", "deleted"])
async def test_hook_refuses_pending_caller_state_before_even_capture_read(
    monkeypatch: pytest.MonkeyPatch, pending: str
) -> None:
    repository = Repository()
    install_hook_repository(monkeypatch, repository)
    marker = object()
    collection = cast(set[object], getattr(repository.session, pending))
    collection.add(marker)
    with pytest.raises(ConflictError, match="clean session"):
        await hook(repository)
    assert collection == {marker}
    assert repository.session.events == []
