"""Validated administrative policy contract."""

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

AccessModule = Literal["catalog", "incoming", "inventory", "suppliers", "pos", "staff", "settings"]


class AccessPolicyFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["full", "readonly"] = "full"
    blocked_modules: tuple[AccessModule, ...] = ()
    max_branches: int | None = Field(default=None, gt=0, le=2147483647, strict=True)
    max_users: int | None = Field(default=None, gt=0, le=2147483647, strict=True)
    max_registers: int | None = Field(default=None, gt=0, le=2147483647, strict=True)
    expires_at: datetime | None = None

    @field_validator("blocked_modules")
    @classmethod
    def unique_modules(cls, value: tuple[AccessModule, ...]) -> tuple[AccessModule, ...]:
        if len(set(value)) != len(value):
            raise ValueError("Modules must be unique")
        return value

    @field_validator("expires_at")
    @classmethod
    def aware_expiry(cls, value: datetime | None) -> datetime | None:
        if value is not None:
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("Expiration requires a timezone")
            return value.astimezone(UTC)
        return value


class AccessPolicyUpdate(AccessPolicyFields):
    expected_version: int = Field(ge=1, strict=True)
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("reason")
    @classmethod
    def clean_reason(cls, value: str) -> str:
        if len(value.strip()) < 3:
            raise ValueError("A meaningful reason is required")
        return value.strip()


class AccessPolicyTenantRead(AccessPolicyFields):
    model_config = ConfigDict(from_attributes=True)

    tenant_id: UUID
    version: int
    updated_at: datetime


class AccessPolicyRead(AccessPolicyTenantRead):
    reason: str
    updated_by: UUID | None
