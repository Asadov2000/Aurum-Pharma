"""Historical quarantine valuation uses the tenant's calendar and saved costs."""

from datetime import date
from uuid import UUID
from zoneinfo import ZoneInfoNotFoundError

from app.core.errors import BusinessRuleError
from app.core.time import local_day_range
from app.domains.pos.quarantine_report_repository import QuarantineReportRepository
from app.domains.pos.quarantine_report_schemas import QuarantineReportOverview


class QuarantineReportService:
    def __init__(self, repo: QuarantineReportRepository) -> None:
        self.repo = repo

    async def read(
        self,
        *,
        tenant_id: UUID,
        on_date: date,
        branch_id: UUID | None,
        page: int = 1,
        page_size: int = 25,
        export: bool = False,
    ) -> QuarantineReportOverview:
        try:
            _, before = local_day_range(on_date, await self.repo.timezone(tenant_id))
        except (ValueError, OverflowError, ZoneInfoNotFoundError):
            raise BusinessRuleError("Invalid quarantine report date or timezone") from None
        snapshot = await self.repo.snapshot(
            tenant_id=tenant_id,
            before=before,
            branch_id=branch_id,
            limit=None if export else page_size,
            offset=0 if export else (page - 1) * page_size,
        )
        return QuarantineReportOverview(
            **snapshot.model_dump(), on_date=on_date, page=page, page_size=page_size
        )
