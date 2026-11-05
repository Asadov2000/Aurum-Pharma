"""Internal prepared-stock valuation on a local date; no financial sales report."""

from __future__ import annotations

from datetime import date
from uuid import UUID
from zoneinfo import ZoneInfoNotFoundError

from app.core.errors import PermissionDeniedError
from app.core.time import local_day_range
from app.domains.inventory.unit_report_contracts import UnitStockReport
from app.domains.inventory.unit_report_repository import UnitStockReportRepository


class UnitStockReportService:
    def __init__(self, repo: UnitStockReportRepository) -> None:
        self.repo = repo

    async def read_stock_on_date(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
        on_date: date,
        tz: str = "Asia/Dushanbe",
        page: int = 1,
        page_size: int = 50,
    ) -> UnitStockReport:
        if not isinstance(allowed_branch_ids, set) or branch_id not in allowed_branch_ids:
            raise PermissionDeniedError("Branch access denied")
        if type(on_date) is not date or not isinstance(tz, str) or not tz:
            raise ValueError("a local report date and timezone are required")
        try:
            _, cutoff = local_day_range(on_date, tz)
        except (ValueError, OverflowError, ZoneInfoNotFoundError) as error:
            raise ValueError("invalid report date or timezone") from error
        await self.repo.assert_actor(tenant_id, branch_id, actor_id)
        snapshot = await self.repo.fetch_stock_snapshot(
            tenant_id=tenant_id,
            branch_id=branch_id,
            cutoff_exclusive=cutoff,
            page=page,
            page_size=page_size,
        )
        return UnitStockReport(
            summary=snapshot.summary,
            items=snapshot.items,
            tenant_id=tenant_id,
            branch_id=branch_id,
            on_date=on_date,
            timezone=tz,
            cutoff_exclusive=cutoff,
            page=page,
            page_size=page_size,
        )
