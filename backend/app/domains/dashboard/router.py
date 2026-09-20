"""FastAPI endpoint for the dashboard summary."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, get_redis, require_tenant_permission
from app.core.errors import BusinessRuleError
from app.domains.dashboard.repository import DashboardRepository
from app.domains.dashboard.schemas import DashboardSummary
from app.domains.dashboard.service import DashboardService

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])


async def _service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
    redis: Annotated[Redis, Depends(get_redis)],
) -> DashboardService:
    return DashboardService(DashboardRepository(db), redis=redis)


def _current_tenant_or_400(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise BusinessRuleError(
            "Request is not scoped to a tenant",
            details={"hint": "Login as a tenant user or pass X-Tenant-Id (phase 2)."},
        )
    return user.tenant_id


@router.get("/summary", response_model=DashboardSummary)
async def get_summary(
    response: Response,
    user: Annotated[
        CurrentUser,
        Depends(require_tenant_permission("reports.view")),
    ],
    service: Annotated[DashboardService, Depends(_service)],
    refresh: Annotated[bool, Query()] = False,
) -> DashboardSummary:
    # reports.view (owner/admin/dev) — keeps owner-level financials off the
    # seller's screen. Tenant scoping still comes from the token, not a param.
    response.headers["Cache-Control"] = "private, no-store"
    tenant_id = _current_tenant_or_400(user)
    summary = await service.get_summary(tenant_id, force_refresh=refresh)
    # Shared cache contains operational data only. Financial access is checked
    # afresh and the protected ledger projection is never stored in that cache.
    finance = None
    if (
        not user.is_developer
        and not user.is_administrator
        and user.support_access_session_id is None
        and user.has_tenant_scope("billing.overview.view")
        and user.has_tenant_scope("billing.invoice.view")
    ):
        finance = await service.get_finance(actor_user_id=user.user_id, tenant_id=tenant_id)
    return summary.model_copy(update={"finance": finance})
