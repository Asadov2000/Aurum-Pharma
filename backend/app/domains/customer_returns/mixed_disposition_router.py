"""Scoped mixed quarantine decisions and immutable, cost-free history."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_branch_permission, require_writable_tenant
from app.core.errors import PermissionDeniedError
from app.domains.customer_returns.mixed_disposition import MixedDispositionRequest
from app.domains.customer_returns.mixed_disposition_repository import MixedDispositionRepository
from app.domains.customer_returns.mixed_disposition_schemas import (
    MixedDispositionHistory,
    ResolvedMixedDisposition,
)
from app.domains.customer_returns.mixed_disposition_service import MixedDispositionService

router = APIRouter(prefix="/mixed/dispositions")


async def _service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> MixedDispositionService:
    return MixedDispositionService(MixedDispositionRepository(db))


def _tenant_id(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise PermissionDeniedError("Tenant context is required")
    return user.tenant_id


@router.post(
    "",
    response_model=ResolvedMixedDisposition,
    dependencies=[Depends(require_writable_tenant)],
)
async def resolve_mixed_customer_return(
    payload: MixedDispositionRequest,
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("customer_returns.resolve", policy="resource")),
    ],
    service: Annotated[MixedDispositionService, Depends(_service)],
) -> ResolvedMixedDisposition:
    response.headers["Cache-Control"] = "private, no-store"
    # The DB writer independently checks the current actor and the source branch,
    # including every replay; queue/view permission is deliberately not required.
    return await service.resolve(tenant_id=_tenant_id(user), request=payload)


@router.get("", response_model=MixedDispositionHistory)
async def list_mixed_dispositions(
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_branch_permission("customer_returns.view", policy="filter")),
    ],
    service: Annotated[MixedDispositionService, Depends(_service)],
    branch_id: Annotated[UUID | None, Query()] = None,
    page: Annotated[int, Query(ge=1, le=2147483647)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> MixedDispositionHistory:
    response.headers["Cache-Control"] = "private, no-store"
    return await service.list_history(
        tenant_id=_tenant_id(user),
        branch_id=branch_id,
        allowed_branch_ids=user.branch_scope_for("customer_returns.view"),
        page=page,
        page_size=page_size,
    )
