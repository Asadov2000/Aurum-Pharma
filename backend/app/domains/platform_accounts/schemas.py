"""External schemas for platform team account lifecycle."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class PlatformStaffStatus(StrEnum):
    INVITED = "invited"
    ACTIVE = "active"
    BLOCKED = "blocked"
    OFFBOARDED = "offboarded"


class PlatformStaffInvitationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    full_name: str = Field(min_length=2, max_length=200)

    @field_validator("full_name")
    @classmethod
    def normalize_full_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 2:
            raise ValueError("full_name must contain at least 2 characters")
        return normalized


class PlatformStaffAccountRead(BaseModel):
    user_id: UUID
    email: str
    full_name: str
    status: PlatformStaffStatus
    version: int
    invited_at: datetime
    invitation_expires_at: datetime | None
    activated_at: datetime | None
    blocked_at: datetime | None
    offboarded_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PlatformStaffAccountList(BaseModel):
    items: list[PlatformStaffAccountRead]
    total: int = Field(ge=0)


class PlatformStaffInvitationRead(PlatformStaffAccountRead):
    activation_token: str | None = None


class PlatformStaffActivationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=32, max_length=512)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("password must not start or end with whitespace")
        if not any(character.islower() for character in value):
            raise ValueError("password must contain a lowercase letter")
        if not any(character.isupper() for character in value):
            raise ValueError("password must contain an uppercase letter")
        if not any(character.isdigit() for character in value):
            raise ValueError("password must contain a digit")
        return value


class PlatformStaffActivationRead(BaseModel):
    status: Literal["activated"] = "activated"
