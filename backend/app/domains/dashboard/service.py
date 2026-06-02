"""Dashboard summary assembly + Redis cache.

The summary is read-heavy and tolerates 60s of staleness, so we cache the
serialized payload under `tenant:{id}:dashboard`. A cache miss runs ~7
small aggregate queries and writes the JSON back with a 60s TTL.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from redis.asyncio import Redis

from app.core.time import utc_now
from app.domains.dashboard.repository import DashboardRepository, as_decimal
from app.domains.dashboard.schemas import (
    ChecklistSection,
    DashboardSummary,
    ExpiringBatch,
    ExpiringLicense,
    ExpiringSection,
    FinanceSection,
    TodaySection,
)
from app.domains.foundation.repository import FoundationRepository

logger = structlog.get_logger("dashboard.service")

CACHE_TTL_SECONDS = 60


def cache_key(tenant_id: UUID) -> str:
    return f"tenant:{tenant_id}:dashboard"


class DashboardService:
    def __init__(self, repo: DashboardRepository, redis: Redis | None = None) -> None:
        self.repo = repo
        self.redis = redis

    async def get_summary(self, tenant_id: UUID) -> DashboardSummary:
        if self.redis is not None:
            cached = await self.redis.get(cache_key(tenant_id))
            if cached:
                try:
                    return DashboardSummary.model_validate_json(cached)
                except ValueError:
                    pass  # corrupt cache → recompute

        summary = await self._compute(tenant_id)

        if self.redis is not None:
            await self.redis.set(
                cache_key(tenant_id),
                summary.model_dump_json(),
                ex=CACHE_TTL_SECONDS,
            )
        return summary

    async def _report_tz(self, tenant_id: UUID) -> str:
        """Tenant's report timezone — the local day boundary for 'today' tiles.
        Falls back to Asia/Dushanbe if settings are somehow missing."""
        settings = await FoundationRepository(self.repo.session).get_settings(tenant_id)
        return settings.report_timezone if settings is not None else "Asia/Dushanbe"

    async def _compute(self, tenant_id: UUID) -> DashboardSummary:
        sales = await self.repo.today_sales(tenant_id, tz=await self._report_tz(tenant_id))
        shifts = await self.repo.active_shifts(tenant_id)
        batches = await self.repo.expiring_batches(tenant_id)
        licenses = await self.repo.expiring_licenses(tenant_id)
        sub = await self.repo.current_subscription(tenant_id)
        invoices = await self.repo.open_invoices(tenant_id)
        draft_incoming = await self.repo.draft_incoming_count(tenant_id)
        closed = await self.repo.closed_shifts(tenant_id)

        return DashboardSummary(
            today=TodaySection(
                revenue=as_decimal(sales["revenue"]),
                currency=sales["currency"],
                receipts=int(sales["receipts"]),
                active_shifts=int(shifts["active_shifts"]),
                cashiers_on_shift=int(shifts["cashiers"]),
            ),
            expiring=ExpiringSection(
                batches=[
                    ExpiringBatch(
                        id=b["id"],
                        batch_number=b["batch_number"],
                        branch_id=b["branch_id"],
                        expires_at=b["expires_at"],
                        days_to_expiry=int(b["days_to_expiry"]),
                        expiry_status=b["expiry_status"],
                        qty_remaining=as_decimal(b["qty_remaining"]),
                    )
                    for b in batches
                ],
                licenses=[
                    ExpiringLicense(
                        branch_id=lic["branch_id"],
                        branch_name=lic["branch_name"],
                        license_expires_at=lic["license_expires_at"],
                        days_left=int(lic["days_left"]),
                    )
                    for lic in licenses
                ],
            ),
            finance=FinanceSection(
                subscription_status=sub["status"] if sub else None,
                subscription_period_end=sub["period_end"] if sub else None,
                open_invoices_count=int(invoices["cnt"]),
                open_invoices_total=as_decimal(invoices["total"]),
                currency=invoices["currency"],
                has_overdue=int(invoices["overdue_cnt"]) > 0,
            ),
            checklist=ChecklistSection(
                draft_incoming_count=draft_incoming,
                closed_shifts_count=int(closed["cnt"]),
                latest_closed_shift_id=closed["latest_id"],
            ),
            generated_at=utc_now(),
        )
