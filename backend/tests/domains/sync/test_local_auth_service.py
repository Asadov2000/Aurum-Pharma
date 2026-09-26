"""Local login decisions with transactional doubles and independent protected state."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TypeVar, cast
from unittest.mock import Mock
from uuid import UUID

import pytest
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.sync import local_auth_service as service
from app.domains.sync.local_auth_state import (
    LocalAuthGuardSnapshotV1,
    TrustedLocalCredentialMaterialV1,
    TrustedLocalLoginStateTransactionV1,
)
from app.domains.sync.local_credentials import (
    IssuedLocalCredentialV1,
    LocalCredentialContextV1,
    issue_local_credential,
    local_credential_context_digest,
    verify_local_credential,
)
from app.domains.sync.models import LocalAuthGuard
from app.domains.sync.offline_auth import (
    OFFLINE_AUTH_UNAVAILABLE_MESSAGE,
    OfflineAuthUnavailableError,
    TrustedClockReading,
)
from app.domains.sync.offline_auth_lease import (
    offline_auth_lease_claims_bytes,
    offline_auth_lease_claims_hash,
)
from app.domains.sync.offline_auth_lease_state import (
    LocalOfflineLeaseSessionV1,
    OfflineLeaseAuthorizationStateV1,
    OfflineLeaseReauthenticationPolicyV1,
)
from app.domains.sync.schemas import (
    OfflineAuthDeviceBindingV0,
    OfflineAuthLeaseClaimsV1,
    OfflineAuthLeaseReferenceV1,
    OfflineAuthScopeV0,
    SignedOfflineAuthLeaseV1,
)

NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)
PEPPER = bytes(range(32))
MAX_SEQUENCE = 9_223_372_036_854_775_807
Model = TypeVar("Model", bound=BaseModel)
pytestmark = pytest.mark.asyncio


def _uuid(value: int) -> UUID:
    return UUID(f"{value:08x}-0000-4000-8000-000000000001")


def _changed(model: Model, path: str, value: object) -> Model:
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
            method="tpm2",
            key_id=_uuid(9),
            spki_sha256="ab" * 32,
        ),
        allowed_commands=("sale.cash.complete", "shift.open"),
        policy_revision=11,
        tenant_access_revision=7,
        subject_revision=12,
        admission=OfflineAuthLeaseReferenceV1(reference_id=_uuid(13), revision=2),
        local_credential=OfflineAuthLeaseReferenceV1(reference_id=_uuid(14), revision=3),
        reauthentication_policy=OfflineAuthLeaseReferenceV1(reference_id=_uuid(15), revision=4),
        credential_audience="aurum-edge-local-auth-v1",
        issued_at=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(days=1),
    )


def _signed(claims: OfflineAuthLeaseClaimsV1) -> SignedOfflineAuthLeaseV1:
    return SignedOfflineAuthLeaseV1(
        claims=claims,
        claims_hash=offline_auth_lease_claims_hash(claims),
        signing_key_id=_uuid(16),
        signature_algorithm="ed25519-v1",
        signature="cd" * 64,
    )


@pytest.fixture(scope="module")
def issued() -> IssuedLocalCredentialV1:
    claims = _claims()
    context = LocalCredentialContextV1(
        schema_version=1,
        audience=claims.credential_audience,
        scope=claims.scope,
        local_credential=claims.local_credential,
        device_binding=claims.device_binding,
        protection_key=OfflineAuthDeviceBindingV0(
            method="tpm2",
            key_id=_uuid(17),
            spki_sha256="ef" * 32,
        ),
    )
    return issue_local_credential(context, pepper=PEPPER)


@dataclass
class _Harness:
    material: TrustedLocalCredentialMaterialV1
    guard: LocalAuthGuardSnapshotV1 | None
    authorization: OfflineLeaseAuthorizationStateV1 | None
    lease: SignedOfflineAuthLeaseV1
    high_water: LocalAuthGuardSnapshotV1
    sealed_tenant_access_revision: int | None = 7
    events: list[str] = field(default_factory=list)
    sessions: list[LocalOfflineLeaseSessionV1] = field(default_factory=list)
    clocks: list[TrustedClockReading] = field(
        default_factory=lambda: [
            TrustedClockReading(NOW, 100, "boot-one"),
            TrustedClockReading(NOW + timedelta(seconds=1), 101, "boot-one"),
        ]
    )
    error_at: str | None = None
    suppress_at: str | None = None
    change_authorization_after_secret: bool = False
    authorization_reads: int = 0
    commit_started: asyncio.Event | None = None
    release_commit: asyncio.Event | None = None
    guard_keys: list[dict[str, UUID]] = field(default_factory=list)
    tenant_parameters: list[dict[str, str]] = field(default_factory=list)
    replacements: list[LocalOfflineLeaseSessionV1 | None] = field(default_factory=list)
    protected_session: LocalOfflineLeaseSessionV1 | None = None

    def event(self, name: str) -> None:
        self.events.append(name)
        if self.error_at == name:
            raise RuntimeError("synthetic private diagnostic: nobody@example.invalid")

    def session_factory(self) -> AsyncSession:
        # Only the small AsyncSession surface exercised by this service is doubled.
        return cast(AsyncSession, _Database(self))

    @asynccontextmanager
    async def serialized(self) -> AsyncIterator[TrustedLocalLoginStateTransactionV1]:
        self.event("state.enter")
        try:
            yield self
        except Exception:
            if self.suppress_at != "state":
                raise
        self.event("state.exit")

    async def read_authorization(self) -> OfflineLeaseAuthorizationStateV1 | None:
        self.event("authorization")
        self.authorization_reads += 1
        if self.change_authorization_after_secret and self.authorization_reads > 1:
            return None
        return self.authorization

    async def read_credential(self) -> TrustedLocalCredentialMaterialV1:
        self.event("credential")
        return self.material

    async def assert_current_authorization(
        self, authorization: OfflineLeaseAuthorizationStateV1
    ) -> None:
        self.event("authorization.assert")
        if authorization.tenant_access_revision != self.sealed_tenant_access_revision:
            raise RuntimeError("protected tenant policy does not match")

    async def read_trusted_time(self) -> TrustedClockReading:
        self.event("clock")
        return self.clocks.pop(0) if len(self.clocks) > 1 else self.clocks[0]

    async def assert_current_guard(self, snapshot: LocalAuthGuardSnapshotV1) -> None:
        self.event("assert")
        if snapshot != self.high_water:
            raise RuntimeError("protected high-water mismatch")

    async def seal_next_guard(
        self,
        snapshot: LocalAuthGuardSnapshotV1,
        *,
        replacement: LocalOfflineLeaseSessionV1 | None,
    ) -> None:
        self.event("seal")
        self.high_water = snapshot
        self.replacements.append(replacement)
        if replacement is not None:
            self.protected_session = replacement

    async def verify(self, lease: SignedOfflineAuthLeaseV1, signing_bytes: bytes) -> None:
        self.event("signature")
        assert signing_bytes == offline_auth_lease_claims_bytes(lease.claims)

    async def assert_possession(self, binding: OfflineAuthDeviceBindingV0) -> None:
        self.event("device")
        assert binding == self.lease.claims.device_binding

    def authenticator(self) -> service.LocalOfflineAuthenticatorV1:
        return service.LocalOfflineAuthenticatorV1(
            session_factory=self.session_factory,
            signature_verifier=self,
            device_binding_verifier=self,
            state_provider=self,
        )


class _Database:
    def __init__(self, harness: _Harness) -> None:
        self.harness = harness
        self.guard: LocalAuthGuard | None = None
        self.sessions: list[LocalOfflineLeaseSessionV1] = []

    async def __aenter__(self) -> _Database:
        self.harness.event("session.enter")
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        self.harness.event("session.exit")
        return self.harness.suppress_at == "session"

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        state = self.harness
        state.event("begin")
        self.guard = LocalAuthGuard(**asdict(state.guard)) if state.guard else None
        self.sessions = deepcopy(state.sessions)
        try:
            yield
        except Exception:
            state.events.append("rollback")
            if state.suppress_at != "transaction":
                raise
        else:
            state.event("commit")
            if state.commit_started is not None:
                state.commit_started.set()
            if state.release_commit is not None:
                await state.release_commit.wait()
            if self.guard is not None:
                state.guard = LocalAuthGuardSnapshotV1(
                    **{
                        name: getattr(self.guard, name)
                        for name in LocalAuthGuardSnapshotV1.__dataclass_fields__
                    }
                )
            state.sessions = self.sessions
            state.events.append("committed")

    async def execute(self, _statement: object, parameters: dict[str, str]) -> None:
        self.harness.event("tenant")
        self.harness.tenant_parameters.append(parameters)

    async def flush(self) -> None:
        self.harness.event("flush")


class _Repository:
    def __init__(self, db: _Database) -> None:
        self.db = db

    async def lock_guard(self, **keys: UUID) -> LocalAuthGuard | None:
        self.db.harness.event("guard")
        self.db.harness.guard_keys.append(keys)
        guard = self.db.guard
        if guard is None or any(getattr(guard, key) != value for key, value in keys.items()):
            return None
        return guard

    async def lock_workplace(self, **keys: UUID) -> None:
        self.db.harness.event("workplace")
        scope = self.db.harness.lease.claims.scope
        assert keys == {
            key: getattr(scope, key)
            for key in (
                "tenant_id",
                "branch_id",
                "edge_node_id",
                "register_id",
            )
        }

    async def replace_session(self, record: LocalOfflineLeaseSessionV1) -> None:
        self.db.harness.event("replace")
        self.db.sessions = [record]


@pytest.fixture
def harness(issued: IssuedLocalCredentialV1, monkeypatch: pytest.MonkeyPatch) -> _Harness:
    claims = _claims()
    material = TrustedLocalCredentialMaterialV1(issued.record.context, issued.record, PEPPER)
    guard = LocalAuthGuardSnapshotV1(
        tenant_id=claims.scope.tenant_id,
        branch_id=claims.scope.branch_id,
        edge_node_id=claims.scope.edge_node_id,
        user_id=claims.scope.user_id,
        credential_id=claims.local_credential.reference_id,
        credential_revision=3,
        context_digest=local_credential_context_digest(material.context).hex(),
        failed_attempts=0,
        attempt_sequence=10,
        blocked_until=None,
        last_utc=NOW - timedelta(seconds=1),
        last_counter=99,
        continuity_id="boot-one",
    )
    authorization = OfflineLeaseAuthorizationStateV1(
        lease_id=claims.lease_id,
        scope=claims.scope,
        device_binding=claims.device_binding,
        policy_revision=claims.policy_revision,
        tenant_access_revision=claims.tenant_access_revision,
        subject_revision=claims.subject_revision,
        allowed_commands=claims.allowed_commands,
        admission=claims.admission,
        local_credential=claims.local_credential,
        credential_audience=claims.credential_audience,
        reauthentication_policy=OfflineLeaseReauthenticationPolicyV1(
            reference=claims.reauthentication_policy,
            max_session_seconds=600,
            confirmation_commands=("shift.open",),
            max_confirmation_seconds=30,
        ),
        shift_context_id=_uuid(18),
        status="active",
    )
    monkeypatch.setattr(service, "LocalAuthRepository", _Repository)
    return _Harness(material, guard, authorization, _signed(claims), guard)


@pytest.fixture
def secret_check(monkeypatch: pytest.MonkeyPatch, harness: _Harness) -> Mock:
    def checked(*_args: object, **_kwargs: object) -> bool:
        harness.event("bcrypt")
        return True

    spy = Mock(side_effect=checked)
    monkeypatch.setattr(service, "verify_local_credential", spy)
    return spy


async def _deny(harness: _Harness) -> None:
    with pytest.raises(OfflineAuthUnavailableError) as error:
        await harness.authenticator().authenticate(harness.lease, "synthetic-input")
    assert str(error.value) == OFFLINE_AUTH_UNAVAILABLE_MESSAGE
    assert error.value.__suppress_context__


async def test_real_secret_creates_only_non_secret_session_after_commit(
    harness: _Harness,
    issued: IssuedLocalCredentialV1,
) -> None:
    result = await harness.authenticator().authenticate(
        harness.lease,
        issued.secret.get_secret_value(),
    )
    assert harness.sessions == [result]
    assert harness.replacements == [result]
    assert harness.guard is not None
    assert harness.guard.attempt_sequence == 11
    assert result.authenticated_at == NOW + timedelta(seconds=1)
    assert result.expires_at == NOW + timedelta(seconds=601)
    assert result.confirmation is None
    assert result.credential_audience == "aurum-edge-local-auth-v1"
    assert harness.events.index("tenant") < harness.events.index("guard")
    assert harness.events.index("assert") < harness.events.index("workplace")
    assert harness.events.index("seal") < harness.events.index("committed")
    assert harness.events[-1] == "state.exit"
    assert harness.tenant_parameters == [
        {
            "tenant": str(result.scope.tenant_id),
            "actor": str(result.scope.user_id),
        }
    ]
    persisted = repr(harness.guard) + result.model_dump_json()
    assert issued.secret.get_secret_value() not in persisted
    assert issued.record.verifier.get_secret_value() not in persisted
    assert "pepper" not in persisted


async def test_no_result_before_commit_and_protected_state_exit(
    harness: _Harness,
    secret_check: Mock,
) -> None:
    harness.commit_started = asyncio.Event()
    harness.release_commit = asyncio.Event()
    task = asyncio.create_task(harness.authenticator().authenticate(harness.lease, "input"))
    try:
        await asyncio.wait_for(harness.commit_started.wait(), timeout=5)
        assert not task.done()
        assert harness.sessions == []
        assert "state.exit" not in harness.events
    finally:
        harness.release_commit.set()
    result = await task
    assert harness.sessions == [result]
    secret_check.assert_called_once()


async def test_failures_commit_and_fifth_failure_locks_without_more_bcrypt(
    harness: _Harness,
    secret_check: Mock,
) -> None:
    secret_check.side_effect = None
    secret_check.return_value = False
    for attempt in range(1, 6):
        await _deny(harness)
        assert harness.guard is not None
        assert harness.guard.failed_attempts == attempt
        assert harness.guard.attempt_sequence == 10 + attempt
        assert harness.high_water == harness.guard
    assert harness.guard.blocked_until == NOW + timedelta(minutes=5, seconds=1)
    await _deny(harness)
    assert secret_check.call_count == 5
    assert harness.events.count("committed") == 5
    assert harness.replacements == [None] * 5
    assert harness.sessions == []
    harness.clocks = [TrustedClockReading(harness.guard.blocked_until, 102, "boot-one")]
    secret_check.return_value = True
    result = await harness.authenticator().authenticate(harness.lease, "input")
    assert result.authenticated_at == NOW + timedelta(minutes=5, seconds=1)
    assert harness.guard.failed_attempts == 0
    assert harness.guard.blocked_until is None
    assert harness.guard.attempt_sequence == 16


@pytest.mark.parametrize(
    "stage",
    [
        "signature",
        "state.enter",
        "session.enter",
        "begin",
        "tenant",
        "guard",
        "authorization",
        "authorization.assert",
        "credential",
        "assert",
        "clock",
        "device",
        "workplace",
        "bcrypt",
        "replace",
        "flush",
        "seal",
        "commit",
        "session.exit",
        "state.exit",
    ],
)
async def test_adapter_errors_deny_without_disclosing_diagnostics(
    harness: _Harness,
    secret_check: Mock,
    stage: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    harness.error_at = stage
    await _deny(harness)
    if stage not in {"session.exit", "state.exit"}:
        assert harness.sessions == []
    assert "nobody@example.invalid" not in caplog.text
    assert "synthetic-input" not in caplog.text


@pytest.mark.parametrize("suppress_at", ["transaction", "session", "state"])
@pytest.mark.parametrize("stage", ["authorization", "replace", "flush", "seal"])
async def test_suppressed_body_error_cannot_return_prepared_session(
    harness: _Harness,
    secret_check: Mock,
    suppress_at: str,
    stage: str,
) -> None:
    harness.suppress_at = suppress_at
    harness.error_at = stage
    await _deny(harness)
    assert harness.sessions == []
    assert "committed" not in harness.events


async def test_failed_commit_leaves_forward_gap_and_next_attempt_denies_before_bcrypt(
    harness: _Harness,
    secret_check: Mock,
) -> None:
    harness.error_at = "commit"
    await _deny(harness)
    assert harness.guard is not None
    assert harness.guard.attempt_sequence == 10
    assert harness.high_water.attempt_sequence == 11
    harness.error_at = None
    await _deny(harness)
    secret_check.assert_called_once()


@pytest.mark.parametrize("missing", ["guard", "authorization"])
async def test_missing_enrollment_never_creates_guard_or_session(
    harness: _Harness,
    secret_check: Mock,
    missing: str,
) -> None:
    setattr(harness, missing, None)
    await _deny(harness)
    secret_check.assert_not_called()
    assert harness.sessions == []
    assert "seal" not in harness.events


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("status", "revoked"),
        ("status", "locally-blocked"),
        ("lease_id", _uuid(90)),
        ("scope.tenant_id", _uuid(90)),
        ("scope.branch_id", _uuid(90)),
        ("scope.edge_node_id", _uuid(90)),
        ("scope.register_id", _uuid(90)),
        ("scope.user_id", _uuid(90)),
        ("scope.activation_id", _uuid(90)),
        ("scope.writer_epoch", 99),
        ("device_binding.key_id", _uuid(90)),
        ("policy_revision", 99),
        ("tenant_access_revision", 99),
        ("subject_revision", 99),
        ("admission.revision", 99),
        ("local_credential.revision", 99),
        ("local_credential.reference_id", _uuid(90)),
        ("reauthentication_policy.reference.revision", 99),
        ("credential_audience", "aurum-cloud"),
        ("allowed_commands", ("shift.open",)),
    ],
)
async def test_admission_mismatch_denies_before_secret_check(
    harness: _Harness,
    secret_check: Mock,
    path: str,
    value: object,
) -> None:
    assert harness.authorization is not None
    harness.authorization = _changed(harness.authorization, path, value)
    await _deny(harness)
    secret_check.assert_not_called()


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("branch_id", _uuid(90)),
        ("credential_revision", 99),
        ("context_digest", "00" * 32),
        ("attempt_sequence", MAX_SEQUENCE),
    ],
)
async def test_guard_preconditions_deny_without_reset(
    harness: _Harness,
    secret_check: Mock,
    field_name: str,
    value: object,
) -> None:
    assert harness.guard is not None
    harness.guard = replace(harness.guard, **{field_name: value})
    before = harness.guard
    await _deny(harness)
    assert harness.guard == before
    secret_check.assert_not_called()


@pytest.mark.parametrize(
    "clock",
    [
        TrustedClockReading(NOW - timedelta(seconds=2), 100, "boot-one"),
        TrustedClockReading(NOW, 98, "boot-one"),
        TrustedClockReading(NOW, 100, "new-boot"),
        TrustedClockReading(NOW, MAX_SEQUENCE + 1, "boot-one"),
        TrustedClockReading(NOW + timedelta(days=1), 100, "boot-one"),
    ],
)
async def test_initial_clock_rejection_skips_secret_check(
    harness: _Harness,
    secret_check: Mock,
    clock: TrustedClockReading,
) -> None:
    harness.clocks = [clock]
    await _deny(harness)
    secret_check.assert_not_called()


@pytest.mark.parametrize(
    "after",
    [
        TrustedClockReading(NOW - timedelta(microseconds=1), 100, "boot-one"),
        TrustedClockReading(NOW, 99, "boot-one"),
        TrustedClockReading(NOW, 100, "new-boot"),
        TrustedClockReading(NOW + timedelta(days=1), 101, "boot-one"),
    ],
)
async def test_time_rechecked_after_expensive_secret_work(
    harness: _Harness,
    secret_check: Mock,
    after: TrustedClockReading,
) -> None:
    harness.clocks[1] = after
    await _deny(harness)
    secret_check.assert_called_once()
    assert harness.sessions == []
    assert "seal" not in harness.events


async def test_revocation_during_bcrypt_cannot_commit_session(
    harness: _Harness,
    secret_check: Mock,
) -> None:
    harness.change_authorization_after_secret = True
    await _deny(harness)
    secret_check.assert_called_once()
    assert harness.sessions == []
    assert "committed" not in harness.events


@pytest.mark.parametrize("sealed_revision", [None, 6, 8])
async def test_matching_old_lease_and_sql_policy_cannot_reset_sealed_revision(
    harness: _Harness, secret_check: Mock, sealed_revision: int | None
) -> None:
    assert harness.authorization is not None
    assert (
        harness.authorization.tenant_access_revision == harness.lease.claims.tenant_access_revision
    )
    harness.sealed_tenant_access_revision = sealed_revision
    await _deny(harness)
    secret_check.assert_not_called()
    assert "credential" not in harness.events
    assert "seal" not in harness.events
    assert harness.sessions == []
    assert harness.sealed_tenant_access_revision == sealed_revision


async def test_policy_delivery_during_secret_check_denies_even_with_unchanged_sql(
    harness: _Harness, secret_check: Mock
) -> None:
    def delivered(*_args: object, **_kwargs: object) -> bool:
        harness.event("bcrypt")
        harness.sealed_tenant_access_revision = 8
        return True

    secret_check.side_effect = delivered
    original_authorization = harness.authorization
    await _deny(harness)
    secret_check.assert_called_once()
    assert harness.authorization == original_authorization
    assert harness.events.count("authorization.assert") == 2
    assert "seal" not in harness.events
    assert harness.sessions == []


@pytest.mark.parametrize("change", ["lease", "shift", "epoch", "register"])
async def test_changing_lease_or_workplace_cannot_reset_attempt_state(
    harness: _Harness,
    secret_check: Mock,
    change: str,
) -> None:
    assert harness.guard is not None
    harness.guard = replace(
        harness.guard, failed_attempts=4, blocked_until=NOW + timedelta(minutes=5)
    )
    harness.high_water = harness.guard
    assert harness.authorization is not None
    claims = harness.lease.claims
    if change == "lease":
        claims = _changed(claims, "lease_id", _uuid(90))
        harness.authorization = _changed(harness.authorization, "lease_id", _uuid(90))
    elif change == "shift":
        harness.authorization = _changed(harness.authorization, "shift_context_id", _uuid(90))
    else:
        path = "scope.writer_epoch" if change == "epoch" else "scope.register_id"
        value = 99 if change == "epoch" else _uuid(90)
        claims = _changed(claims, path, value)
        harness.authorization = _changed(harness.authorization, path, value)
    harness.lease = _signed(claims)
    await _deny(harness)
    secret_check.assert_not_called()
    assert harness.guard.failed_attempts == 4
    assert harness.guard.attempt_sequence == 10
    assert set(harness.guard_keys[0]) == {"tenant_id", "edge_node_id", "user_id", "credential_id"}


async def test_session_deadline_is_bounded_by_lease_and_uses_fresh_clock(
    harness: _Harness,
    secret_check: Mock,
) -> None:
    harness.lease = _signed(
        _changed(harness.lease.claims, "expires_at", NOW + timedelta(seconds=2))
    )
    result = await harness.authenticator().authenticate(harness.lease, "input")
    assert result.authenticated_at == NOW + timedelta(seconds=1)
    assert result.expires_at == NOW + timedelta(seconds=2)
    assert result.monotonic_counter == 101


async def test_wrong_secret_preserves_existing_sql_and_protected_session(
    harness: _Harness,
    secret_check: Mock,
) -> None:
    original = await harness.authenticator().authenticate(harness.lease, "input")
    secret_check.side_effect = None
    secret_check.return_value = False
    await _deny(harness)
    assert harness.replacements == [original, None]
    assert harness.sessions == [original]
    assert harness.protected_session == original
    assert harness.guard is not None
    assert harness.guard.failed_attempts == 1


async def test_last_representable_attempt_commits_then_exhaustion_denies(
    harness: _Harness,
    secret_check: Mock,
) -> None:
    assert harness.guard is not None
    harness.guard = replace(harness.guard, attempt_sequence=MAX_SEQUENCE - 1)
    harness.high_water = harness.guard
    await harness.authenticator().authenticate(harness.lease, "input")
    assert harness.guard.attempt_sequence == MAX_SEQUENCE
    await _deny(harness)
    secret_check.assert_called_once()


async def test_not_yet_issued_lease_denies_before_bcrypt(
    harness: _Harness,
    secret_check: Mock,
) -> None:
    harness.lease = _signed(_changed(harness.lease.claims, "issued_at", NOW + timedelta(seconds=1)))
    await _deny(harness)
    secret_check.assert_not_called()


async def test_restored_old_sql_guard_is_rejected_by_independent_high_water(
    harness: _Harness,
    secret_check: Mock,
) -> None:
    old_guard = harness.guard
    await harness.authenticator().authenticate(harness.lease, "input")
    harness.guard = old_guard
    await _deny(harness)
    secret_check.assert_called_once()


@pytest.mark.parametrize("corruption", ["hash", "copy", "construct"])
async def test_unvalidated_lease_objects_are_rejected_before_state_access(
    harness: _Harness,
    secret_check: Mock,
    corruption: str,
) -> None:
    if corruption == "hash":
        harness.lease = harness.lease.model_copy(update={"claims_hash": "00" * 32})
    else:
        data = harness.lease.claims.model_dump(mode="python")
        data["lease_version"] = 0
        claims = (
            harness.lease.claims.model_copy(update={"lease_version": 0})
            if corruption == "copy"
            else OfflineAuthLeaseClaimsV1.model_construct(**data)
        )
        harness.lease = harness.lease.model_copy(update={"claims": claims})
    await _deny(harness)
    secret_check.assert_not_called()
    assert "state.enter" not in harness.events


async def test_independent_credential_context_cannot_be_replaced_by_record_context(
    harness: _Harness,
    issued: IssuedLocalCredentialV1,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(service, "verify_local_credential", verify_local_credential)
    context = _changed(harness.material.context, "protection_key.key_id", _uuid(90))
    harness.material = replace(harness.material, context=context)
    assert harness.guard is not None
    harness.guard = replace(
        harness.guard, context_digest=local_credential_context_digest(context).hex()
    )
    harness.high_water = harness.guard
    with pytest.raises(OfflineAuthUnavailableError, match=OFFLINE_AUTH_UNAVAILABLE_MESSAGE):
        await harness.authenticator().authenticate(harness.lease, issued.secret.get_secret_value())
    assert harness.guard.failed_attempts == 1
    assert harness.sessions == []
