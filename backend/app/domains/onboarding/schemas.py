"""Pydantic schemas for the onboarding domain."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WizardStateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tenant_id: UUID
    current_step: int
    steps_completed: list[int]
    wizard_data: dict[str, object]
    is_completed: bool
    started_at: datetime
    completed_at: datetime | None
    updated_at: datetime


class WizardStepSubmit(BaseModel):
    """Free-form payload — wizard_data["step_N"] = payload."""

    model_config = ConfigDict(extra="forbid")

    data: dict[str, object] = Field(default_factory=dict)


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


class StartTrialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID


ReadinessStepCode = Literal[
    "pharmacy_profile",
    "licensed_branch",
    "receipt_details",
    "tenant_owner",
    "catalog",
    "pos_settings",
    "regulatory",
    "ready",
]
ReadinessTaskCode = Literal[
    "catalog_loaded",
    "first_incoming",
    "first_sale",
    "second_user",
    "shift_opened",
    "test_receipt_printed",
]


class ReadinessStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: ReadinessStepCode
    is_complete: bool
    required: bool
    current: int | None
    target: int | None
    action_hint: (
        Literal[
            "register_missing",
            "payment_methods_missing",
            "operational_branch_missing",
        ]
        | None
    ) = None


class ReadinessTaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: ReadinessTaskCode
    is_complete: bool


class OnboardingOverviewRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    tenant_id: UUID
    tenant_name: str
    tenant_status: Literal[
        "setup",
        "trial",
        "active",
        "grace_period",
        "readonly",
        "archived",
    ]
    setup_ends_at: datetime
    trial_started_at: datetime | None
    trial_ends_at: datetime | None
    subscription_id: UUID | None
    steps: list[ReadinessStepRead]
    recommended_tasks: list[ReadinessTaskRead]
    required_completed: int
    required_total: int
    recommended_completed: int
    recommended_total: int
    is_ready: bool
    can_start_trial: bool
    blocker_codes: list[ReadinessStepCode]
