"""Cloud issuance returns a signed lease only after trusted durable commit."""

from __future__ import annotations

import asyncio
import secrets
import warnings
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from uuid import UUID

import pytest
from Crypto.Signature import eddsa

from app.domains.sync.offline_auth import (
    OFFLINE_AUTH_UNAVAILABLE_MESSAGE,
    OfflineAuthUnavailableError,
)
from app.domains.sync.offline_auth_lease import (
    DenyAllOfflineLeaseVerifierV1,
    offline_auth_lease_claims_bytes,
    offline_auth_lease_claims_hash,
    runtime_offline_auth_lease_verifier,
)
from app.domains.sync.offline_lease_issuance_state import CloudOfflineLeaseIssuanceStateV1
from app.domains.sync.offline_lease_issuer import CloudOfflineLeaseIssuerV1
from app.domains.sync.offline_lease_signature import Ed25519OfflineLeaseSignatureVerifier
from app.domains.sync.offline_tenant_access import CloudTenantAccessPolicyV1
from app.domains.sync.schemas import (
    OfflineAuthDeviceBindingV0,
    OfflineAuthLeaseReferenceV1,
    OfflineAuthScopeV0,
    OfflinePosCommand,
    SignedOfflineAuthLeaseV1,
)

NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)
DEADLINE_FIELDS = (
    "admission_expires_at",
    "credential_expires_at",
    "device_expires_at",
    "policy_expires_at",
)
CONFIGURATION_ERROR = "Cloud offline signing configuration is invalid"


def _uuid(value: int) -> UUID:
    return UUID(f"{value:08x}-0000-4000-8000-000000000001")


def _state() -> CloudOfflineLeaseIssuanceStateV1:
    return CloudOfflineLeaseIssuanceStateV1(
        scope=OfflineAuthScopeV0(
            activation_id=_uuid(1),
            tenant_id=_uuid(2),
            branch_id=_uuid(3),
            edge_node_id=_uuid(4),
            register_id=_uuid(5),
            writer_epoch=6,
            user_id=_uuid(7),
            capability="cash_sale_v1",
        ),
        device_binding=OfflineAuthDeviceBindingV0(
            method="tpm2", key_id=_uuid(8), spki_sha256="ab" * 32
        ),
        policy_revision=17,
        tenant_access_policy=CloudTenantAccessPolicyV1(
            tenant_id=_uuid(2),
            version=7,
            tenant_status="active",
            mode="full",
            blocked_modules=(),
            expires_at=None,
        ),
        subject_revision=19,
        allowed_commands=("receipt.print", "sale.cash.complete"),
        admission=OfflineAuthLeaseReferenceV1(reference_id=_uuid(9), revision=3),
        local_credential=OfflineAuthLeaseReferenceV1(reference_id=_uuid(10), revision=5),
        reauthentication_policy=OfflineAuthLeaseReferenceV1(reference_id=_uuid(11), revision=7),
        admission_expires_at=NOW + timedelta(days=60),
        credential_expires_at=NOW + timedelta(days=59),
        device_expires_at=NOW + timedelta(days=58),
        policy_expires_at=NOW + timedelta(days=57),
        status="active",
    )


@dataclass
class _Provider:
    state: CloudOfflineLeaseIssuanceStateV1 | None
    now: datetime = NOW
    error_at: str | None = None
    suppress_errors: bool = False
    events: list[str] = field(default_factory=list)
    selectors: list[OfflineAuthScopeV0] = field(default_factory=list)
    staged: list[SignedOfflineAuthLeaseV1] = field(default_factory=list)
    committed: list[SignedOfflineAuthLeaseV1] = field(default_factory=list)
    commit_started: asyncio.Event | None = None
    release_commit: asyncio.Event | None = None

    def _record(self, stage: str) -> None:
        self.events.append(stage)
        if stage == self.error_at:
            raise RuntimeError("Private synthetic transaction diagnostic")

    async def read_current_authorization(
        self, scope: OfflineAuthScopeV0
    ) -> CloudOfflineLeaseIssuanceStateV1 | None:
        self._record("read")
        self.selectors.append(scope)
        return self.state

    async def read_cloud_time(self) -> datetime:
        self._record("time")
        return self.now

    async def record_issued_lease(self, lease: SignedOfflineAuthLeaseV1) -> None:
        self._record("record")
        self.staged.append(lease)

    async def commit(self) -> None:
        self._record("commit")
        if self.commit_started is not None:
            self.commit_started.set()
        if self.release_commit is not None:
            await self.release_commit.wait()
        self.committed.extend(self.staged)

    @asynccontextmanager
    async def serialized(self) -> AsyncIterator[_Provider]:
        self._record("enter")
        try:
            yield self
        except Exception:
            self.events.append("rollback")
            self.staged.clear()
            if not self.suppress_errors:
                raise
        finally:
            self._record("exit")


@dataclass(frozen=True)
class _Harness:
    provider: _Provider
    issuer: CloudOfflineLeaseIssuerV1
    public_key: bytes
    scope: OfflineAuthScopeV0

    @property
    def verifier(self) -> Ed25519OfflineLeaseSignatureVerifier:
        return Ed25519OfflineLeaseSignatureVerifier({_uuid(12): self.public_key})


def _harness(*, key_until: datetime = NOW + timedelta(days=90)) -> _Harness:
    state = _state()
    provider = _Provider(state)
    # Random private seed remains in memory and never becomes fixture repr/output.
    private_seed = secrets.token_bytes(32)
    public_key = cast(
        bytes, eddsa.import_private_key(private_seed).public_key().export_key(format="raw")
    )
    issuer = CloudOfflineLeaseIssuerV1(
        provider,
        signing_key_id=_uuid(12),
        private_seed=private_seed,
        key_valid_from=NOW,
        key_valid_until=key_until,
    )
    return _Harness(provider, issuer, public_key, state.scope)


@pytest.fixture
def harness() -> _Harness:
    return _harness()


async def _assert_denied(harness: _Harness, scope: OfflineAuthScopeV0 | None = None) -> None:
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter("always")
        with pytest.raises(OfflineAuthUnavailableError) as error:
            await harness.issuer.issue(harness.scope if scope is None else scope)
    assert not emitted
    assert str(error.value) == OFFLINE_AUTH_UNAVAILABLE_MESSAGE
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True


async def test_issue_uses_trusted_rights_revisions_and_real_signature(harness: _Harness) -> None:
    lease = await harness.issuer.issue(harness.scope)
    state = harness.provider.state
    assert state is not None
    claims = lease.claims
    assert claims.scope == state.scope
    assert claims.device_binding == state.device_binding
    assert claims.allowed_commands == state.allowed_commands
    assert claims.policy_revision == state.policy_revision
    assert claims.tenant_access_revision == state.tenant_access_policy.version
    assert claims.subject_revision == state.subject_revision
    assert claims.admission == state.admission
    assert claims.local_credential == state.local_credential
    assert claims.reauthentication_policy == state.reauthentication_policy
    assert claims.issued_at == NOW
    assert claims.lease_id.version == 4
    assert claims.credential_audience == "aurum-edge-local-auth-v1"
    assert lease.signing_key_id == _uuid(12)
    assert lease.claims_hash == offline_auth_lease_claims_hash(claims)
    assert harness.provider.committed == [lease]
    assert harness.provider.selectors == [harness.scope]
    assert harness.provider.events == ["enter", "read", "time", "record", "commit", "exit"]
    assert await harness.verifier.verify(lease, offline_auth_lease_claims_bytes(claims)) is None
    second = await harness.issuer.issue(harness.scope)
    assert second.claims.lease_id != claims.lease_id
    altered_claims = claims.model_copy(update={"subject_revision": claims.subject_revision + 1})
    altered = lease.model_copy(
        update={
            "claims": altered_claims,
            "claims_hash": offline_auth_lease_claims_hash(altered_claims),
        }
    )
    with pytest.raises(OfflineAuthUnavailableError):
        await harness.verifier.verify(altered, offline_auth_lease_claims_bytes(altered_claims))


@pytest.mark.parametrize(
    ("restriction", "expected"),
    [
        ("full", ("receipt.print", "sale.cash.complete", "shift.close", "shift.open")),
        ("readonly", ("receipt.print",)),
        ("pos", ("receipt.print",)),
        ("inventory", ("receipt.print", "shift.close", "shift.open")),
    ],
)
async def test_signed_commands_intersect_current_tenant_policy(
    harness: _Harness, restriction: str, expected: tuple[OfflinePosCommand, ...]
) -> None:
    assert harness.provider.state is not None
    state = harness.provider.state
    policy = state.tenant_access_policy.model_copy(
        update={
            "mode": "readonly" if restriction == "readonly" else "full",
            "blocked_modules": (restriction,) if restriction in {"pos", "inventory"} else (),
        }
    )
    harness.provider.state = state.model_copy(
        update={
            "tenant_access_policy": policy,
            "allowed_commands": (
                "receipt.print",
                "sale.cash.complete",
                "shift.close",
                "shift.open",
            ),
        }
    )
    lease = await harness.issuer.issue(harness.scope)
    assert lease.claims.allowed_commands == expected
    assert lease.claims.tenant_access_revision == policy.version
    assert (
        await harness.verifier.verify(lease, offline_auth_lease_claims_bytes(lease.claims)) is None
    )


@pytest.mark.parametrize("offset", [-1, 0, 1])
async def test_policy_expiry_only_affects_new_lease_and_never_shortens_admission(
    harness: _Harness, offset: int
) -> None:
    assert harness.provider.state is not None
    state = harness.provider.state
    policy = state.tenant_access_policy.model_copy(
        update={"mode": "readonly", "expires_at": NOW + timedelta(microseconds=offset)}
    )
    harness.provider.state = state.model_copy(update={"tenant_access_policy": policy})
    lease = await harness.issuer.issue(harness.scope)
    assert lease.claims.allowed_commands == (
        ("receipt.print", "sale.cash.complete") if offset <= 0 else ("receipt.print",)
    )
    assert lease.claims.expires_at == NOW + timedelta(days=30)
    harness.provider.now = NOW + timedelta(seconds=1)
    renewed = await harness.issuer.issue(harness.scope)
    assert renewed.claims.allowed_commands == ("receipt.print", "sale.cash.complete")
    assert lease.claims.allowed_commands == (
        ("receipt.print", "sale.cash.complete") if offset <= 0 else ("receipt.print",)
    )


@pytest.mark.parametrize("invalid", ["missing", "tenant", "version", "archived", "empty"])
async def test_unusable_tenant_policy_never_stages_or_commits_lease(
    harness: _Harness, invalid: str
) -> None:
    assert harness.provider.state is not None
    state = harness.provider.state
    if invalid == "missing":
        harness.provider.state = state.model_copy(update={"tenant_access_policy": None})
    else:
        changes: dict[str, object] = {
            "tenant": {"tenant_id": _uuid(99)},
            "version": {"version": True},
            "archived": {"tenant_status": "archived", "expires_at": NOW - timedelta(days=1)},
            "empty": {"mode": "readonly"},
        }[invalid]
        harness.provider.state = state.model_copy(
            update={
                "tenant_access_policy": state.tenant_access_policy.model_copy(update=changes),
                "allowed_commands": ("sale.cash.complete",),
            }
        )
    await _assert_denied(harness)
    assert harness.provider.staged == []
    assert harness.provider.committed == []
    assert "record" not in harness.provider.events
    assert "commit" not in harness.provider.events


@pytest.mark.parametrize("deadline", [*DEADLINE_FIELDS, "key", "thirty-days"])
async def test_expiry_is_minimum_of_all_trusted_deadlines(deadline: str) -> None:
    expected = NOW + timedelta(days=30 if deadline == "thirty-days" else 2)
    harness = _harness(key_until=expected if deadline == "key" else NOW + timedelta(days=90))
    if deadline in DEADLINE_FIELDS:
        assert harness.provider.state is not None
        harness.provider.state = harness.provider.state.model_copy(update={deadline: expected})
    lease = await harness.issuer.issue(harness.scope)
    assert lease.claims.expires_at == expected


@pytest.mark.parametrize("deadline", DEADLINE_FIELDS)
@pytest.mark.parametrize("offset", [timedelta(0), timedelta(microseconds=-1)])
async def test_each_authorization_deadline_denies_at_or_before_now(
    harness: _Harness, deadline: str, offset: timedelta
) -> None:
    assert harness.provider.state is not None
    harness.provider.state = harness.provider.state.model_copy(update={deadline: NOW + offset})
    await _assert_denied(harness)
    assert not harness.provider.committed
    assert "record" not in harness.provider.events


@pytest.mark.parametrize("status", ["missing", "revoked", "blocked"])
async def test_missing_or_inactive_authorization_denies(harness: _Harness, status: str) -> None:
    assert harness.provider.state is not None
    harness.provider.state = (
        None
        if status == "missing"
        else harness.provider.state.model_copy(update={"status": status})
    )
    await _assert_denied(harness)
    assert not harness.provider.committed
    assert "time" not in harness.provider.events


@pytest.mark.parametrize("field_name", ["tenant_id", "user_id", "branch_id", "writer_epoch"])
async def test_authorization_must_match_exact_selected_scope(
    harness: _Harness, field_name: str
) -> None:
    selector = harness.scope.model_copy(
        update={field_name: 99 if field_name == "writer_epoch" else _uuid(99)}
    )
    await _assert_denied(harness, selector)
    assert not harness.provider.committed
    assert "time" not in harness.provider.events


@pytest.mark.parametrize("corruption", ["selector", "nested-reference", "commands", "deadline"])
async def test_model_copy_corruption_is_revalidated_without_warnings(
    harness: _Harness, corruption: str
) -> None:
    assert harness.provider.state is not None
    state = harness.provider.state
    selector = harness.scope
    if corruption == "selector":
        selector = selector.model_copy(update={"writer_epoch": "6"})
    elif corruption == "nested-reference":
        reference = state.admission.model_copy(update={"revision": "3"})
        harness.provider.state = state.model_copy(update={"admission": reference})
    elif corruption == "commands":
        harness.provider.state = state.model_copy(update={"allowed_commands": ["receipt.print"]})
    else:
        harness.provider.state = state.model_copy(update={"device_expires_at": NOW.isoformat()})
    await _assert_denied(harness, selector)
    assert not harness.provider.committed


@pytest.mark.parametrize("zone", [None, timezone(timedelta(hours=5))])
async def test_cloud_time_must_be_explicit_utc(harness: _Harness, zone: timezone | None) -> None:
    harness.provider.now = NOW.replace(tzinfo=None) if zone is None else NOW.astimezone(zone)
    await _assert_denied(harness)
    assert "record" not in harness.provider.events


@pytest.mark.parametrize(
    "offset", [timedelta(microseconds=-1), timedelta(days=90), timedelta(days=91)]
)
async def test_signing_key_window_is_start_inclusive_end_exclusive(
    harness: _Harness, offset: timedelta
) -> None:
    harness.provider.now = NOW + offset
    await _assert_denied(harness)
    assert "record" not in harness.provider.events


@pytest.mark.parametrize(
    "invalid", ["short-seed", "mutable-seed", "key-id", "naive-time", "window"]
)
def test_malformed_signing_configuration_has_generic_error(invalid: str) -> None:
    private_seed: object = secrets.token_bytes(31 if invalid == "short-seed" else 32)
    if invalid == "mutable-seed":
        private_seed = bytearray(cast(bytes, private_seed))
    key_id = UUID(int=1) if invalid == "key-id" else _uuid(12)
    valid_from = NOW.replace(tzinfo=None) if invalid == "naive-time" else NOW
    valid_until = NOW if invalid == "window" else NOW + timedelta(days=90)
    with pytest.raises(ValueError) as error:
        CloudOfflineLeaseIssuerV1(
            _Provider(_state()),
            signing_key_id=key_id,
            private_seed=cast(bytes, private_seed),
            key_valid_from=valid_from,
            key_valid_until=valid_until,
        )
    assert str(error.value) == CONFIGURATION_ERROR
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__ is True


@pytest.mark.parametrize("stage", ["enter", "read", "time", "record", "commit", "exit"])
async def test_transaction_failure_never_returns_lease(harness: _Harness, stage: str) -> None:
    harness.provider.error_at = stage
    await _assert_denied(harness)
    if stage != "exit":
        assert not harness.provider.committed


@pytest.mark.parametrize("stage", ["record", "commit"])
async def test_suppressed_transaction_error_cannot_publish_lease(
    harness: _Harness, stage: str
) -> None:
    harness.provider.error_at = stage
    harness.provider.suppress_errors = True
    await _assert_denied(harness)
    assert not harness.provider.committed
    assert "rollback" in harness.provider.events


class _FailingSigner:
    def sign(self, message: bytes) -> bytes:
        del message
        raise RuntimeError("Private synthetic signing diagnostic")


async def test_signing_failure_is_generic_and_never_recorded(
    harness: _Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(harness.issuer, "_signer", _FailingSigner())
    await _assert_denied(harness)
    assert "record" not in harness.provider.events
    assert not harness.provider.committed


async def test_result_is_unavailable_until_explicit_commit_finishes(harness: _Harness) -> None:
    harness.provider.commit_started = asyncio.Event()
    harness.provider.release_commit = asyncio.Event()
    task = asyncio.create_task(harness.issuer.issue(harness.scope))
    try:
        await asyncio.wait_for(harness.provider.commit_started.wait(), timeout=5)
        assert harness.provider.staged
        assert not harness.provider.committed
        assert not task.done()
        harness.provider.release_commit.set()
        lease = await asyncio.wait_for(task, timeout=5)
        assert harness.provider.committed == [lease]
        assert harness.provider.events[-1] == "exit"
    finally:
        harness.provider.release_commit.set()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


async def test_successfully_issued_lease_does_not_enable_runtime(harness: _Harness) -> None:
    lease = await harness.issuer.issue(harness.scope)
    runtime = runtime_offline_auth_lease_verifier()
    assert isinstance(runtime, DenyAllOfflineLeaseVerifierV1)
    with pytest.raises(OfflineAuthUnavailableError, match=f"^{OFFLINE_AUTH_UNAVAILABLE_MESSAGE}$"):
        await runtime.authorize(lease, "sale.cash.complete")
