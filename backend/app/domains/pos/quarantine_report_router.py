"""Report-only access to historical quarantine, separate from warehouse actions."""

from datetime import date
from typing import Annotated
from uuid import UUID

import anyio
from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_db, require_branch_permission, require_permission
from app.core.errors import BusinessRuleError, PermissionDeniedError
from app.domains.audit.repository import AuditRepository
from app.domains.audit.service import AuditService
from app.domains.pos.quarantine_report_repository import QuarantineReportRepository
from app.domains.pos.quarantine_report_schemas import QuarantineReportOverview
from app.domains.pos.quarantine_report_service import QuarantineReportService
from app.domains.pos.quarantine_report_xlsx import render_quarantine_report_xlsx

router = APIRouter(prefix="/reports")
ReportUser = Annotated[
    CurrentUser, Depends(require_branch_permission("reports.view", policy="filter"))
]
ReportSession = Annotated[AsyncSession, Depends(get_db, scope="function")]


def _scope(user: CurrentUser, branch_id: UUID | None) -> tuple[UUID, UUID | None]:
    if user.tenant_id is None:
        raise BusinessRuleError("Select a tenant for the report")
    allowed = user.branch_scope_for("reports.view")
    if allowed is not None:
        if branch_id is not None and branch_id not in allowed:
            raise PermissionDeniedError("Branch access denied")
        if branch_id is None:
            if len(allowed) != 1:
                raise BusinessRuleError("branch_id is required for branch-scoped reports")
            branch_id = next(iter(allowed))
    return user.tenant_id, branch_id


@router.get("/quarantine-on-date", response_model=QuarantineReportOverview)
async def quarantine_on_date(
    user: ReportUser,
    db: ReportSession,
    response: Response,
    on_date: Annotated[date, Query(alias="date")],
    branch_id: UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> QuarantineReportOverview:
    tenant_id, branch_id = _scope(user, branch_id)
    response.headers["Cache-Control"] = "private, no-store"
    return await QuarantineReportService(QuarantineReportRepository(db)).read(
        tenant_id=tenant_id,
        on_date=on_date,
        branch_id=branch_id,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/quarantine-on-date.xlsx",
    response_class=Response,
    dependencies=[Depends(require_permission("reports.export"))],
)
async def quarantine_on_date_xlsx(
    user: ReportUser,
    db: ReportSession,
    on_date: Annotated[date, Query(alias="date")],
    branch_id: UUID | None = None,
) -> Response:
    tenant_id, branch_id = _scope(user, branch_id)
    data = await QuarantineReportService(QuarantineReportRepository(db)).read(
        tenant_id=tenant_id, on_date=on_date, branch_id=branch_id, export=True
    )
    content = await anyio.to_thread.run_sync(render_quarantine_report_xlsx, data)
    await AuditService(AuditRepository(db)).log_export(
        tenant_id=tenant_id,
        user_id=user.user_id,
        what="quarantine_on_date",
        metadata={
            "on_date": on_date.isoformat(),
            "branch_id": str(branch_id) if branch_id is not None else None,
            "format": "xlsx",
        },
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="quarantine-{on_date}.xlsx"',
        },
    )
