"""Business logic for the billing domain.

Subscription life cycle:
- A new tenant gets a 'trial' subscription (created externally by support
  or by `create_subscription` here). When period_end passes, the daily
  `process_trial_endings` task flips it to 'grace_period'.
- After +7 grace days, `process_grace_endings` moves the subscription to
  'suspended' and the tenant to 'readonly'.
- `generate_monthly_invoices` creates one invoice per active subscription
  whose period_end is within the next 7 days, if no invoice exists yet.
- A payment whose total reaches the invoice amount flips the invoice to
  'paid' and (if the subscription was past period_end) extends the
  subscription to a fresh period.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.core.errors import (
    AurumError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.time import utc_now
from app.domains.billing.models import (
    Invoice,
    Payment,
    SubscriptionPlan,
    TenantSubscription,
)
from app.domains.billing.repository import (
    BillingRepository,
    PlatformBillingOverview,
    PlatformInvoiceRecord,
    PlatformPricingCommandRecord,
    PlatformPricingPlanRecord,
)
from app.domains.foundation.models import Branch, Tenant

logger = structlog.get_logger("billing.service")

GRACE_DAYS = 7


def _pricing_error(exc: DBAPIError) -> AurumError:
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "42501":
        return PermissionDeniedError("Billing pricing operation is not allowed")
    if sqlstate == "P0002":
        return NotFoundError("Billing plan or price version not found")
    if sqlstate in {"22001", "22023", "23502", "23514", "P0001"}:
        return BusinessRuleError("Billing pricing request is invalid")
    if sqlstate in {"23503", "23505", "40001", "40P01", "55000"}:
        return ConflictError("Billing pricing state changed; refresh and retry")
    logger.error("billing_pricing_database_guard_failed", sqlstate=sqlstate)
    return AurumError("Billing pricing database guard failed")


def _pricing_request_hash(action: str, payload: dict[str, object]) -> str:
    def _json_default(value: object) -> str:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Decimal):
            return format(value, "f")
        if isinstance(value, UUID):
            return str(value)
        raise TypeError(f"Unsupported pricing hash value: {type(value).__name__}")

    canonical = json.dumps(
        {"action": action, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


class BillingService:
    def __init__(self, repo: BillingRepository) -> None:
        self.repo = repo

    # -------- plans --------

    async def list_plans(self) -> list[SubscriptionPlan]:
        return await self.repo.list_plans()

    async def list_platform_pricing_plans(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        page: int,
        page_size: int,
    ) -> tuple[list[PlatformPricingPlanRecord], int]:
        try:
            return await self.repo.list_platform_pricing_plans(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                page=page,
                page_size=page_size,
            )
        except DBAPIError as exc:
            raise _pricing_error(exc) from exc

    async def create_platform_pricing_plan(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        code: str,
        name: str,
        description: str | None,
    ) -> PlatformPricingCommandRecord:
        payload: dict[str, object] = {
            "code": code,
            "name": name.strip(),
            "description": description.strip() if description else None,
        }
        try:
            return await self.repo.create_platform_pricing_plan(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                operation_id=operation_id,
                request_hash=_pricing_request_hash("plan_created", payload),
                code=code,
                name=name,
                description=description,
            )
        except DBAPIError as exc:
            raise _pricing_error(exc) from exc

    async def create_platform_pricing_price(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        plan_id: UUID,
        monthly_price_per_branch: Decimal,
        annual_discount_pct: Decimal,
        audience: str,
        notice_days: int,
        change_reason: str,
        terms_snapshot: dict[str, object],
    ) -> PlatformPricingCommandRecord:
        payload: dict[str, object] = {
            "plan_id": plan_id,
            "monthly_price_per_branch": monthly_price_per_branch,
            "annual_discount_pct": annual_discount_pct,
            "audience": audience,
            "notice_days": notice_days,
            "change_reason": change_reason.strip(),
            "terms_snapshot": terms_snapshot,
        }
        try:
            return await self.repo.create_platform_pricing_price(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                operation_id=operation_id,
                request_hash=_pricing_request_hash("price_draft_created", payload),
                plan_id=plan_id,
                monthly_price_per_branch=monthly_price_per_branch,
                annual_discount_pct=annual_discount_pct,
                audience=audience,
                notice_days=notice_days,
                change_reason=change_reason,
                terms_snapshot=terms_snapshot,
            )
        except DBAPIError as exc:
            raise _pricing_error(exc) from exc

    async def transition_platform_pricing_price(
        self,
        *,
        action: str,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        price_version_id: UUID,
        expected_row_version: int,
        effective_from: datetime | None = None,
        reason_code: str | None = None,
        reason: str | None = None,
    ) -> PlatformPricingCommandRecord:
        payload: dict[str, object] = {
            "price_version_id": price_version_id,
            "expected_row_version": expected_row_version,
        }
        parameters: dict[str, object] = {
            "actor_user_id": actor_user_id,
            "actor_session_id": actor_session_id,
            "operation_id": operation_id,
            "price_version_id": price_version_id,
            "expected_row_version": expected_row_version,
        }
        if action == "price_scheduled":
            payload["effective_from"] = effective_from
            parameters["effective_from"] = effective_from
            command = self.repo.schedule_platform_pricing_price
        elif action == "price_activated":
            command = self.repo.activate_platform_pricing_price
        elif action == "price_cancelled":
            payload.update(
                {
                    "reason_code": reason_code,
                    "reason": reason.strip() if reason else None,
                }
            )
            parameters.update({"reason_code": reason_code, "reason": reason})
            command = self.repo.cancel_platform_pricing_price
        else:
            raise ValueError("Unsupported billing pricing transition")
        parameters["request_hash"] = _pricing_request_hash(action, payload)
        try:
            return await command(**parameters)
        except DBAPIError as exc:
            raise _pricing_error(exc) from exc

    # -------- subscriptions --------

    async def get_active_subscription(self, tenant_id: UUID) -> dict[str, object] | None:
        return await self.repo.get_active_subscription(tenant_id)

    async def create_subscription(
        self,
        *,
        tenant_id: UUID,
        plan_id: UUID,
        billing_period: str,
        branches_count: int,
        status: str = "trial",
    ) -> TenantSubscription:
        plan = await self.repo.get_plan(plan_id)
        if plan is None or not plan.is_active:
            raise NotFoundError("Subscription plan not found")
        now = utc_now()
        if billing_period == "yearly":
            period_end = now + timedelta(days=365)
            multiplier = Decimal("12") * (Decimal("1") - plan.annual_discount_pct / Decimal("100"))
        else:
            period_end = now + timedelta(days=30)
            multiplier = Decimal("1")

        amount = (plan.price_per_branch * branches_count * multiplier).quantize(Decimal("0.01"))

        return await self.repo.insert_subscription(
            tenant_id=tenant_id,
            plan_id=plan_id,
            status=status,
            billing_period=billing_period,
            period_start=now,
            period_end=period_end,
            branches_count=branches_count,
            amount=amount,
        )

    async def cancel_subscription(self, subscription_id: UUID) -> TenantSubscription:
        sub = await self.repo.get_subscription(subscription_id)
        if sub is None:
            raise NotFoundError("Subscription not found")
        if sub.status in ("cancelled", "archived"):
            raise BusinessRuleError("Subscription is already cancelled")
        return await self.repo.update_subscription(sub, status="cancelled", cancelled_at=utc_now())

    # -------- invoices --------

    async def list_invoices(self, tenant_id: UUID) -> list[Invoice]:
        return await self.repo.list_invoices_for_tenant(tenant_id)

    async def get_invoice(self, invoice_id: UUID) -> Invoice:
        inv = await self.repo.get_invoice(invoice_id)
        if inv is None:
            raise NotFoundError("Invoice not found")
        return inv

    async def get_invoice_with_payments(self, invoice_id: UUID) -> tuple[Invoice, list[Payment]]:
        inv = await self.get_invoice(invoice_id)
        payments = await self.repo.list_payments_for_invoice(inv.id)
        return inv, payments

    async def create_invoice(
        self,
        *,
        tenant_id: UUID,
        subscription_id: UUID,
        amount: Decimal,
        due_in_days: int = 7,
        notes: str | None = None,
        discount_amount: Decimal = Decimal("0"),
        discount_reason: str | None = None,
    ) -> Invoice:
        sub = await self.repo.get_subscription(subscription_id)
        if sub is None or sub.tenant_id != tenant_id:
            raise NotFoundError("Subscription not found")
        now = utc_now()
        return await self.repo.insert_invoice(
            tenant_id=tenant_id,
            subscription_id=subscription_id,
            invoice_number=await self.repo.next_invoice_number(),
            issued_at=now,
            due_at=now + timedelta(days=due_in_days),
            amount=amount - discount_amount,
            discount_amount=discount_amount,
            discount_reason=discount_reason,
            notes=notes,
        )

    # -------- payments --------

    async def record_payment(
        self,
        *,
        tenant_id: UUID,
        invoice_id: UUID,
        amount: Decimal,
        paid_at: datetime,
        method: str,
        reference: str | None,
        notes: str | None,
        recorded_by: UUID | None,
    ) -> Payment:
        inv = await self.repo.get_invoice(invoice_id)
        if inv is None or inv.tenant_id != tenant_id:
            raise NotFoundError("Invoice not found")
        if inv.status in ("paid", "cancelled"):
            raise BusinessRuleError("Invoice is not payable", details={"status": inv.status})

        payment = await self.repo.insert_payment(
            tenant_id=tenant_id,
            invoice_id=invoice_id,
            amount=amount,
            method=method,
            reference=reference,
            paid_at=paid_at,
            recorded_by=recorded_by,
            notes=notes,
        )

        paid_so_far = await self.repo.sum_payments(invoice_id)
        if paid_so_far >= inv.amount:
            await self.repo.update_invoice(inv, status="paid", paid_at=paid_at)
            # Extend subscription on full payment.
            sub = await self.repo.get_subscription(inv.subscription_id)
            if sub is not None:
                await self._extend_subscription(sub)
        return payment

    async def get_platform_overview(self) -> PlatformBillingOverview:
        return await self.repo.get_platform_overview(now=utc_now())

    async def list_platform_invoices(
        self,
        *,
        query: str | None,
        status: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[PlatformInvoiceRecord], int]:
        return await self.repo.list_platform_invoices(
            now=utc_now(),
            query=query,
            status=status,
            page=page,
            page_size=page_size,
        )

    async def _extend_subscription(self, sub: TenantSubscription) -> None:
        """Bump period_end by another billing cycle when an invoice is paid."""
        increment = timedelta(days=365) if sub.billing_period == "yearly" else timedelta(days=30)
        new_end = max(sub.period_end, utc_now()) + increment
        await self.repo.update_subscription(
            sub,
            status="active",
            period_start=sub.period_end,
            period_end=new_end,
        )

    # -------- transitions invoked by Celery --------

    async def process_trial_endings(self) -> int:
        now = utc_now()
        subs = await self.repo.list_subscriptions_with_period_end_before(status="trial", cutoff=now)
        for sub in subs:
            await self.repo.update_subscription(sub, status="grace_period")
            logger.info("trial_to_grace", subscription_id=str(sub.id))
        return len(subs)

    async def process_grace_endings(self) -> int:
        """grace_period whose period_end + 7d < now → suspended,
        and tenant.status='readonly'."""
        now = utc_now()
        cutoff = now - timedelta(days=GRACE_DAYS)
        subs = await self.repo.list_subscriptions_with_period_end_before(
            status="grace_period", cutoff=cutoff
        )
        for sub in subs:
            await self.repo.update_subscription(sub, status="suspended")
            tenant = await self.repo.session.get(Tenant, sub.tenant_id)
            if tenant is not None:
                tenant.status = "readonly"
                await self.repo.session.flush()
            logger.info("grace_to_suspended", subscription_id=str(sub.id))
        return len(subs)

    async def generate_monthly_invoices(self) -> int:
        """Create one invoice per active subscription whose period_end is
        within the next 7 days, only if no invoice for that subscription
        exists yet."""
        cutoff = utc_now() + timedelta(days=7)
        subs = await self.repo.list_subscriptions_with_period_end_before(
            status="active", cutoff=cutoff
        )
        issued = 0
        for sub in subs:
            if await self.repo.invoice_exists_for_subscription(sub.id):
                continue
            now = utc_now()
            await self.repo.insert_invoice(
                tenant_id=sub.tenant_id,
                subscription_id=sub.id,
                invoice_number=await self.repo.next_invoice_number(),
                issued_at=now,
                due_at=now + timedelta(days=7),
                amount=sub.amount,
            )
            issued += 1
        logger.info("generate_monthly_invoices", issued=issued)
        return issued

    # -------- branch-count recalculation hook --------

    async def recalculate_on_branch_change(self, tenant_id: UUID) -> None:
        """Recount active branches and update the active subscription's
        branches_count + amount. Pro-rata adjustment for the current period
        is not implemented in phase 1 — change applies to the next period."""
        sub_row = await self.repo.get_active_subscription(tenant_id)
        if sub_row is None:
            return
        sub = await self.repo.get_subscription(sub_row["id"])
        if sub is None:
            return
        # Count active branches via direct query (cross-domain).
        stmt = select(Branch).where(Branch.tenant_id == tenant_id, Branch.is_active.is_(True))
        branches = (await self.repo.session.execute(stmt)).scalars().all()
        plan = await self.repo.get_plan(sub.plan_id)
        if plan is None:
            return
        new_count = len(branches)
        if sub.billing_period == "yearly":
            multiplier = Decimal("12") * (Decimal("1") - plan.annual_discount_pct / Decimal("100"))
        else:
            multiplier = Decimal("1")
        new_amount = (plan.price_per_branch * new_count * multiplier).quantize(Decimal("0.01"))
        await self.repo.update_subscription(sub, branches_count=new_count, amount=new_amount)
        logger.info(
            "subscription_recalculated",
            tenant_id=str(tenant_id),
            branches=new_count,
            amount=str(new_amount),
        )
