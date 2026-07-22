"""Pydantic schemas for the notifications domain."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domains.notifications.events import MANDATORY_EVENT_TYPES

SEVERITIES = {"info", "warning", "error", "critical"}
CHANNELS = {"in_app", "email", "telegram", "sms"}


class NotificationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    user_id: UUID
    event_type: str
    title: str
    body: str | None
    data: dict[str, Any] | None
    severity: str
    read_at: datetime | None
    created_at: datetime


class SubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    event_type: str
    channels: list[str]
    is_enabled: bool


class SubscriptionPatch(BaseModel):
    event_type: str = Field(min_length=1, max_length=100)
    channels: list[str] = Field(default_factory=lambda: ["in_app"])
    is_enabled: bool = True

    @field_validator("channels")
    @classmethod
    def _check_channels(cls, v: list[str]) -> list[str]:
        bad = [c for c in v if c not in CHANNELS]
        if bad:
            raise ValueError(f"unknown channel(s): {bad}; allowed {sorted(CHANNELS)}")
        return v

    @model_validator(mode="after")
    def _protect_mandatory_security_events(self) -> Self:
        if self.event_type in MANDATORY_EVENT_TYPES and (
            not self.is_enabled or "in_app" not in self.channels
        ):
            raise ValueError("mandatory security notifications must remain enabled in-app")
        return self


class SubscriptionsBulkPatch(BaseModel):
    items: list[SubscriptionPatch]
