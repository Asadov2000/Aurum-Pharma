"""Post a saved closed-pack reconciliation and recover its actor-bound result."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_branch_permission, require_writable_tenant
from app.core.errors import PermissionDeniedError
from app.domains.inventory.unit_legacy_transition_posting import LegacyTransitionPostingService
from app.domains.inventory.unit_legacy_transition_posting_repository import (
    LegacyTransitionPostingRepository,
)
from app.domains.inventory.unit_legacy_transition_schemas import (
    LegacyTransitionPostingRequest,
    LegacyTransitionPostingResult,
)

router = APIRouter()


def _transition_tenant(user: CurrentUser, branch_id: UUID) -> UUID:
    permissions = ("batches.view", "batches.view_costs", "batches.update", "incoming.view")
    if user.tenant_id is None or not all(
        user.can_access_branch(permission, branch_id) for permission in permissions
    ):
        raise PermissionDeniedError("Legacy transition permission denied")
    return user.tenant_id


@router.post(
    "/{batch_id}/unit-transition",
    response_model=LegacyTransitionPostingResult,
    dependencies=[Depends(require_writable_tenant)],
)
async def post_legacy_transition(
    batch_id: UUID,
    payload: LegacyTransitionPostingRequest,
    response: Response,
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("batches.update", policy="resource"))
    ],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> LegacyTransitionPostingResult:
    tenant_id = _transition_tenant(user, payload.branch_id)
    response.headers["Cache-Control"] = "private, no-store"
    return await LegacyTransitionPostingService(LegacyTransitionPostingRepository(db)).post(
        tenant_id=tenant_id,
        branch_id=payload.branch_id,
        batch_id=batch_id,
        reconciliation_id=payload.reconciliation_id,
        command_id=payload.command_id,
        actor_id=user.user_id,
    )


@router.get(
    "/{batch_id}/unit-transition/{command_id}",
    response_model=LegacyTransitionPostingResult,
)
async def read_legacy_transition(
    batch_id: UUID,
    command_id: UUID,
    branch_id: UUID,
    response: Response,
    user: Annotated[
        CurrentUser, Depends(require_branch_permission("batches.view", policy="resource"))
    ],
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> LegacyTransitionPostingResult:
    tenant_id = _transition_tenant(user, branch_id)
    response.headers["Cache-Control"] = "private, no-store"
    return await LegacyTransitionPostingService(LegacyTransitionPostingRepository(db)).read(
        tenant_id=tenant_id,
        branch_id=branch_id,
        batch_id=batch_id,
        command_id=command_id,
        actor_id=user.user_id,
    )
