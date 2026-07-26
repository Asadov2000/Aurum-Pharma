"""External schemas for protected platform access grants."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PlatformAccessKind(StrEnum):
    DEVELOPER = "developer"
    ADMINISTRATOR = "administrator"


class PlatformAccessStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class PlatformAccessReasonCode(StrEnum):
    PLATFORM_STAFF_ONBOARDING = "platform_staff_onboarding"
    RESPONSIBILITY_CHANGE = "responsibility_change"
    SECURITY_INCIDENT = "security_incident"
    ACCESS_REVIEW = "access_review"
    OTHER = "other"


def _normalize_reason(value: str) -> str:
    normalized = " ".join(value.split())
    if len(normalized) < 10:
        raise ValueError("reason must contain at least 10 non-whitespace characters")
    return normalized


class PlatformAccessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    access_kind: PlatformAccessKind
    reason_code: PlatformAccessReasonCode
    reason: str = Field(min_length=10, max_length=500)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        return _normalize_reason(value)


class PlatformAccessApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    reason_code: PlatformAccessReasonCode
    reason: str = Field(min_length=10, max_length=500)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        return _normalize_reason(value)


class PlatformAccessRevocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    reason_code: PlatformAccessReasonCode
    reason: str = Field(min_length=10, max_length=500)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        return _normalize_reason(value)


class PlatformAccessGrantRead(BaseModel):
    id: UUID
    user_id: UUID
    access_kind: PlatformAccessKind
    status: PlatformAccessStatus
    requested_by: UUID | None
    request_reason_code: str
    request_reason: str
    requested_at: datetime
    requires_approval: bool
    approval_expires_at: datetime | None
    approved_by: UUID | None
    approved_at: datetime | None
    approval_reason_code: str | None
    approval_reason: str | None
    revoked_by: UUID | None
    revoked_at: datetime | None
    revoke_reason_code: str | None
    revoke_reason: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class PlatformAccessGrantList(BaseModel):
    items: list[PlatformAccessGrantRead]
