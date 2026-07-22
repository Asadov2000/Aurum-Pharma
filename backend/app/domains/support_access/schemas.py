"""External schemas for short-lived tenant support access."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SupportCapabilityRead(BaseModel):
    code: str
    group_code: str
    name: str
    description: str | None
    is_dangerous: bool
    risk_level: str


class SupportAccessSessionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: UUID
    reason: str = Field(min_length=10, max_length=500)
    duration_minutes: int = Field(default=15, ge=5, le=20)
    capabilities: list[str] = Field(min_length=1, max_length=5)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 10:
            raise ValueError("reason must contain at least 10 non-whitespace characters")
        return normalized

    @field_validator("capabilities")
    @classmethod
    def unique_capabilities(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(value))


class SupportAccessSessionRead(BaseModel):
    id: UUID
    tenant_id: UUID
    tenant_name: str
    actor_user_id: UUID
    reason: str
    capabilities: list[str]
    is_read_only: bool
    started_at: datetime
    expires_at: datetime
    revoked_at: datetime | None


class SupportAccessSessionList(BaseModel):
    items: list[SupportAccessSessionRead]


class SupportAccessSessionRevoke(BaseModel):
    status: str = "revoked"
