"""Pure lease decisions against explicit synthetic trusted adapters."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import TypeVar, cast
from uuid import UUID

import pytest
from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa
from pydantic import BaseModel

from app.domains.sync.offline_auth import (
    OFFLINE_AUTH_UNAVAILABLE_MESSAGE,
    OfflineAuthUnavailableError,
    TrustedClockReading,
)
from app.domains.sync.offline_auth_lease import (
    OfflineAuthLeaseDecisionPipelineV1,
    offline_auth_lease_claims_bytes,
    offline_auth_lease_claims_hash,
)
from app.domains.sync.offline_auth_lease_state import (
    LocalOfflineCommandConfirmationV1,
    LocalOfflineLeaseSessionV1,
    OfflineLeaseAuthorizationStateV1,
    OfflineLeaseReauthenticationPolicyV1,
    OfflineLeaseRevisionObservationV1,
)
from app.domains.sync.offline_lease_signature import Ed25519OfflineLeaseSignatureVerifier
from app.domains.sync.schemas import (
    OfflineAuthDeviceBindingV0,
    OfflineAuthLeaseClaimsV1,
    OfflineAuthLeaseReferenceV1,
    OfflineAuthScopeV0,
    OfflinePosCommand,
    SignedOfflineAuthLeaseV1,
)

NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)
COMMANDS: tuple[OfflinePosCommand, ...] = (
    "operation.result.read",
    "receipt.print",
    "receipt.reprint",
    "sale.cash.complete",
    "shift.close",
    "shift.open",
)
Model = TypeVar("Model", bound=BaseModel)


@pytest.mark.parametrize("mutation", ["none", "tenant", "key-id", "signature"])
async def test_real_cloud_signature_precedes_trusted_state_access(
    harness: _Harness, mutation: str
) -> None:
    key = ECC.generate(curve="Ed25519")
    public_key = key.public_key().export_key(format="raw")
    assert isinstance(public_key, bytes)
    lease = harness.lease.model_copy(
        update={
            "signature": eddsa.new(key, "rfc8032")
            .sign(offline_auth_lease_claims_bytes(harness.lease.claims))
            .hex()
        }
    )
    verifier = Ed25519OfflineLeaseSignatureVerifier({lease.signing_key_id: public_key})
    pipeline = OfflineAuthLeaseDecisionPipelineV1(verifier, harness.tpm, harness.state)
    if mutation == "tenant":
        lease = _changed(lease, "claims.scope.tenant_id", _uuid(99))
        lease = lease.model_copy(
            update={"claims_hash": offline_auth_lease_claims_hash(lease.claims)}
        )
    elif mutation == "key-id":
        lease = lease.model_copy(update={"signing_key_id": _uuid(99)})
    elif mutation == "signature":
        lease = lease.model_copy(update={"signature": "00" * 64})

    if mutation != "none":
        with pytest.raises(OfflineAuthUnavailableError, match=OFFLINE_AUTH_UNAVAILABLE_MESSAGE):
            await pipeline.authorize(lease, "sale.cash.complete")
        assert harness.state.events == []
        assert harness.state.observations == []
    else:
        principal = await pipeline.authorize(lease, "sale.cash.complete")
        assert principal.lease_id == lease.claims.lease_id
        assert principal.scope == lease.claims.scope
        assert "commit" in harness.state.events
        assert len(harness.state.observations) == 1


def _uuid(value: int) -> UUID:
    return UUID(f"{value:08x}-0000-4000-8000-000000000001")


def _changed(model: Model, path: str, value: object) -> Model:
    """Emulate corrupted trusted-adapter instances, without prior validation."""
    key, separator, remaining = path.partition(".")
    if separator:
        nested = getattr(model, key)
        assert isinstance(nested, BaseModel)
        value = _changed(nested, remaining, value)
    return model.model_copy(update={key: value})


def _claims() -> OfflineAuthLeaseClaimsV1:
    return OfflineAuthLeaseClaimsV1(
        schema_version=1,
        lease_version=1,
        lease_id=_uuid(1),
        issuer="aurum-cloud",
        audience="aurum-edge-offline-auth-v1",
        auth_context="owner-approved-local-credential",
        scope=OfflineAuthScopeV0(
            activation_id=_uuid(2),
            tenant_id=_uuid(3),
            branch_id=_uuid(4),
            edge_node_id=_uuid(5),
            register_id=_uuid(6),
            user_id=_uuid(7),
            writer_epoch=8,
            capability="cash_sale_v1",
        ),
        device_binding=OfflineAuthDeviceBindingV0(
            method="tpm2", key_id=_uuid(9), spki_sha256="ab" * 32
        ),
        allowed_commands=COMMANDS,
        policy_revision=11,
        tenant_access_revision=7,
        subject_revision=12,
        admission=OfflineAuthLeaseReferenceV1(reference_id=_uuid(13), revision=2),
        local_credential=OfflineAuthLeaseReferenceV1(reference_id=_uuid(14), revision=3),
        reauthentication_policy=OfflineAuthLeaseReferenceV1(reference_id=_uuid(15), revision=4),
        credential_audience="aurum-edge-local-auth-v1",
        issued_at=NOW - timedelta(hours=2),
        expires_at=NOW + timedelta(days=29),
    )


def _signed(claims: OfflineAuthLeaseClaimsV1) -> SignedOfflineAuthLeaseV1:
    return SignedOfflineAuthLeaseV1(
        claims=claims,
        claims_hash=offline_auth_lease_claims_hash(claims),
        signing_key_id=_uuid(16),
        signature_algorithm="ed25519-v1",
        signature="cd" * 64,
    )


@dataclass
class _State:
    authorization: OfflineLeaseAuthorizationStateV1 | None
    session: LocalOfflineLeaseSessionV1 | None
    clock: TrustedClockReading
    error_at: str | None = None
    suppress_errors: bool = False
    events: list[str] = field(default_factory=list)
    observations: list[OfflineLeaseRevisionObservationV1] = field(default_factory=list)
    high_water: OfflineLeaseRevisionObservationV1 | None = None
    sealed_tenant_access_revision: int | None = 7
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    commit_started: asyncio.Event | None = None
    release_commit: asyncio.Event | None = None

    def record(self, stage: str) -> None:
        self.events.append(stage)
        if stage == self.error_at:
            raise RuntimeError(f"private adapter diagnostic: {stage}")

    async def read_authorization(self) -> OfflineLeaseAuthorizationStateV1 | None:
        self.record("authorization")
        return self.authorization

    async def assert_current_authorization(
        self, authorization: OfflineLeaseAuthorizationStateV1
    ) -> None:
        self.record("authorization.assert")
        if authorization.tenant_access_revision != self.sealed_tenant_access_revision:
            raise RuntimeError("protected tenant policy does not match")

    async def read_local_session(self) -> LocalOfflineLeaseSessionV1 | None:
        self.record("session")
        return self.session

    async def read_trusted_time(self) -> TrustedClockReading:
        self.record("clock")
        return self.clock

    async def check_and_advance(self, observation: OfflineLeaseRevisionObservationV1) -> None:
        self.record("advance")
        previous = self.high_water
        if previous is not None and (
            observation.trusted_time.monotonic_counter < previous.trusted_time.monotonic_counter
            or observation.trusted_time.utc_now < previous.trusted_time.utc_now
            or observation.authorization.policy_revision < previous.authorization.policy_revision
            or observation.authorization.tenant_access_revision
            < previous.authorization.tenant_access_revision
            or observation.authorization.subject_revision < previous.authorization.subject_revision
        ):
            raise RuntimeError("sealed high-water rollback")
        self.observations.append(observation)

    @asynccontextmanager
    async def serialized(self) -> AsyncIterator[_State]:
        async with self.lock:
            self.record("enter")
            try:
                yield self
                self.record("commit")
                if self.commit_started is not None:
                    self.commit_started.set()
                if self.release_commit is not None:
                    await self.release_commit.wait()
                self.high_water = self.observations[-1]
            except BaseException:
                self.events.append("rollback")
                if not self.suppress_errors:
                    raise
            finally:
                self.events.append("exit")


@dataclass
class _Signature:
    state: _State
    signing_bytes: bytes | None = None

    async def verify(self, lease: SignedOfflineAuthLeaseV1, signing_bytes: bytes) -> None:
        self.state.record("signature")
        self.signing_bytes = signing_bytes
        assert lease.claims_hash == offline_auth_lease_claims_hash(lease.claims)


@dataclass
class _Tpm:
    state: _State
    binding: OfflineAuthDeviceBindingV0 | None = None

    async def assert_possession(self, binding: OfflineAuthDeviceBindingV0) -> None:
        assert self.state.lock.locked()
        self.state.record("tpm")
        self.binding = binding


@dataclass
class _Harness:
    lease: SignedOfflineAuthLeaseV1
    state: _State
    signature: _Signature
    tpm: _Tpm

    @property
    def pipeline(self) -> OfflineAuthLeaseDecisionPipelineV1:
        return OfflineAuthLeaseDecisionPipelineV1(self.signature, self.tpm, self.state)


@pytest.fixture
def harness() -> _Harness:
    claims = _claims()
    policy = OfflineLeaseReauthenticationPolicyV1(
        reference=claims.reauthentication_policy,
        max_session_seconds=7200,
        confirmation_commands=(),
        max_confirmation_seconds=300,
    )
    state = _State(
        authorization=OfflineLeaseAuthorizationStateV1(
            lease_id=claims.lease_id,
            scope=claims.scope,
            device_binding=claims.device_binding,
            policy_revision=claims.policy_revision,
            tenant_access_revision=claims.tenant_access_revision,
            subject_revision=claims.subject_revision,
            allowed_commands=COMMANDS,
            admission=claims.admission,
            local_credential=claims.local_credential,
            credential_audience=claims.credential_audience,
            reauthentication_policy=policy,
            shift_context_id=_uuid(17),
            status="active",
        ),
        session=LocalOfflineLeaseSessionV1(
            session_id=_uuid(18),
            lease_id=claims.lease_id,
            scope=claims.scope,
            local_credential=claims.local_credential,
            credential_audience=claims.credential_audience,
            reauthentication_policy=claims.reauthentication_policy,
            shift_context_id=_uuid(17),
            authenticated_at=NOW - timedelta(hours=1),
            expires_at=NOW + timedelta(hours=1),
            continuity_id="sealed-continuity",
            monotonic_counter=90,
        ),
        clock=TrustedClockReading(NOW, 100, "sealed-continuity"),
    )
    return _Harness(_signed(claims), state, _Signature(state), _Tpm(state))


async def _assert_denied(
    harness: _Harness, command: OfflinePosCommand = "sale.cash.complete"
) -> None:
    with pytest.raises(OfflineAuthUnavailableError) as error:
        await harness.pipeline.authorize(harness.lease, command)
    assert str(error.value) == OFFLINE_AUTH_UNAVAILABLE_MESSAGE
    assert error.value.__suppress_context__ is True
    assert not harness.state.lock.locked()


def _require_confirmation(harness: _Harness) -> None:
    assert harness.state.authorization is not None
    assert harness.state.session is not None
    harness.state.authorization = _changed(
        harness.state.authorization,
        "reauthentication_policy.confirmation_commands",
        ("sale.cash.complete",),
    )
    harness.state.session = _changed(
        harness.state.session,
        "confirmation",
        LocalOfflineCommandConfirmationV1(
            command="sale.cash.complete",
            confirmed_at=NOW - timedelta(seconds=60),
            monotonic_counter=95,
        ),
    )


@pytest.mark.parametrize("command", COMMANDS)
async def test_all_commands_require_complete_transaction_and_session_bound(
    harness: _Harness, command: OfflinePosCommand
) -> None:
    principal = await harness.pipeline.authorize(harness.lease, command)
    assert harness.state.session is not None
    assert principal.lease_id == harness.lease.claims.lease_id
    assert principal.session_id == harness.state.session.session_id
    assert principal.scope == harness.lease.claims.scope
    assert principal.command == command
    assert principal.expires_at == harness.state.session.expires_at
    assert harness.signature.signing_bytes == offline_auth_lease_claims_bytes(harness.lease.claims)
    assert harness.tpm.binding == harness.lease.claims.device_binding
    assert harness.state.events == [
        "signature",
        "enter",
        "authorization",
        "authorization.assert",
        "tpm",
        "session",
        "clock",
        "advance",
        "commit",
        "exit",
    ]
    assert harness.state.high_water is harness.state.observations[0]
    assert harness.state.high_water.authorization == harness.state.authorization
    assert harness.state.high_water.session == harness.state.session
    assert harness.state.high_water.trusted_time == harness.state.clock


SCOPE_CHANGES: list[tuple[str, object]] = [
    (f"scope.{field_name}", _uuid(99))
    for field_name in (
        "activation_id",
        "tenant_id",
        "branch_id",
        "edge_node_id",
        "register_id",
        "user_id",
    )
] + [("scope.writer_epoch", 99), ("scope.capability", "not-cash")]


@pytest.mark.parametrize(
    ("path", "value"),
    [
        *SCOPE_CHANGES,
        ("lease_id", _uuid(99)),
        ("device_binding.key_id", _uuid(99)),
        ("device_binding.spki_sha256", "ef" * 32),
        ("device_binding.method", "software"),
        ("policy_revision", 99),
        ("tenant_access_revision", 99),
        ("subject_revision", 99),
        ("admission.reference_id", _uuid(99)),
        ("admission.revision", 99),
        ("local_credential.reference_id", _uuid(99)),
        ("local_credential.revision", 99),
        ("reauthentication_policy.reference.reference_id", _uuid(99)),
        ("reauthentication_policy.reference.revision", 99),
        ("credential_audience", "aurum-cloud"),
        ("allowed_commands", ("receipt.print",)),
        ("status", "revoked"),
        ("status", "locally-blocked"),
    ],
)
async def test_authorization_must_match_every_signed_authority_field(
    harness: _Harness, path: str, value: object
) -> None:
    assert harness.state.authorization is not None
    harness.state.authorization = _changed(harness.state.authorization, path, value)
    await _assert_denied(harness)
    assert "tpm" not in harness.state.events
    assert not harness.state.observations


@pytest.mark.parametrize(
    ("path", "value"),
    [
        *SCOPE_CHANGES,
        ("lease_id", _uuid(99)),
        ("local_credential.reference_id", _uuid(99)),
        ("local_credential.revision", 99),
        ("reauthentication_policy.reference_id", _uuid(99)),
        ("reauthentication_policy.revision", 99),
        ("credential_audience", "aurum-cloud"),
        ("shift_context_id", _uuid(99)),
        ("continuity_id", "other-boot"),
        ("monotonic_counter", 101),
        ("monotonic_counter", True),
        ("authenticated_at", NOW + timedelta(microseconds=1)),
        ("authenticated_at", NOW - timedelta(hours=2, microseconds=1)),
        ("authenticated_at", NOW.replace(tzinfo=None)),
        ("expires_at", NOW),
        ("expires_at", NOW - timedelta(microseconds=1)),
        ("expires_at", NOW + timedelta(hours=1, microseconds=1)),
    ],
)
async def test_local_session_scope_shift_and_time_are_enforced(
    harness: _Harness, path: str, value: object
) -> None:
    assert harness.state.session is not None
    harness.state.session = _changed(harness.state.session, path, value)
    await _assert_denied(harness)
    assert not harness.state.observations


@pytest.mark.parametrize("missing", ["authorization", "session"])
async def test_missing_trusted_state_denies(harness: _Harness, missing: str) -> None:
    setattr(harness.state, missing, None)
    await _assert_denied(harness)
    assert not harness.state.observations


async def test_session_may_not_outlive_lease(harness: _Harness) -> None:
    claims = _changed(harness.lease.claims, "expires_at", NOW + timedelta(minutes=30))
    harness.lease = _signed(claims)
    await _assert_denied(harness)
    assert not harness.state.observations


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("utc_now", NOW - timedelta(hours=2, microseconds=1)),
        ("utc_now", NOW + timedelta(days=29)),
        ("utc_now", NOW.replace(tzinfo=None)),
        ("utc_now", NOW.astimezone(timezone(timedelta(hours=5)))),
        ("monotonic_counter", -1),
        ("monotonic_counter", 89),
        ("monotonic_counter", True),
        ("monotonic_counter", 2**63),
        ("continuity_id", "other-boot"),
        ("continuity_id", ""),
        ("continuity_id", 42),
    ],
)
async def test_invalid_trusted_clock_readings_deny(
    harness: _Harness, path: str, value: object
) -> None:
    fields: dict[str, object] = {
        "utc_now": NOW,
        "monotonic_counter": 100,
        "continuity_id": "sealed-continuity",
    }
    fields[path] = value
    # An adapter may return a malformed object despite its declared protocol.
    harness.state.clock = cast(TrustedClockReading, SimpleNamespace(**fields))
    await _assert_denied(harness)
    assert not harness.state.observations


async def test_confirmation_limits_principal_and_exact_session_deadline(harness: _Harness) -> None:
    _require_confirmation(harness)
    principal = await harness.pipeline.authorize(harness.lease, "sale.cash.complete")
    assert principal.expires_at == NOW + timedelta(seconds=240)
    assert harness.state.session is not None
    harness.state.session = _changed(
        harness.state.session, "expires_at", NOW + timedelta(seconds=30)
    )
    principal = await harness.pipeline.authorize(harness.lease, "sale.cash.complete")
    assert principal.expires_at == NOW + timedelta(seconds=30)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("confirmation", None),
        ("confirmation.command", "receipt.print"),
        ("confirmation.confirmed_at", NOW - timedelta(seconds=300)),
        ("confirmation.confirmed_at", NOW - timedelta(seconds=301)),
        ("confirmation.confirmed_at", NOW + timedelta(microseconds=1)),
        ("confirmation.confirmed_at", NOW - timedelta(hours=1, microseconds=1)),
        ("confirmation.monotonic_counter", 89),
        ("confirmation.monotonic_counter", 101),
        ("confirmation.monotonic_counter", True),
    ],
)
async def test_fresh_confirmation_is_bound_to_command_session_and_clock(
    harness: _Harness, path: str, value: object
) -> None:
    _require_confirmation(harness)
    assert harness.state.session is not None
    harness.state.session = _changed(harness.state.session, path, value)
    await _assert_denied(harness)
    assert not harness.state.observations


@pytest.mark.parametrize(
    "stage",
    [
        "signature",
        "enter",
        "authorization",
        "authorization.assert",
        "tpm",
        "session",
        "clock",
        "advance",
        "commit",
    ],
)
async def test_all_adapter_failures_are_uniform_denials(harness: _Harness, stage: str) -> None:
    harness.state.error_at = stage
    await _assert_denied(harness)
    assert harness.state.high_water is None
    assert stage in harness.state.events


@pytest.mark.parametrize("stage", ["authorization", "advance"])
async def test_context_manager_cannot_turn_suppressed_errors_into_allow(
    harness: _Harness, stage: str
) -> None:
    harness.state.error_at = stage
    harness.state.suppress_errors = True
    await _assert_denied(harness)
    assert "rollback" in harness.state.events
    assert harness.state.high_water is None


@pytest.mark.parametrize("construction", ["copy", "construct"])
@pytest.mark.parametrize("target", ["lease", "authorization", "session"])
async def test_revalidates_copied_and_constructed_models(
    harness: _Harness, construction: str, target: str
) -> None:
    if target == "lease":
        original: BaseModel = harness.lease
        patch: dict[str, object] = {"signature_algorithm": "hmac-sha256-edge-v1"}
    elif target == "authorization":
        assert harness.state.authorization is not None
        original = harness.state.authorization
        patch = {"subject_revision": True}
    else:
        assert harness.state.session is not None
        original = harness.state.session
        patch = {"credential_audience": "aurum-cloud"}
    invalid = (
        original.model_copy(update=patch)
        if construction == "copy"
        else type(original).model_construct(**{**dict(original), **patch})
    )
    if target == "lease":
        harness.lease = cast(SignedOfflineAuthLeaseV1, invalid)
    else:
        setattr(harness.state, target, invalid)
    await _assert_denied(harness)
    assert not harness.state.observations


async def test_tampered_hash_denies_before_signature_or_state(harness: _Harness) -> None:
    harness.lease = _changed(harness.lease, "claims_hash", "00" * 32)
    await _assert_denied(harness)
    assert harness.state.events == []


async def test_command_must_be_signed_even_if_trusted_state_allows_it(harness: _Harness) -> None:
    harness.lease = _signed(_changed(harness.lease.claims, "allowed_commands", ("receipt.print",)))
    await _assert_denied(harness)
    assert harness.state.events == ["signature"]


async def test_unknown_command_is_denied_before_trusted_adapters(harness: _Harness) -> None:
    await _assert_denied(harness, cast(OfflinePosCommand, "pos.sell"))
    assert harness.state.events == []


@pytest.mark.parametrize("sealed_revision", [None, 6, 8])
async def test_restored_matching_lease_and_sql_policy_cannot_replace_sealed_revision(
    harness: _Harness, sealed_revision: int | None
) -> None:
    assert harness.state.authorization is not None
    assert harness.lease.claims.tenant_access_revision == 7
    assert harness.state.authorization.tenant_access_revision == 7
    harness.state.sealed_tenant_access_revision = sealed_revision
    await _assert_denied(harness)
    assert "authorization.assert" in harness.state.events
    assert "tpm" not in harness.state.events
    assert "advance" not in harness.state.events
    assert harness.state.observations == []
    assert harness.state.sealed_tenant_access_revision == sealed_revision


async def test_no_principal_is_exposed_until_durable_commit(harness: _Harness) -> None:
    harness.state.commit_started = asyncio.Event()
    harness.state.release_commit = asyncio.Event()
    task = asyncio.create_task(harness.pipeline.authorize(harness.lease, "sale.cash.complete"))
    try:
        await asyncio.wait_for(harness.state.commit_started.wait(), timeout=1)
        assert not task.done()
        assert harness.state.lock.locked()
        assert harness.state.high_water is None
        harness.state.release_commit.set()
        principal = await asyncio.wait_for(task, timeout=1)
        assert principal.command == "sale.cash.complete"
        assert harness.state.high_water is not None
    finally:
        harness.state.release_commit.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("rollback", ["counter", "utc", "policy", "subject"])
async def test_trusted_adapter_rejects_sealed_high_water_rollback(
    harness: _Harness, rollback: str
) -> None:
    await harness.pipeline.authorize(harness.lease, "sale.cash.complete")
    high_water = harness.state.high_water
    if rollback == "counter":
        harness.state.clock = replace(harness.state.clock, monotonic_counter=99)
    elif rollback == "utc":
        harness.state.clock = replace(harness.state.clock, utc_now=NOW - timedelta(seconds=1))
    else:
        path = f"{rollback}_revision"
        claims = _changed(harness.lease.claims, path, 10)
        harness.lease = _signed(claims)
        assert harness.state.authorization is not None
        harness.state.authorization = _changed(harness.state.authorization, path, 10)
    harness.state.events.clear()
    await _assert_denied(harness)
    assert "advance" in harness.state.events
    assert "commit" not in harness.state.events
    assert harness.state.high_water is high_water


async def test_revocation_serializes_a_waiting_authorization(harness: _Harness) -> None:
    revocation_started = asyncio.Event()
    release_revocation = asyncio.Event()

    async def revoke() -> None:
        async with harness.state.lock:
            revocation_started.set()
            await release_revocation.wait()
            assert harness.state.authorization is not None
            harness.state.authorization = _changed(harness.state.authorization, "status", "revoked")

    revoker = asyncio.create_task(revoke())
    authorize: asyncio.Task[None] | None = None
    try:
        await asyncio.wait_for(revocation_started.wait(), timeout=1)
        authorize = asyncio.create_task(_assert_denied(harness))
        await asyncio.sleep(0)
        assert not authorize.done()
        assert "enter" not in harness.state.events
        release_revocation.set()
        await asyncio.wait_for(asyncio.gather(revoker, authorize), timeout=1)
        assert "tpm" not in harness.state.events
        assert not harness.state.observations
    finally:
        release_revocation.set()
        tasks = [revoker] if authorize is None else [revoker, authorize]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
