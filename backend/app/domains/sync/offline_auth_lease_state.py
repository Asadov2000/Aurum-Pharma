"""Trusted adapter state for lease v1, never client-supplied login evidence."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol, Self

from pydantic import UUID4, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domains.sync.offline_auth import TrustedClockReading
from app.domains.sync.schemas import (
    OfflineAuthDeviceBindingV0,
    OfflineAuthLeaseReferenceV1,
    OfflineAuthScopeV0,
    OfflinePosCommand,
)


class _TrustedLeaseState(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )


class OfflineLeaseReauthenticationPolicyV1(_TrustedLeaseState):
    reference: OfflineAuthLeaseReferenceV1
    max_session_seconds: int = Field(ge=1, le=86_400)
    confirmation_commands: tuple[OfflinePosCommand, ...] = Field(max_length=6)
    max_confirmation_seconds: int = Field(ge=1, le=300)

    @field_validator("confirmation_commands")
    @classmethod
    def validate_commands(
        cls, value: tuple[OfflinePosCommand, ...]
    ) -> tuple[OfflinePosCommand, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("confirmation commands must be unique and sorted")
        return value


class OfflineLeaseAuthorizationStateV1(_TrustedLeaseState):
    """Active means owner admission and credential validity were checked by the adapter."""

    lease_id: UUID4
    scope: OfflineAuthScopeV0
    device_binding: OfflineAuthDeviceBindingV0
    policy_revision: int = Field(ge=1, le=9_223_372_036_854_775_807)
    subject_revision: int = Field(ge=1, le=9_223_372_036_854_775_807)
    allowed_commands: tuple[OfflinePosCommand, ...] = Field(min_length=1, max_length=6)
    admission: OfflineAuthLeaseReferenceV1
    local_credential: OfflineAuthLeaseReferenceV1
    credential_audience: Literal["aurum-edge-local-auth-v1"]
    reauthentication_policy: OfflineLeaseReauthenticationPolicyV1
    shift_context_id: UUID4
    status: Literal["active", "revoked", "locally-blocked"]

    @field_validator("allowed_commands")
    @classmethod
    def validate_commands(
        cls, value: tuple[OfflinePosCommand, ...]
    ) -> tuple[OfflinePosCommand, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("authorization commands must be unique and sorted")
        return value


class LocalOfflineCommandConfirmationV1(_TrustedLeaseState):
    command: OfflinePosCommand
    confirmed_at: datetime
    monotonic_counter: int = Field(ge=0, le=9_223_372_036_854_775_807)

    @field_validator("confirmed_at")
    @classmethod
    def validate_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("local confirmation must use UTC")
        return value


class LocalOfflineLeaseSessionV1(_TrustedLeaseState):
    """Only a trusted local authenticator may create this server-side session."""

    session_id: UUID4
    lease_id: UUID4
    scope: OfflineAuthScopeV0
    local_credential: OfflineAuthLeaseReferenceV1
    credential_audience: Literal["aurum-edge-local-auth-v1"]
    reauthentication_policy: OfflineAuthLeaseReferenceV1
    shift_context_id: UUID4
    authenticated_at: datetime
    expires_at: datetime
    continuity_id: str = Field(min_length=1, max_length=128)
    monotonic_counter: int = Field(ge=0, le=9_223_372_036_854_775_807)
    confirmation: LocalOfflineCommandConfirmationV1 | None = None

    @field_validator("authenticated_at", "expires_at")
    @classmethod
    def validate_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("local session must use UTC")
        return value

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.authenticated_at >= self.expires_at:
            raise ValueError("local session window is inconsistent")
        return self


@dataclass(frozen=True, slots=True)
class OfflineLeaseRevisionObservationV1:
    """Everything the durable adapter must compare with its sealed high-water state."""

    authorization: OfflineLeaseAuthorizationStateV1
    session: LocalOfflineLeaseSessionV1
    trusted_time: TrustedClockReading


class OfflineLeaseStateTransactionV1(Protocol):
    """Hold revocation/session/clock serialization until crash-safe commit succeeds."""

    async def read_authorization(self) -> OfflineLeaseAuthorizationStateV1 | None: ...

    async def read_local_session(self) -> LocalOfflineLeaseSessionV1 | None: ...

    async def read_trusted_time(self) -> TrustedClockReading: ...

    async def check_and_advance(self, observation: OfflineLeaseRevisionObservationV1) -> None: ...


class OfflineLeaseStateTransactionProviderV1(Protocol):
    def serialized(self) -> AbstractAsyncContextManager[OfflineLeaseStateTransactionV1]: ...
