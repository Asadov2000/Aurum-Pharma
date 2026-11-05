"""Read historical sales profit and warehouse costs without writing business data."""

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
from app.domains.pos.profit_report_repository import ProfitReportRepository
from app.domains.pos.profit_report_schemas import ProfitReportOverview
from app.domains.pos.profit_report_service import ProfitReportService
from app.domains.pos.profit_report_xlsx import render_profit_report_xlsx

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


@router.get("/gross-profit", response_model=ProfitReportOverview)
async def gross_profit(
    user: ReportUser,
    db: ReportSession,
    response: Response,
    date_from: date,
    date_to: date,
    branch_id: UUID | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> ProfitReportOverview:
    tenant_id, branch_id = _scope(user, branch_id)
    response.headers["Cache-Control"] = "private, no-store"
    return await ProfitReportService(ProfitReportRepository(db)).read(
        tenant_id=tenant_id,
        date_from=date_from,
        date_to=date_to,
        branch_id=branch_id,
        page=page,
        page_size=page_size,
    )


@router.get(
    "/gross-profit.xlsx",
    response_class=Response,
    dependencies=[Depends(require_permission("reports.export"))],
)
async def gross_profit_xlsx(
    user: ReportUser,
    db: ReportSession,
    date_from: date,
    date_to: date,
    branch_id: UUID | None = None,
) -> Response:
    tenant_id, branch_id = _scope(user, branch_id)
    data = await ProfitReportService(ProfitReportRepository(db)).read(
        tenant_id=tenant_id,
        date_from=date_from,
        date_to=date_to,
        branch_id=branch_id,
        export=True,
    )
    content = await anyio.to_thread.run_sync(render_profit_report_xlsx, data)
    await AuditService(AuditRepository(db)).log_export(
        tenant_id=tenant_id,
        user_id=user.user_id,
        what="gross_profit",
        metadata={
            "date_from": date_from.isoformat(),
            "date_to": date_to.isoformat(),
            "branch_id": str(branch_id) if branch_id is not None else None,
            "format": "xlsx",
        },
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="profit-{date_from}-{date_to}.xlsx"',
        },
    )
