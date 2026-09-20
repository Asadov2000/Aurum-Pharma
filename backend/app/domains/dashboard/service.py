"""Dashboard summary assembly + Redis cache.

The summary is read-heavy and tolerates 60s of staleness, so we cache the
operational payload under `tenant:{id}:dashboard:v2` with a 60s TTL.
Financial data is read live through the caller-authorized ledger projection.
"""

from __future__ import annotations

from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.errors import AurumError
from app.core.time import local_day_range, utc_now
from app.domains.billing.repository import BillingRepository
from app.domains.billing.schemas import TenantBillingFinancialAccountRead
from app.domains.billing.service import BillingService
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
from app.domains.inventory.expiry import build_expiry_boundaries

logger = structlog.get_logger("dashboard.service")

CACHE_TTL_SECONDS = 60


def cache_key(tenant_id: UUID) -> str:
    return f"tenant:{tenant_id}:dashboard:v2"


class DashboardService:
    def __init__(self, repo: DashboardRepository, redis: Redis | None = None) -> None:
        self.repo = repo
        self.redis = redis

    async def get_summary(
        self, tenant_id: UUID, *, force_refresh: bool = False
    ) -> DashboardSummary:
        if self.redis is not None and not force_refresh:
            try:
                cached = await self.redis.get(cache_key(tenant_id))
            except RedisError:
                cached = None
                logger.warning("dashboard_cache_read_failed")
            if cached:
                try:
                    return DashboardSummary.model_validate_json(cached)
                except ValueError:
                    pass  # corrupt cache → recompute

        summary = await self._compute(tenant_id)

        if self.redis is not None:
            try:
                await self.redis.set(
                    cache_key(tenant_id),
                    summary.model_dump_json(),
                    ex=CACHE_TTL_SECONDS,
                )
            except RedisError:
                logger.warning("dashboard_cache_write_failed")
        return summary

    async def get_finance(self, *, actor_user_id: UUID, tenant_id: UUID) -> FinanceSection:
        """Read the protected ledger live; never cache caller-specific finances."""
        account = TenantBillingFinancialAccountRead.model_validate(
            await BillingService(
                BillingRepository(self.repo.session)
            ).read_tenant_financial_account(actor_user_id=actor_user_id, tenant_id=tenant_id)
        )
        open_invoices = [
            invoice
            for invoice in account.invoices
            if invoice.document_state == "issued" and invoice.outstanding_amount > 0
        ]
        return FinanceSection(
            subscription_status=account.subscription.status if account.subscription else None,
            subscription_period_end=(
                account.subscription.period_end if account.subscription else None
            ),
            open_invoices_count=len(open_invoices),
            open_invoices_total=account.outstanding_amount,
            currency=account.currency,
            has_overdue=any(invoice.collection_state == "overdue" for invoice in open_invoices),
        )

    async def _compute(self, tenant_id: UUID) -> DashboardSummary:
        settings = await FoundationRepository(self.repo.session).get_settings(tenant_id)
        timezone_name = settings.report_timezone if settings is not None else "Asia/Dushanbe"
        try:
            today = utc_now().astimezone(ZoneInfo(timezone_name)).date()
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise AurumError("Tenant report timezone is invalid") from exc
        boundaries = build_expiry_boundaries(
            today,
            settings.expiry_thresholds if settings is not None else None,
        )
        sales = await self.repo.today_sales(tenant_id, tz=timezone_name)
        shifts = await self.repo.active_shifts(tenant_id)
        batches = await self.repo.expiring_batches(tenant_id, boundaries=boundaries)
        licenses = await self.repo.expiring_licenses(tenant_id, today=today)
        draft_incoming = await self.repo.draft_incoming_count(tenant_id)
        day_start, day_end = local_day_range(today, timezone_name)
        closed = await self.repo.closed_shifts(
            tenant_id,
            start=day_start,
            end=day_end,
        )

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
            finance=None,
            checklist=ChecklistSection(
                draft_incoming_count=draft_incoming,
                closed_shifts_count=int(closed["cnt"]),
                latest_closed_shift_id=closed["latest_id"],
            ),
            generated_at=utc_now(),
        )
