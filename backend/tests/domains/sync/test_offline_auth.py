"""Offline-auth v0 contracts remain strict and runtime-denied."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domains.sync.offline_auth import (
    LocalOfflineSession,
    OfflineAuthContractError,
    OfflineAuthDecisionPipeline,
    OfflineAuthorizationSnapshotV0,
    OfflineAuthRevisionObservation,
    OfflineAuthUnavailableError,
    TrustedClockReading,
    assert_offline_auth_claims_hash,
    offline_auth_claims_bytes,
    offline_auth_claims_hash,
    runtime_offline_auth_verifier,
)
from app.domains.sync.schemas import (
    OfflineAuthDeviceBindingV0,
    OfflineAuthGrantClaimsV0,
    OfflineAuthPayloadV0,
    OfflineAuthScopeV0,
    OfflinePosCommand,
    SignedOfflineAuthGrantV0,
)

AUTHENTICATED_AT = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
GRANT_ID_A = UUID("10000000-0000-4000-8000-000000000001")
GRANT_ID_B = UUID("10000000-0000-4000-8000-000000000002")


def _claims(
    *,
    grant_id: UUID = GRANT_ID_A,
    allowed_commands: tuple[OfflinePosCommand, ...] = (
        "operation.result.read",
        "sale.cash.complete",
    ),
    authenticated_at: datetime = AUTHENTICATED_AT,
    issued_at: datetime = AUTHENTICATED_AT + timedelta(minutes=5),
    expires_at: datetime = AUTHENTICATED_AT + timedelta(hours=72),
) -> OfflineAuthGrantClaimsV0:
    return OfflineAuthGrantClaimsV0(
        schema_version=1,
        grant_id=grant_id,
        issuer="aurum-cloud",
        audience="aurum-edge-offline-auth-v0",
        auth_context="fresh-online-interactive",
        scope=OfflineAuthScopeV0(
            activation_id=UUID("20000000-0000-4000-8000-000000000001"),
            tenant_id=UUID("30000000-0000-4000-8000-000000000001"),
            branch_id=UUID("40000000-0000-4000-8000-000000000001"),
            edge_node_id=UUID("50000000-0000-4000-8000-000000000001"),
            register_id=UUID("60000000-0000-4000-8000-000000000001"),
            writer_epoch=7,
            user_id=UUID("70000000-0000-4000-8000-000000000001"),
            capability="cash_sale_v1",
        ),
        allowed_commands=allowed_commands,
        device_binding=OfflineAuthDeviceBindingV0(
            method="tpm2",
            key_id=UUID("80000000-0000-4000-8000-000000000001"),
            spki_sha256="1" * 64,
        ),
        policy_revision=11,
        subject_revision=13,
        authenticated_at=authenticated_at,
        issued_at=issued_at,
        expires_at=expires_at,
    )


def _signed(claims: OfflineAuthGrantClaimsV0) -> SignedOfflineAuthGrantV0:
    return SignedOfflineAuthGrantV0(
        claims=claims,
        claims_hash=offline_auth_claims_hash(claims),
        signing_key_id=UUID("90000000-0000-4000-8000-000000000001"),
        signature_algorithm="ed25519-v1",
        signature="ab" * 64,
    )


@dataclass
class _SignatureVerifier:
    error: Exception | None = None
    calls: int = 0
    signing_bytes: bytes | None = None

    async def verify(
        self,
        grant: SignedOfflineAuthGrantV0,
        signing_bytes: bytes,
    ) -> None:
        del grant
        self.calls += 1
        self.signing_bytes = signing_bytes
        if self.error is not None:
            raise self.error


@dataclass
class _DeviceBindingVerifier:
    error: Exception | None = None
    calls: int = 0
    events: list[str] | None = None

    async def assert_possession(self, binding: OfflineAuthDeviceBindingV0) -> None:
        del binding
        self.calls += 1
        if self.events is not None:
            self.events.append("device")
        if self.error is not None:
            raise self.error


@dataclass
class _StateTransaction:
    active_scope: OfflineAuthScopeV0 | None
    active_device_binding: OfflineAuthDeviceBindingV0 | None
    local_session: LocalOfflineSession | None
    snapshot: OfflineAuthorizationSnapshotV0 | None
    clock: TrustedClockReading
    advance_error: Exception | None = None
    observations: list[OfflineAuthRevisionObservation] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    requested_scope: OfflineAuthScopeV0 | None = None

    async def read_active_scope(self) -> OfflineAuthScopeV0 | None:
        self.events.append("scope")
        return self.active_scope

    async def read_active_device_binding(
        self,
    ) -> OfflineAuthDeviceBindingV0 | None:
        self.events.append("binding")
        return self.active_device_binding

    async def read_local_session(self) -> LocalOfflineSession | None:
        self.events.append("session")
        return self.local_session

    async def read_authorization_snapshot(
        self,
        scope: OfflineAuthScopeV0,
    ) -> OfflineAuthorizationSnapshotV0 | None:
        self.events.append("snapshot")
        self.requested_scope = scope
        return self.snapshot

    async def read_trusted_time(self) -> TrustedClockReading:
        self.events.append("clock")
        return self.clock

    async def check_and_advance(
        self,
        observation: OfflineAuthRevisionObservation,
    ) -> None:
        self.events.append("advance")
        self.observations.append(observation)
        if self.advance_error is not None:
            raise self.advance_error


@dataclass
class _StateTransactionProvider:
    state: _StateTransaction
    exit_error: Exception | None = None
    enter_calls: int = 0

    @asynccontextmanager
    async def serialized(self) -> AsyncIterator[_StateTransaction]:
        self.enter_calls += 1
        self.state.events.append("enter")
        try:
            yield self.state
        except BaseException:
            self.state.events.append("rollback")
            raise
        else:
            self.state.events.append("commit")
            if self.exit_error is not None:
                raise self.exit_error


@dataclass
class _PipelineHarness:
    signature: _SignatureVerifier
    device_binding: _DeviceBindingVerifier
    state: _StateTransaction
    transaction: _StateTransactionProvider

    @classmethod
    def valid(cls, claims: OfflineAuthGrantClaimsV0) -> _PipelineHarness:
        state = _StateTransaction(
            active_scope=claims.scope,
            active_device_binding=claims.device_binding,
            local_session=LocalOfflineSession(
                user_id=claims.scope.user_id,
                authenticated_online_at=claims.authenticated_at,
            ),
            snapshot=OfflineAuthorizationSnapshotV0(
                scope=claims.scope,
                policy_revision=claims.policy_revision,
                subject_revision=claims.subject_revision,
                commands=frozenset(claims.allowed_commands),
            ),
            clock=TrustedClockReading(
                utc_now=claims.issued_at,
                monotonic_counter=41,
                continuity_id="sealed-clock-v1",
            ),
        )
        return cls(
            signature=_SignatureVerifier(),
            device_binding=_DeviceBindingVerifier(events=state.events),
            state=state,
            transaction=_StateTransactionProvider(state=state),
        )

    def pipeline(self) -> OfflineAuthDecisionPipeline:
        return OfflineAuthDecisionPipeline(
            signature_verifier=self.signature,
            device_binding_verifier=self.device_binding,
            state_transaction_provider=self.transaction,
        )


def _different_scope(scope: OfflineAuthScopeV0) -> OfflineAuthScopeV0:
    return OfflineAuthScopeV0.model_validate(
        {
            **scope.model_dump(mode="python"),
            "writer_epoch": scope.writer_epoch + 1,
        }
    )


def test_offline_auth_claims_have_stable_domain_separated_digest() -> None:
    claims = _claims()

    assert offline_auth_claims_hash(claims) == (
        "0899bed65cd3cd1f0df801dbef39b74e747636dbf1bfcdb5aabcf94e2728c930"
    )


def test_offline_auth_accepts_exact_freshness_and_lifetime_boundaries() -> None:
    claims = _claims()

    assert claims.issued_at - claims.authenticated_at == timedelta(minutes=5)
    assert claims.expires_at - claims.authenticated_at == timedelta(hours=72)


@pytest.mark.parametrize(
    ("issued_at", "expires_at"),
    [
        (
            AUTHENTICATED_AT + timedelta(minutes=5, microseconds=1),
            AUTHENTICATED_AT + timedelta(hours=1),
        ),
        (AUTHENTICATED_AT, AUTHENTICATED_AT),
        (AUTHENTICATED_AT, AUTHENTICATED_AT + timedelta(hours=72, microseconds=1)),
    ],
)
def test_offline_auth_rejects_invalid_time_windows(
    issued_at: datetime,
    expires_at: datetime,
) -> None:
    with pytest.raises(ValidationError):
        _claims(issued_at=issued_at, expires_at=expires_at)


def test_offline_auth_rejects_non_utc_timestamps() -> None:
    non_utc = AUTHENTICATED_AT.astimezone(timezone(timedelta(hours=5)))

    with pytest.raises(ValidationError):
        _claims(authenticated_at=non_utc)


@pytest.mark.parametrize(
    "commands",
    [
        (),
        ("sale.cash.complete", "sale.cash.complete"),
        ("sale.cash.complete", "operation.result.read"),
        ("pos.sell",),
    ],
)
def test_offline_auth_rejects_noncanonical_commands(commands: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError):
        OfflineAuthGrantClaimsV0.model_validate(
            {
                **_claims().model_dump(mode="python"),
                "allowed_commands": commands,
            }
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("password_hash", "forbidden"),
        ("refresh_token", "forbidden"),
        ("permissions", ("pos.sell",)),
    ],
)
def test_offline_auth_rejects_sensitive_or_unexpected_fields(field: str, value: object) -> None:
    payload = _claims().model_dump(mode="python")
    payload[field] = value

    with pytest.raises(ValidationError):
        OfflineAuthGrantClaimsV0.model_validate(payload)


def test_offline_auth_rejects_coerced_revisions() -> None:
    payload = _claims().model_dump(mode="python")
    payload["policy_revision"] = "11"

    with pytest.raises(ValidationError):
        OfflineAuthGrantClaimsV0.model_validate(payload)


def test_offline_auth_rejects_development_hmac_algorithm() -> None:
    payload = _signed(_claims()).model_dump(mode="python")
    payload["signature_algorithm"] = "hmac-sha256-edge-v1"

    with pytest.raises(ValidationError):
        SignedOfflineAuthGrantV0.model_validate(payload)


def test_offline_auth_detects_claim_tampering() -> None:
    grant = _signed(_claims())
    tampered_claims = _claims(allowed_commands=("receipt.reprint",))
    tampered = SignedOfflineAuthGrantV0(
        claims=tampered_claims,
        claims_hash=grant.claims_hash,
        signing_key_id=grant.signing_key_id,
        signature_algorithm="ed25519-v1",
        signature=grant.signature,
    )

    with pytest.raises(OfflineAuthContractError):
        assert_offline_auth_claims_hash(tampered)


def test_offline_auth_payload_requires_unique_canonical_grant_order() -> None:
    grant_a = _signed(_claims(grant_id=GRANT_ID_A))
    grant_b = _signed(_claims(grant_id=GRANT_ID_B))

    payload = OfflineAuthPayloadV0(
        schema_version=1,
        component="offline_auth",
        grants=(grant_a, grant_b),
    )
    assert len(payload.grants) == 2

    with pytest.raises(ValidationError):
        OfflineAuthPayloadV0(
            schema_version=1,
            component="offline_auth",
            grants=(grant_b, grant_a),
        )
    with pytest.raises(ValidationError):
        OfflineAuthPayloadV0(
            schema_version=1,
            component="offline_auth",
            grants=(grant_a, grant_a),
        )


async def test_offline_auth_pipeline_allows_only_after_every_check() -> None:
    claims = _claims()
    harness = _PipelineHarness.valid(claims)

    principal = await harness.pipeline().authorize(
        _signed(claims),
        "sale.cash.complete",
    )

    assert principal.grant_id == claims.grant_id
    assert principal.user_id == claims.scope.user_id
    assert principal.command == "sale.cash.complete"
    assert principal.expires_at == claims.expires_at
    assert principal.scope == claims.scope
    assert harness.signature.calls == 1
    assert harness.signature.signing_bytes == offline_auth_claims_bytes(claims)
    assert harness.device_binding.calls == 1
    assert harness.transaction.enter_calls == 1
    assert harness.state.requested_scope == claims.scope
    assert harness.state.events == [
        "enter",
        "scope",
        "binding",
        "device",
        "session",
        "snapshot",
        "clock",
        "advance",
        "commit",
    ]
    assert harness.state.observations == [
        OfflineAuthRevisionObservation(
            grant_id=claims.grant_id,
            user_id=claims.scope.user_id,
            policy_revision=claims.policy_revision,
            subject_revision=claims.subject_revision,
            trusted_time=harness.state.clock,
        )
    ]


async def test_offline_auth_pipeline_revalidates_constructed_grant_before_signature() -> None:
    claims = _claims()
    valid_grant = _signed(claims)
    bypassed_grant = SignedOfflineAuthGrantV0.model_construct(
        claims=claims,
        claims_hash="invalid",
        signing_key_id=valid_grant.signing_key_id,
        signature_algorithm="ed25519-v1",
        signature=valid_grant.signature,
    )
    harness = _PipelineHarness.valid(claims)

    with pytest.raises(
        OfflineAuthUnavailableError,
        match="^Offline authentication is unavailable$",
    ):
        await harness.pipeline().authorize(bypassed_grant, "sale.cash.complete")

    assert harness.signature.calls == 0
    assert harness.state.observations == []


async def test_offline_auth_pipeline_rejects_tampering_before_signature() -> None:
    claims = _claims()
    grant = _signed(claims)
    tampered_claims = _claims(allowed_commands=("receipt.reprint",))
    tampered_grant = SignedOfflineAuthGrantV0(
        claims=tampered_claims,
        claims_hash=grant.claims_hash,
        signing_key_id=grant.signing_key_id,
        signature_algorithm="ed25519-v1",
        signature=grant.signature,
    )
    harness = _PipelineHarness.valid(tampered_claims)

    with pytest.raises(OfflineAuthUnavailableError):
        await harness.pipeline().authorize(tampered_grant, "receipt.reprint")

    assert harness.signature.calls == 0
    assert harness.state.observations == []


async def test_offline_auth_pipeline_normalizes_signature_failure() -> None:
    claims = _claims()
    harness = _PipelineHarness.valid(claims)
    harness.signature.error = RuntimeError("sensitive adapter detail")

    with pytest.raises(OfflineAuthUnavailableError) as exc_info:
        await harness.pipeline().authorize(_signed(claims), "sale.cash.complete")

    assert str(exc_info.value) == "Offline authentication is unavailable"
    assert exc_info.value.__cause__ is None
    assert harness.transaction.enter_calls == 0
    assert harness.state.observations == []


async def test_offline_auth_pipeline_rejects_unknown_command_before_signature() -> None:
    claims = _claims()
    harness = _PipelineHarness.valid(claims)

    with pytest.raises(OfflineAuthUnavailableError):
        await harness.pipeline().authorize(
            _signed(claims),
            cast(OfflinePosCommand, "sale.card.complete"),
        )

    assert harness.signature.calls == 0
    assert harness.state.observations == []


async def test_offline_auth_pipeline_rejects_command_absent_from_grant() -> None:
    claims = _claims()
    harness = _PipelineHarness.valid(claims)

    with pytest.raises(OfflineAuthUnavailableError):
        await harness.pipeline().authorize(_signed(claims), "shift.open")

    assert harness.signature.calls == 1
    assert harness.transaction.enter_calls == 0
    assert harness.state.observations == []


@pytest.mark.parametrize(
    "case",
    [
        "active_scope_missing",
        "active_scope_changed",
        "active_device_binding_missing",
        "active_device_binding_changed",
        "device_binding_failure",
        "local_session_missing",
        "local_session_wrong_user",
        "local_session_wrong_authentication",
        "snapshot_missing",
        "snapshot_wrong_scope",
        "snapshot_wrong_policy_revision",
        "snapshot_wrong_subject_revision",
        "snapshot_command_missing",
        "trusted_time_before_issue",
        "trusted_time_at_expiry",
    ],
)
async def test_offline_auth_pipeline_denies_each_runtime_mismatch(case: str) -> None:
    claims = _claims()
    harness = _PipelineHarness.valid(claims)
    mutations: dict[str, tuple[object, str, object]] = {
        "active_scope_missing": (harness.state, "active_scope", None),
        "active_scope_changed": (
            harness.state,
            "active_scope",
            _different_scope(claims.scope),
        ),
        "active_device_binding_missing": (
            harness.state,
            "active_device_binding",
            None,
        ),
        "active_device_binding_changed": (
            harness.state,
            "active_device_binding",
            OfflineAuthDeviceBindingV0(
                method="tpm2",
                key_id=UUID("80000000-0000-4000-8000-000000000002"),
                spki_sha256="2" * 64,
            ),
        ),
        "device_binding_failure": (
            harness.device_binding,
            "error",
            RuntimeError("TPM proof failed"),
        ),
        "local_session_missing": (harness.state, "local_session", None),
        "local_session_wrong_user": (
            harness.state,
            "local_session",
            LocalOfflineSession(
                user_id=UUID("70000000-0000-4000-8000-000000000002"),
                authenticated_online_at=claims.authenticated_at,
            ),
        ),
        "local_session_wrong_authentication": (
            harness.state,
            "local_session",
            LocalOfflineSession(
                user_id=claims.scope.user_id,
                authenticated_online_at=claims.authenticated_at + timedelta(seconds=1),
            ),
        ),
        "snapshot_missing": (harness.state, "snapshot", None),
        "snapshot_wrong_scope": (
            harness.state,
            "snapshot",
            OfflineAuthorizationSnapshotV0(
                scope=_different_scope(claims.scope),
                policy_revision=claims.policy_revision,
                subject_revision=claims.subject_revision,
                commands=frozenset(claims.allowed_commands),
            ),
        ),
        "snapshot_wrong_policy_revision": (
            harness.state,
            "snapshot",
            OfflineAuthorizationSnapshotV0(
                scope=claims.scope,
                policy_revision=claims.policy_revision + 1,
                subject_revision=claims.subject_revision,
                commands=frozenset(claims.allowed_commands),
            ),
        ),
        "snapshot_wrong_subject_revision": (
            harness.state,
            "snapshot",
            OfflineAuthorizationSnapshotV0(
                scope=claims.scope,
                policy_revision=claims.policy_revision,
                subject_revision=claims.subject_revision + 1,
                commands=frozenset(claims.allowed_commands),
            ),
        ),
        "snapshot_command_missing": (
            harness.state,
            "snapshot",
            OfflineAuthorizationSnapshotV0(
                scope=claims.scope,
                policy_revision=claims.policy_revision,
                subject_revision=claims.subject_revision,
                commands=frozenset({"operation.result.read"}),
            ),
        ),
        "trusted_time_before_issue": (
            harness.state,
            "clock",
            TrustedClockReading(
                utc_now=claims.issued_at - timedelta(microseconds=1),
                monotonic_counter=41,
                continuity_id="sealed-clock-v1",
            ),
        ),
        "trusted_time_at_expiry": (
            harness.state,
            "clock",
            TrustedClockReading(
                utc_now=claims.expires_at,
                monotonic_counter=41,
                continuity_id="sealed-clock-v1",
            ),
        ),
    }
    target, attribute, value = mutations[case]
    setattr(target, attribute, value)

    with pytest.raises(
        OfflineAuthUnavailableError,
        match="^Offline authentication is unavailable$",
    ):
        await harness.pipeline().authorize(_signed(claims), "sale.cash.complete")

    assert harness.state.observations == []


@pytest.mark.parametrize(
    "trusted_now",
    [
        AUTHENTICATED_AT + timedelta(minutes=5),
        AUTHENTICATED_AT + timedelta(hours=72) - timedelta(microseconds=1),
    ],
)
async def test_offline_auth_pipeline_accepts_closed_open_time_window(
    trusted_now: datetime,
) -> None:
    claims = _claims()
    harness = _PipelineHarness.valid(claims)
    harness.state.clock = TrustedClockReading(
        utc_now=trusted_now,
        monotonic_counter=42,
        continuity_id="sealed-clock-v1",
    )

    principal = await harness.pipeline().authorize(
        _signed(claims),
        "sale.cash.complete",
    )

    assert principal.expires_at == claims.expires_at
    assert len(harness.state.observations) == 1


async def test_offline_auth_pipeline_denies_failed_atomic_advance() -> None:
    claims = _claims()
    harness = _PipelineHarness.valid(claims)
    harness.state.advance_error = RuntimeError("sealed storage unavailable")

    with pytest.raises(OfflineAuthUnavailableError) as exc_info:
        await harness.pipeline().authorize(_signed(claims), "sale.cash.complete")

    assert str(exc_info.value) == "Offline authentication is unavailable"
    assert exc_info.value.__cause__ is None
    assert len(harness.state.observations) == 1


async def test_offline_auth_pipeline_denies_failed_transaction_commit() -> None:
    claims = _claims()
    harness = _PipelineHarness.valid(claims)
    harness.transaction.exit_error = RuntimeError("durable commit failed")

    with pytest.raises(OfflineAuthUnavailableError) as exc_info:
        await harness.pipeline().authorize(_signed(claims), "sale.cash.complete")

    assert str(exc_info.value) == "Offline authentication is unavailable"
    assert exc_info.value.__cause__ is None
    assert harness.state.events[-2:] == ["advance", "commit"]
    assert len(harness.state.observations) == 1


def test_offline_auth_runtime_state_contracts_reject_unsafe_values() -> None:
    claims = _claims()
    non_utc = claims.authenticated_at.astimezone(timezone(timedelta(hours=5)))

    with pytest.raises(ValueError, match="must be UTC"):
        LocalOfflineSession(
            user_id=claims.scope.user_id,
            authenticated_online_at=non_utc,
        )
    with pytest.raises(ValueError, match="revisions must be positive"):
        OfflineAuthorizationSnapshotV0(
            scope=claims.scope,
            policy_revision=0,
            subject_revision=claims.subject_revision,
            commands=frozenset(claims.allowed_commands),
        )
    with pytest.raises(ValueError, match="must be a frozenset"):
        OfflineAuthorizationSnapshotV0(
            scope=claims.scope,
            policy_revision=claims.policy_revision,
            subject_revision=claims.subject_revision,
            commands=cast(frozenset[OfflinePosCommand], claims.allowed_commands),
        )


@pytest.mark.parametrize(
    "command",
    [
        "operation.result.read",
        "receipt.print",
        "receipt.reprint",
        "sale.cash.complete",
        "shift.close",
        "shift.open",
    ],
)
async def test_runtime_offline_auth_is_unconditionally_denied(
    command: OfflinePosCommand,
) -> None:
    verifier = runtime_offline_auth_verifier()

    with pytest.raises(OfflineAuthUnavailableError, match="unavailable"):
        await verifier.authorize(_signed(_claims()), command)
