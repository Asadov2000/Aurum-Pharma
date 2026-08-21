"""FastAPI endpoints for the onboarding domain."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_permission
from app.core.errors import BusinessRuleError, PermissionDeniedError
from app.domains.onboarding.repository import OnboardingRepository
from app.domains.onboarding.schemas import (
    ChecklistRead,
    OnboardingOverviewRead,
    StartTrialRequest,
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


def _tenant_or_400(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise BusinessRuleError("Request is not scoped to a tenant")
    return user.tenant_id


@router.get("/overview", response_model=OnboardingOverviewRead)
async def get_overview(
    user: Annotated[CurrentUser, Depends(require_permission("settings.update"))],
    service: Annotated[OnboardingService, Depends(_service)],
) -> OnboardingOverviewRead:
    overview = await service.get_overview(_tenant_or_400(user))
    return OnboardingOverviewRead.model_validate(overview)


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
    payload: StartTrialRequest,
    user: Annotated[CurrentUser, Depends(require_permission("settings.update"))],
    service: Annotated[OnboardingService, Depends(_service)],
) -> StartTrialResponse:
    if not user.is_tenant_owner:
        raise PermissionDeniedError("Only an active pharmacy owner can start the trial")
    if user.session_id is None:
        raise PermissionDeniedError("An authenticated owner session is required")
    result = await service.start_trial(
        tenant_id=_tenant_or_400(user),
        source="manual",
        operation_id=payload.operation_id,
        actor_user_id=user.user_id,
        actor_session_id=user.session_id,
    )
    return StartTrialResponse(
        tenant_id=result.tenant_id,
        status=result.status,
        trial_started_at=result.trial_started_at,
        trial_ends_at=result.trial_ends_at,
        subscription_id=result.subscription_id,
    )
