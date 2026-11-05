"""Tenant-local gross-profit periods without mixing in warehouse losses."""

from datetime import date
from uuid import UUID
from zoneinfo import ZoneInfoNotFoundError

from app.core.errors import BusinessRuleError
from app.core.time import local_day_range
from app.domains.pos.profit_report_repository import ProfitReportRepository
from app.domains.pos.profit_report_schemas import ProfitReportOverview


class ProfitReportService:
    def __init__(self, repo: ProfitReportRepository) -> None:
        self.repo = repo

    async def read(
        self,
        *,
        tenant_id: UUID,
        date_from: date,
        date_to: date,
        branch_id: UUID | None,
        page: int = 1,
        page_size: int = 25,
        export: bool = False,
    ) -> ProfitReportOverview:
        if date_to < date_from or (date_to - date_from).days >= 366:
            raise BusinessRuleError("Gross profit period must be ordered and not exceed 366 days")
        try:
            timezone = await self.repo.timezone(tenant_id)
            start, _ = local_day_range(date_from, timezone)
            _, end = local_day_range(date_to, timezone)
        except (ValueError, OverflowError, ZoneInfoNotFoundError):
            raise BusinessRuleError("Invalid gross profit date or timezone") from None
        snapshot = await self.repo.snapshot(
            tenant_id=tenant_id,
            start=start,
            end=end,
            branch_id=branch_id,
            limit=None if export else page_size,
            offset=0 if export else (page - 1) * page_size,
        )
        return ProfitReportOverview(
            **snapshot.model_dump(),
            date_from=date_from,
            date_to=date_to,
            page=page,
            page_size=page_size,
        )
