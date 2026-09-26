"""Reject damaged stored deliveries and withhold receipts across failed commits."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.sync import offline_policy_service as service_module
from app.domains.sync.models import OfflinePolicyInbox
from app.domains.sync.offline_auth import (
    OFFLINE_AUTH_UNAVAILABLE_MESSAGE,
    OfflineAuthUnavailableError,
)
from app.domains.sync.schemas import OfflinePolicyDeliveryScopeV1, SignedOfflinePolicyDeliveryV1
from tests.domains.sync.offline_policy_fixtures import NOW, SigningFixture, binding, scope, uuid


def _row(delivery: SignedOfflinePolicyDeliveryV1) -> OfflinePolicyInbox:
    return OfflinePolicyInbox(
        id=delivery.claims.delivery_id,
        **delivery.claims.scope.model_dump(mode="python"),
        delivery_sequence=delivery.claims.delivery_sequence,
        tenant_access_revision=delivery.claims.policy.version,
        issued_at=delivery.claims.issued_at,
        claims_hash=delivery.claims_hash,
        status="pending_protected_acceptance",
        payload=delivery.model_dump(mode="json"),
    )


@dataclass
class _Database:
    current: OfflinePolicyInbox | None = None
    staged: OfflinePolicyInbox | None = None
    fail_commit: bool = False
    suppress_body: bool = False
    fail_read: bool = False
    fail_insert: bool = False
    calls: int = 0

    def factory(self) -> AsyncSession:
        self.calls += 1
        return cast(AsyncSession, self)

    async def __aenter__(self) -> _Database:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def execute(self, _statement: object, _parameters: dict[str, str]) -> None:
        return None

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        try:
            yield
            if self.fail_commit:
                raise RuntimeError("synthetic private commit detail")
            if self.staged is not None:
                self.current = self.staged
        except Exception:
            if not self.suppress_body:
                raise
        finally:
            self.staged = None


class _Repository:
    def __init__(self, database: _Database) -> None:
        self.db = database

    async def lock_target(self, _scope: OfflinePolicyDeliveryScopeV1) -> None:
        return None

    async def assert_enrollment(self, _scope: OfflinePolicyDeliveryScopeV1) -> None:
        if self.db.fail_read:
            raise RuntimeError("synthetic private read detail")

    async def get_latest(self, _scope: OfflinePolicyDeliveryScopeV1) -> OfflinePolicyInbox | None:
        return self.db.current

    async def get_by_id(
        self, _scope: OfflinePolicyDeliveryScopeV1, _delivery_id: UUID
    ) -> OfflinePolicyInbox | None:
        return None

    async def insert(self, delivery: SignedOfflinePolicyDeliveryV1) -> None:
        self.db.staged = _row(delivery)
        if self.db.fail_insert:
            raise RuntimeError("synthetic private insert detail")


def _service(
    database: _Database, signing: SigningFixture, monkeypatch: pytest.MonkeyPatch
) -> service_module.OfflinePolicyInboxService:
    monkeypatch.setattr(service_module, "OfflinePolicyInboxRepository", _Repository)
    return service_module.OfflinePolicyInboxService(
        session_factory=database.factory,
        expected_scope=scope(),
        expected_device=binding(),
        signature_verifier=signing.verifier,
    )


@pytest.mark.parametrize(
    "corruption",
    ["tenant", "sequence", "revision", "time", "hash", "status", "payload", "signature"],
)
async def test_stored_policy_requires_valid_signature_and_exact_indexed_columns(
    corruption: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    signing = SigningFixture()
    delivery = signing.signed_delivery()
    row = _row(delivery)
    if corruption == "payload":
        row.payload = {"unexpected": "synthetic-private-value"}
    elif corruption == "signature":
        row.payload = delivery.model_copy(update={"signature": "00" * 64}).model_dump(mode="json")
    else:
        field, value = {
            "tenant": ("tenant_id", uuid(99)),
            "sequence": ("delivery_sequence", 99),
            "revision": ("tenant_access_revision", 99),
            "time": ("issued_at", NOW + timedelta(seconds=1)),
            "hash": ("claims_hash", "00" * 32),
            "status": ("status", "active"),
        }[corruption]
        setattr(row, field, value)
    database = _Database(current=row)
    with pytest.raises(OfflineAuthUnavailableError) as error:
        await _service(database, signing, monkeypatch).read_latest()
    assert str(error.value) == OFFLINE_AUTH_UNAVAILABLE_MESSAGE
    assert error.value.__suppress_context__
    assert database.staged is None


@pytest.mark.parametrize("failure", ["commit", "suppressed-insert", "suppressed-read"])
async def test_failed_transaction_cannot_publish_receipt_or_empty_success(
    failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    signing = SigningFixture()
    database = _Database(
        fail_commit=failure == "commit",
        suppress_body=failure.startswith("suppressed"),
        fail_insert=failure == "suppressed-insert",
        fail_read=failure == "suppressed-read",
    )
    service = _service(database, signing, monkeypatch)
    with pytest.raises(OfflineAuthUnavailableError):
        await service.stage(signing.signed_delivery())
    assert database.current is None
    assert database.staged is None
    if failure == "suppressed-read":
        with pytest.raises(OfflineAuthUnavailableError):
            await service.read_latest()


async def test_invalid_signature_denies_before_database_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signing = SigningFixture()
    database = _Database()
    tampered = signing.signed_delivery().model_copy(update={"signature": "00" * 64})
    with pytest.raises(OfflineAuthUnavailableError):
        await _service(database, signing, monkeypatch).stage(tampered)
    assert database.calls == 0
