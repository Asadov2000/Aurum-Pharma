"""Pydantic schemas for the onboarding domain."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WizardStateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tenant_id: UUID
    current_step: int
    steps_completed: list[int]
    wizard_data: dict[str, Any]
    is_completed: bool
    started_at: datetime
    completed_at: datetime | None
    updated_at: datetime


class WizardStepSubmit(BaseModel):
    """Free-form payload — wizard_data["step_N"] = payload."""

    data: dict[str, Any] = Field(default_factory=dict)


class ChecklistRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tenant_id: UUID
    completed_tasks: list[str]
    catalog_items_count: int
    trial_eligible: bool
    trial_started_at: datetime | None
    setup_ends_at: datetime
    updated_at: datetime


class StartTrialResponse(BaseModel):
    tenant_id: UUID
    status: str
    trial_started_at: datetime
    trial_ends_at: datetime
    subscription_id: UUID
