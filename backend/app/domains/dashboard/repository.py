"""DB access for the dashboard summary — raw aggregate queries.

All queries are tenant-scoped explicitly (the app pool also enforces RLS,
but passing :tid keeps the intent obvious and lets support-pool callers
work the same way). The expiry CASE mirrors v_batch_with_expiry_status
(1/3/6 month thresholds) so the colours match the /batches page exactly.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import local_day_range


class DashboardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def today_sales(self, tenant_id: UUID, *, tz: str = "Asia/Dushanbe") -> dict[str, Any]:
        # Gross sales for the local "today", matching the sales-summary report:
        # a forward sale counts once it has completed_at (so a sale fully
        # refunded the same day still stays in gross); test sales excluded;
        # the day boundary is the tenant timezone, not UTC. Sargable half-open
        # range on completed_at uses ix_sale_tenant(tenant_id, completed_at).
        today_local = datetime.now(ZoneInfo(tz)).date()
        start, end = local_day_range(today_local, tz)
        row = (
            (
                await self.session.execute(
                    text(
                        "SELECT COALESCE(SUM(total_amount), 0) AS revenue, "
                        "COUNT(*) AS receipts, "
                        "COALESCE(MAX(currency), 'TJS') AS currency "
                        "FROM sale "
                        "WHERE tenant_id = :tid AND sale_type = 'sale' "
                        "AND is_test = false "
                        "AND completed_at >= :start AND completed_at < :end"
                    ),
                    {"tid": str(tenant_id), "start": start, "end": end},
                )
            )
            .mappings()
            .one()
        )
        return dict(row)

    async def active_shifts(self, tenant_id: UUID) -> dict[str, Any]:
        row = (
            (
                await self.session.execute(
                    text(
                        "SELECT COUNT(*) AS active_shifts, "
                        "COUNT(DISTINCT opened_by_user_id) AS cashiers "
                        "FROM shift WHERE tenant_id = :tid AND status = 'open'"
                    ),
                    {"tid": str(tenant_id)},
                )
            )
            .mappings()
            .one()
        )
        return dict(row)

    async def expiring_batches(self, tenant_id: UUID, *, limit: int = 5) -> list[dict[str, Any]]:
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT id, batch_number, branch_id, expires_at, "
                        "(expires_at - CURRENT_DATE) AS days_to_expiry, "
                        "CASE "
                        "WHEN expires_at <= CURRENT_DATE THEN 'expired' "
                        "WHEN expires_at <= CURRENT_DATE + INTERVAL '1 month' THEN 'red' "
                        "WHEN expires_at <= CURRENT_DATE + INTERVAL '3 months' THEN 'orange' "
                        "WHEN expires_at <= CURRENT_DATE + INTERVAL '6 months' THEN 'yellow' "
                        "ELSE 'normal' END AS expiry_status, "
                        "qty_remaining "
                        "FROM batch "
                        "WHERE tenant_id = :tid AND is_blocked = false "
                        "AND qty_remaining > 0 "
                        "AND expires_at <= CURRENT_DATE + INTERVAL '6 months' "
                        "ORDER BY expires_at ASC LIMIT :lim"
                    ),
                    {"tid": str(tenant_id), "lim": limit},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def expiring_licenses(
        self, tenant_id: UUID, *, within_days: int = 30
    ) -> list[dict[str, Any]]:
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT id AS branch_id, name AS branch_name, license_expires_at, "
                        "(license_expires_at - CURRENT_DATE) AS days_left "
                        "FROM branch "
                        "WHERE tenant_id = :tid AND is_active = true "
                        "AND license_expires_at IS NOT NULL "
                        "AND license_expires_at <= CURRENT_DATE + make_interval(days => :wd) "
                        "ORDER BY license_expires_at ASC"
                    ),
                    {"tid": str(tenant_id), "wd": within_days},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def current_subscription(self, tenant_id: UUID) -> dict[str, Any] | None:
        row = (
            (
                await self.session.execute(
                    text(
                        "SELECT status, period_end FROM tenant_subscription "
                        "WHERE tenant_id = :tid AND status <> 'archived' "
                        "ORDER BY created_at DESC LIMIT 1"
                    ),
                    {"tid": str(tenant_id)},
                )
            )
            .mappings()
            .first()
        )
        return dict(row) if row is not None else None

    async def open_invoices(self, tenant_id: UUID) -> dict[str, Any]:
        row = (
            (
                await self.session.execute(
                    text(
                        "SELECT COUNT(*) AS cnt, "
                        "COALESCE(SUM(amount - discount_amount), 0) AS total, "
                        "COALESCE(MAX(currency), 'TJS') AS currency, "
                        "COUNT(*) FILTER ("
                        "  WHERE status = 'overdue' "
                        "  OR (status = 'pending' AND due_at < now())"
                        ") AS overdue_cnt "
                        "FROM invoice "
                        "WHERE tenant_id = :tid AND status IN ('pending', 'overdue')"
                    ),
                    {"tid": str(tenant_id)},
                )
            )
            .mappings()
            .one()
        )
        return dict(row)

    async def draft_incoming_count(self, tenant_id: UUID) -> int:
        return int(
            (
                await self.session.execute(
                    text(
                        "SELECT COUNT(*) FROM incoming_document "
                        "WHERE tenant_id = :tid AND status = 'draft'"
                    ),
                    {"tid": str(tenant_id)},
                )
            ).scalar_one()
        )

    async def closed_shifts(self, tenant_id: UUID) -> dict[str, Any]:
        row = (
            (
                await self.session.execute(
                    text(
                        "SELECT COUNT(*) AS cnt, "
                        "(SELECT id FROM shift WHERE tenant_id = :tid AND status = 'closed' "
                        " ORDER BY closed_at DESC NULLS LAST LIMIT 1) AS latest_id "
                        "FROM shift WHERE tenant_id = :tid AND status = 'closed'"
                    ),
                    {"tid": str(tenant_id)},
                )
            )
            .mappings()
            .one()
        )
        return dict(row)


# A tiny helper so the service can coerce NUMERIC sums (asyncpg returns
# Decimal already, but COUNT/SUM over an empty set can surface as int 0).
def as_decimal(v: Any) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v))
