"""FastAPI endpoints for the onboarding domain."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_permission
from app.core.errors import BusinessRuleError
from app.domains.onboarding.repository import OnboardingRepository
from app.domains.onboarding.schemas import (
    ChecklistRead,
    StartTrialResponse,
    WizardStateRead,
    WizardStepSubmit,
)
from app.domains.onboarding.service import OnboardingService

router = APIRouter(prefix="/api/v1/onboarding", tags=["onboarding"])


async def _service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> OnboardingService:
    return OnboardingService(OnboardingRepository(db))


def _tenant_or_400(user: CurrentUser):  # type: ignore[no-untyped-def]
    if user.tenant_id is None:
        raise BusinessRuleError("Request is not scoped to a tenant")
    return user.tenant_id


@router.get("/wizard", response_model=WizardStateRead)
async def get_wizard(
    user: Annotated[CurrentUser, Depends(require_permission("settings.update"))],
    service: Annotated[OnboardingService, Depends(_service)],
) -> WizardStateRead:
    wizard = await service.get_wizard(_tenant_or_400(user))
    return WizardStateRead.model_validate(wizard)


@router.post("/wizard/step/{step}", response_model=WizardStateRead)
async def submit_step(
    step: int,
    payload: WizardStepSubmit,
    user: Annotated[CurrentUser, Depends(require_permission("settings.update"))],
    service: Annotated[OnboardingService, Depends(_service)],
) -> WizardStateRead:
    wizard = await service.submit_step(tenant_id=_tenant_or_400(user), step=step, data=payload.data)
    return WizardStateRead.model_validate(wizard)


@router.get("/checklist", response_model=ChecklistRead)
async def get_checklist(
    user: Annotated[CurrentUser, Depends(require_permission("settings.update"))],
    service: Annotated[OnboardingService, Depends(_service)],
) -> ChecklistRead:
    checklist = await service.get_checklist(_tenant_or_400(user))
    return ChecklistRead.model_validate(checklist)


@router.post(
    "/start-trial",
    response_model=StartTrialResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_trial(
    user: Annotated[CurrentUser, Depends(require_permission("settings.update"))],
    service: Annotated[OnboardingService, Depends(_service)],
) -> StartTrialResponse:
    tenant, subscription = await service.start_trial(tenant_id=_tenant_or_400(user))
    return StartTrialResponse(
        tenant_id=tenant.id,
        status=tenant.status,
        trial_started_at=tenant.trial_started_at,
        trial_ends_at=tenant.trial_ends_at,
        subscription_id=subscription.id,
    )
