"""Typed, bounded snapshot data for the independently anchored Windows reader.

Parsing and hashing establish no admission, signature, trusted time or enrollment.
The Windows owner must compare the exact payload digest with its independently
authenticated checkpoint. This format contains no credential verifier or secret.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Literal, Self

from pydantic import UUID4, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domains.sync.integrity import canonical_json_bytes
from app.domains.sync.local_credentials import (
    LocalCredentialContextV1,
    local_credential_context_digest,
)
from app.domains.sync.offline_auth_lease_state import (
    LocalOfflineLeaseSessionV1,
    OfflineLeaseAuthorizationStateV1,
)
from app.domains.sync.schemas import (
    CloudTenantAccessPolicyV1,
    OfflineAuthDeviceBindingV0,
    OfflinePolicyDeliveryScopeV1,
)

MAX_PROTECTED_CASHIER_STATE_BYTES = 1_048_576
_MAX_SEQUENCE = 9_223_372_036_854_775_807
_MAX_DEPTH = 32
_DOMAIN = b"aurum:protected-cashier-state:v1\x00"
_DIGEST_PATTERN = r"^[0-9a-f]{64}$"
_UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-4[0-9a-fA-F]{3}-[89aAbB][0-9a-fA-F]{3}" r"-[0-9a-fA-F]{12}\Z"
)
_UTC_PATTERN = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}" r"(?:\.[0-9]{1,6})?(?:Z|\+00:00)\Z"
)
_UUID_FIELDS = frozenset(
    {
        "activation_id",
        "tenant_id",
        "branch_id",
        "edge_node_id",
        "register_id",
        "user_id",
        "key_id",
        "reference_id",
        "lease_id",
        "shift_context_id",
        "credential_id",
        "session_id",
    }
)
_TIME_FIELDS = frozenset(
    {"utc_now", "last_utc", "blocked_until", "expires_at", "authenticated_at", "confirmed_at"}
)


class _ProtectedStateModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        frozen=True,
        validate_default=True,
        revalidate_instances="always",
    )


class ProtectedCashierPolicyFloorV1(_ProtectedStateModel):
    delivery_sequence: int = Field(ge=1, le=_MAX_SEQUENCE)
    claims_hash: str = Field(pattern=_DIGEST_PATTERN)
    policy: CloudTenantAccessPolicyV1


class ProtectedCashierObservedTimeV1(_ProtectedStateModel):
    utc_now: datetime
    monotonic_counter: int = Field(ge=0, le=_MAX_SEQUENCE)
    continuity_id: str = Field(min_length=1, max_length=128)

    @field_validator("utc_now")
    @classmethod
    def validate_utc(cls, value: datetime) -> datetime:
        offset = value.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("Protected observation must use UTC")
        return value


class ProtectedCashierGuardV1(_ProtectedStateModel):
    """Strict non-secret mirror of LocalAuthGuardSnapshotV1."""

    tenant_id: UUID4
    branch_id: UUID4
    edge_node_id: UUID4
    user_id: UUID4
    credential_id: UUID4
    credential_revision: int = Field(ge=1, le=_MAX_SEQUENCE)
    context_digest: str = Field(pattern=_DIGEST_PATTERN)
    failed_attempts: int = Field(ge=0, le=2_147_483_647)
    attempt_sequence: int = Field(ge=0, le=_MAX_SEQUENCE)
    blocked_until: datetime | None
    last_utc: datetime
    last_counter: int = Field(ge=0, le=_MAX_SEQUENCE)
    continuity_id: str = Field(min_length=1, max_length=128)

    @field_validator("last_utc", "blocked_until")
    @classmethod
    def validate_utc(cls, value: datetime | None) -> datetime | None:
        offset = None if value is None else value.utcoffset()
        if value is not None and (offset is None or offset.total_seconds() != 0):
            raise ValueError("Protected guard must use UTC")
        return value


class ProtectedCashierRecordV1(_ProtectedStateModel):
    authorization: OfflineLeaseAuthorizationStateV1
    credential_context: LocalCredentialContextV1
    guard: ProtectedCashierGuardV1

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        auth = self.authorization
        context = self.credential_context
        guard = self.guard
        if (
            context.scope != auth.scope
            or context.local_credential != auth.local_credential
            or context.device_binding != auth.device_binding
            or context.audience != auth.credential_audience
            or guard.tenant_id != auth.scope.tenant_id
            or guard.branch_id != auth.scope.branch_id
            or guard.edge_node_id != auth.scope.edge_node_id
            or guard.user_id != auth.scope.user_id
            or guard.credential_id != auth.local_credential.reference_id
            or guard.credential_revision != auth.local_credential.revision
            or guard.context_digest != local_credential_context_digest(context).hex()
        ):
            raise ValueError("Protected cashier binding is inconsistent")
        return self


class ProtectedCashierStateV1(_ProtectedStateModel):
    schema_version: Literal[1]
    scope: OfflinePolicyDeliveryScopeV1
    device_binding: OfflineAuthDeviceBindingV0
    checkpoint_context_sha256: str = Field(pattern=_DIGEST_PATTERN)
    policy_floor: ProtectedCashierPolicyFloorV1
    observed_time: ProtectedCashierObservedTimeV1
    cashiers: tuple[ProtectedCashierRecordV1, ...] = Field(max_length=256)
    workplace_session: LocalOfflineLeaseSessionV1 | None

    @field_validator("schema_version", mode="before")
    @classmethod
    def validate_version_type(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Protected schema version must be an integer")
        return value

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        floor = self.policy_floor.policy
        clock = self.observed_time
        if floor.tenant_id != self.scope.tenant_id:
            raise ValueError("Protected policy tenant is inconsistent")
        users = tuple(record.authorization.scope.user_id.hex for record in self.cashiers)
        if users != tuple(sorted(set(users))):
            raise ValueError("Protected cashiers must have unique sorted users")
        for record in self.cashiers:
            auth = record.authorization
            guard = record.guard
            target = auth.scope.model_dump(mode="python", exclude={"user_id"})
            if (
                target != self.scope.model_dump(mode="python")
                or auth.device_binding != self.device_binding
                or auth.tenant_access_revision > floor.version
                or guard.continuity_id != clock.continuity_id
                or guard.last_utc > clock.utc_now
                or guard.last_counter > clock.monotonic_counter
            ):
                raise ValueError("Protected cashier snapshot is inconsistent")
        if self.workplace_session is not None:
            self._validate_session(self.workplace_session)
        return self

    def _validate_session(self, session: LocalOfflineLeaseSessionV1) -> None:
        if "confirmation" not in session.model_fields_set:
            raise ValueError("Protected session requires an explicit confirmation member")
        record = next(
            (item for item in self.cashiers if item.authorization.scope == session.scope), None
        )
        if record is None:
            raise ValueError("Protected workplace session has no cashier")
        auth = record.authorization
        clock = self.observed_time
        if (
            session.lease_id != auth.lease_id
            or session.local_credential != auth.local_credential
            or session.credential_audience != auth.credential_audience
            or session.reauthentication_policy != auth.reauthentication_policy.reference
            or session.shift_context_id != auth.shift_context_id
            or session.authenticated_at > clock.utc_now
            or session.continuity_id != clock.continuity_id
            or session.monotonic_counter > clock.monotonic_counter
        ):
            raise ValueError("Protected workplace session binding is inconsistent")
        confirmation = session.confirmation
        if confirmation is not None and (
            not session.authenticated_at <= confirmation.confirmed_at <= clock.utc_now
            or not session.monotonic_counter
            <= confirmation.monotonic_counter
            <= clock.monotonic_counter
        ):
            raise ValueError("Protected workplace confirmation is inconsistent")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate protected state member")
        result[key] = value
    return result


def _reject_non_integer(value: str) -> object:
    raise ValueError("Protected state numbers must be finite integers")


def _validate_wire(value: object, *, depth: int = 1) -> None:
    if isinstance(value, dict | list) and depth > _MAX_DEPTH:
        raise ValueError("Protected state nesting exceeds the limit")
    if isinstance(value, str):
        # Reject escaped unpaired surrogates as well as malformed raw UTF-8.
        value.encode("utf-8", errors="strict")
    elif isinstance(value, dict):
        for key, member in value.items():
            if not isinstance(key, str):
                raise ValueError("Protected state member names must be strings")
            key.encode("utf-8", errors="strict")
            if key in _UUID_FIELDS and (
                not isinstance(member, str) or _UUID_PATTERN.fullmatch(member) is None
            ):
                raise ValueError("Protected state UUID must use RFC v4 D format")
            if (
                key in _TIME_FIELDS
                and member is not None
                and (not isinstance(member, str) or _UTC_PATTERN.fullmatch(member) is None)
            ):
                raise ValueError("Protected state timestamp must use UTC ISO seconds")
            _validate_wire(member, depth=depth + 1)
    elif isinstance(value, list):
        for member in value:
            _validate_wire(member, depth=depth + 1)
    elif value is not None and type(value) is not int:
        raise ValueError("Protected state contains an unsupported JSON value")


def decode_protected_cashier_state(raw: bytes) -> ProtectedCashierStateV1:
    """Validate data only; callers must establish independent checkpoint trust."""
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_PROTECTED_CASHIER_STATE_BYTES:
        raise ValueError("Protected state payload size is invalid")
    try:
        parsed: object = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_float=_reject_non_integer,
            parse_constant=_reject_non_integer,
        )
        _validate_wire(parsed)
        return ProtectedCashierStateV1.model_validate_json(raw)
    except (RecursionError, UnicodeError) as error:
        raise ValueError("Protected state JSON is invalid") from error


def encode_protected_cashier_state(snapshot: ProtectedCashierStateV1) -> bytes:
    """Canonical bounded wire encoding; includes explicit null session state."""
    validated = ProtectedCashierStateV1.model_validate(snapshot.model_dump(mode="python"))
    raw = canonical_json_bytes(validated.model_dump(mode="json"))
    decode_protected_cashier_state(raw)
    return raw


def protected_cashier_state_digest(raw: bytes) -> bytes:
    """Hash exact valid bytes with domain separation, never normalized JSON."""
    decode_protected_cashier_state(raw)
    return hashlib.sha256(_DOMAIN + raw).digest()
