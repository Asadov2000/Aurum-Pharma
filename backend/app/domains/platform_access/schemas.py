"""External schemas for protected platform access grants."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class PlatformAccessKind(StrEnum):
    DEVELOPER = "developer"
    ADMINISTRATOR = "administrator"


class PlatformCapabilityCode(StrEnum):
    TENANTS_VIEW = "platform.tenants.view"
    TENANTS_MANAGE = "platform.tenants.manage"
    MEMBERSHIPS_MANAGE = "platform.memberships.manage"
    OWNERSHIP_PROVISION = "platform.ownership.provision"
    BILLING_MANAGE = "platform.billing.manage"
    BILLING_VIEW = "platform.billing.view"
    BILLING_PAYMENT_REVIEW = "platform.billing.payment.review"
    BILLING_PAYMENT_APPROVE = "platform.billing.payment.approve"
    BILLING_INVOICE_ISSUE = "platform.billing.invoice.issue"
    BILLING_ADJUSTMENT_CREATE = "platform.billing.adjustment.create"
    BILLING_ADJUSTMENT_APPROVE = "platform.billing.adjustment.approve"
    BILLING_PLAN_MANAGE = "platform.billing.plan.manage"
    BILLING_AUDIT_VIEW = "platform.billing.audit.view"
    SUPPORT_USE = "platform.support.use"
    SYNC_VIEW = "platform.sync.view"
    SYNC_MANAGE = "platform.sync.manage"
    AUDIT_GLOBAL_VIEW = "platform.audit.global.view"
    ACCESS_VIEW = "platform.access.view"
    ACCESS_MANAGE = "platform.access.manage"
    ACCOUNTS_VIEW = "platform.accounts.view"
    ACCOUNTS_MANAGE = "platform.accounts.manage"


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
    capabilities: list[PlatformCapabilityCode] = Field(min_length=1, max_length=32)
    reason_code: PlatformAccessReasonCode
    reason: str = Field(min_length=10, max_length=500)

    @field_validator("capabilities")
    @classmethod
    def normalize_capabilities(
        cls, value: list[PlatformCapabilityCode]
    ) -> list[PlatformCapabilityCode]:
        if len(set(value)) != len(value):
            raise ValueError("capabilities must be unique")
        return sorted(value, key=str)

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
    capabilities: list[PlatformCapabilityCode]
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
    user_email: str | None = None
    user_full_name: str | None = None


class PlatformAccessGrantList(BaseModel):
    items: list[PlatformAccessGrantRead]


class PlatformCapabilityRead(BaseModel):
    code: PlatformCapabilityCode
    group_code: str
    name: str
    description: str | None
    risk_level: str
    requires_step_up: bool
    requires_confirmation: bool
