"""FastAPI endpoints for the audit domain."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_permission
from app.core.errors import BusinessRuleError
from app.domains.audit.models import AuditLog
from app.domains.audit.repository import AuditRepository
from app.domains.audit.schemas import AuditEntry, AuditPage
from app.domains.audit.service import AuditService

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


async def _service(
    db: Annotated[AsyncSession, Depends(get_db, scope="function")],
) -> AuditService:
    return AuditService(AuditRepository(db))


def _current_tenant_or_400(user: CurrentUser) -> UUID:
    if user.tenant_id is None:
        raise BusinessRuleError("Request is not scoped to a tenant")
    return user.tenant_id


def _to_page(
    items: Sequence[AuditLog],
    total: int,
    page: int,
    page_size: int,
    service: AuditService,
) -> AuditPage:
    return AuditPage(
        items=[AuditEntry.model_validate(service.scrub(it)) for it in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/my", response_model=AuditPage)
async def my_audit(
    user: Annotated[CurrentUser, Depends(require_permission("audit.view.own"))],
    service: Annotated[AuditService, Depends(_service)],
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
    action: Annotated[str | None, Query()] = None,
    table_name: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPage:
    items, total = await service.search(
        tenant_id=_current_tenant_or_400(user),
        user_id=user.user_id,
        action=action,
        table_name=table_name,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )
    return _to_page(items, total, page, page_size, service)


@router.get("/tenant", response_model=AuditPage)
async def tenant_audit(
    user: Annotated[CurrentUser, Depends(require_permission("audit.view.tenant"))],
    service: Annotated[AuditService, Depends(_service)],
    user_id: Annotated[UUID | None, Query()] = None,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
    action: Annotated[str | None, Query()] = None,
    table_name: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPage:
    items, total = await service.search(
        tenant_id=_current_tenant_or_400(user),
        user_id=user_id,
        action=action,
        table_name=table_name,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
    )
    return _to_page(items, total, page, page_size, service)


@router.get("/global", response_model=AuditPage)
async def global_audit(
    _user: Annotated[CurrentUser, Depends(require_permission("audit.view.global"))],
    service: Annotated[AuditService, Depends(_service)],
    tenant_id: Annotated[UUID | None, Query()] = None,
    user_id: Annotated[UUID | None, Query()] = None,
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
    action: Annotated[str | None, Query()] = None,
    table_name: Annotated[str | None, Query()] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPage:
    items, total = await service.search(
        tenant_id=tenant_id,
        user_id=user_id,
        action=action,
        table_name=table_name,
        date_from=date_from,
        date_to=date_to,
        page=page,
        page_size=page_size,
        global_scope=True,
    )
    return _to_page(items, total, page, page_size, service)


@router.get("/record/{table_name}/{record_id}", response_model=AuditPage)
async def record_audit(
    table_name: str,
    record_id: UUID,
    user: Annotated[CurrentUser, Depends(require_permission("audit.view.tenant"))],
    service: Annotated[AuditService, Depends(_service)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AuditPage:
    items, total = await service.search(
        tenant_id=_current_tenant_or_400(user),
        table_name=table_name,
        record_id=record_id,
        page=page,
        page_size=page_size,
    )
    return _to_page(items, total, page, page_size, service)
