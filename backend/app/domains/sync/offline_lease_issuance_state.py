"""Trusted Cloud issuance inputs; never an HTTP request or local admission state."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domains.sync.schemas import (
    OfflineAuthDeviceBindingV0,
    OfflineAuthLeaseReferenceV1,
    OfflineAuthScopeV0,
    OfflinePosCommand,
    SignedOfflineAuthLeaseV1,
)


class CloudOfflineLeaseIssuanceStateV1(BaseModel):
    """Active requires current owner permission, subject rights and enrollment.

    The trusted adapter intersects rights with the admitted command set and
    checks every reference/revision for this exact scope under the issuance lock.
    Device eligibility includes activation, register and active writer epoch.
    """

    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )

    scope: OfflineAuthScopeV0
    device_binding: OfflineAuthDeviceBindingV0
    policy_revision: int = Field(ge=1, le=9_223_372_036_854_775_807)
    subject_revision: int = Field(ge=1, le=9_223_372_036_854_775_807)
    allowed_commands: tuple[OfflinePosCommand, ...] = Field(min_length=1, max_length=6)
    admission: OfflineAuthLeaseReferenceV1
    local_credential: OfflineAuthLeaseReferenceV1
    reauthentication_policy: OfflineAuthLeaseReferenceV1
    admission_expires_at: datetime
    credential_expires_at: datetime
    device_expires_at: datetime
    policy_expires_at: datetime
    status: Literal["active", "revoked", "blocked"]

    @field_validator("allowed_commands")
    @classmethod
    def validate_commands(
        cls, value: tuple[OfflinePosCommand, ...]
    ) -> tuple[OfflinePosCommand, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("Issuance commands must be unique and sorted")
        return value

    @field_validator(
        "admission_expires_at", "credential_expires_at", "device_expires_at", "policy_expires_at"
    )
    @classmethod
    def validate_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("Cloud issuance deadlines must use UTC")
        return value


class CloudOfflineLeaseIssuanceTransactionV1(Protocol):
    """Serialize issuance against revocation, role, admission and writer changes.

    Ordinary tenant isolation applies. Keep all checks and issuance persistence
    in one transaction. commit() must confirm durable storage; exiting cannot
    auto-commit an incomplete operation or publish a staged envelope.
    """

    async def read_current_authorization(
        self, scope: OfflineAuthScopeV0
    ) -> CloudOfflineLeaseIssuanceStateV1 | None: ...

    async def read_cloud_time(self) -> datetime: ...

    async def record_issued_lease(self, lease: SignedOfflineAuthLeaseV1) -> None: ...

    async def commit(self) -> None: ...


class CloudOfflineLeaseIssuanceProviderV1(Protocol):
    def serialized(self) -> AbstractAsyncContextManager[CloudOfflineLeaseIssuanceTransactionV1]: ...
