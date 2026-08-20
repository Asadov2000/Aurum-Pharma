"""Business logic for the billing domain.

Legacy subscriptions and documents remain readable during migration. New
invoices and payments are created only by the immutable financial commands.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

import structlog
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
    BillingWorkerRepository,
    PlatformBillingOverview,
    PlatformBillingTenantRecord,
    PlatformInvoiceRecord,
    PlatformPricingCommandRecord,
    PlatformPricingPlanRecord,
)

logger = structlog.get_logger("billing.service")


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


def _financial_error(exc: DBAPIError) -> AurumError:
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate == "42501":
        return PermissionDeniedError("Billing financial operation is not allowed")
    if sqlstate == "P0002":
        return NotFoundError("Billing financial record not found")
    if sqlstate in {"22001", "22023", "23502", "23514", "P0001"}:
        return BusinessRuleError("Billing financial request is invalid")
    if sqlstate in {"23503", "23505", "40001", "40P01", "55000"}:
        return ConflictError("Billing financial state changed; refresh and retry")
    logger.error("billing_financial_database_guard_failed", sqlstate=sqlstate)
    return AurumError("Billing financial database guard failed")


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

    async def apply_initial_subscription_price(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        tenant_id: UUID,
        subscription_id: UUID,
        expected_row_version: int,
    ) -> PlatformPricingCommandRecord:
        payload: dict[str, object] = {
            "tenant_id": tenant_id,
            "subscription_id": subscription_id,
            "expected_row_version": expected_row_version,
        }
        try:
            return await self.repo.apply_initial_subscription_price(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                operation_id=operation_id,
                request_hash=_pricing_request_hash(
                    "initial_subscription_price_applied",
                    payload,
                ),
                tenant_id=tenant_id,
                subscription_id=subscription_id,
                expected_row_version=expected_row_version,
            )
        except DBAPIError as exc:
            raise _pricing_error(exc) from exc

    async def issue_subscription_invoice(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        tenant_id: UUID,
        subscription_id: UUID,
        expected_row_version: int,
    ) -> PlatformPricingCommandRecord:
        payload: dict[str, object] = {
            "tenant_id": tenant_id,
            "subscription_id": subscription_id,
            "expected_row_version": expected_row_version,
        }
        try:
            return await self.repo.issue_subscription_invoice(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                operation_id=operation_id,
                request_hash=_pricing_request_hash("invoice_issued", payload),
                tenant_id=tenant_id,
                subscription_id=subscription_id,
                expected_row_version=expected_row_version,
            )
        except DBAPIError as exc:
            raise _financial_error(exc) from exc

    async def create_bank_payment_review(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        tenant_id: UUID,
        target_invoice_id: UUID,
        amount: Decimal,
        paid_at: datetime,
        recipient_account_key: str,
        external_reference: str,
    ) -> PlatformPricingCommandRecord:
        payload: dict[str, object] = {
            "tenant_id": tenant_id,
            "target_invoice_id": target_invoice_id,
            "amount": amount,
            "paid_at": paid_at,
            "recipient_account_key": recipient_account_key,
            "external_reference": external_reference,
        }
        try:
            return await self.repo.create_bank_payment_review(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                operation_id=operation_id,
                request_hash=_pricing_request_hash("payment_review_created", payload),
                tenant_id=tenant_id,
                target_invoice_id=target_invoice_id,
                amount=amount,
                paid_at=paid_at,
                recipient_account_key=recipient_account_key,
                external_reference=external_reference,
            )
        except DBAPIError as exc:
            raise _financial_error(exc) from exc

    async def approve_bank_payment(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        tenant_id: UUID,
        review_id: UUID,
        expected_row_version: int,
    ) -> PlatformPricingCommandRecord:
        payload: dict[str, object] = {
            "tenant_id": tenant_id,
            "review_id": review_id,
            "expected_row_version": expected_row_version,
        }
        try:
            return await self.repo.approve_bank_payment(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                operation_id=operation_id,
                request_hash=_pricing_request_hash("payment_approved", payload),
                tenant_id=tenant_id,
                review_id=review_id,
                expected_row_version=expected_row_version,
            )
        except DBAPIError as exc:
            raise _financial_error(exc) from exc

    async def reject_bank_payment_review(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        tenant_id: UUID,
        review_id: UUID,
        expected_row_version: int,
        reason_code: str,
        reason_note: str | None,
    ) -> PlatformPricingCommandRecord:
        payload: dict[str, object] = {
            "tenant_id": tenant_id,
            "review_id": review_id,
            "expected_row_version": expected_row_version,
            "reason_code": reason_code,
            "reason_note": reason_note,
        }
        try:
            return await self.repo.reject_bank_payment_review(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                operation_id=operation_id,
                request_hash=_pricing_request_hash("payment_review_rejected", payload),
                tenant_id=tenant_id,
                review_id=review_id,
                expected_row_version=expected_row_version,
                reason_code=reason_code,
                reason_note=reason_note,
            )
        except DBAPIError as exc:
            raise _financial_error(exc) from exc

    async def create_payment_adjustment(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        tenant_id: UUID,
        payment_id: UUID,
        adjustment_kind: str,
        amount: Decimal,
        reason_code: str,
        reason_note: str,
        refunded_at: datetime | None,
        refund_reference: str | None,
    ) -> PlatformPricingCommandRecord:
        payload: dict[str, object] = {
            "tenant_id": tenant_id,
            "payment_id": payment_id,
            "adjustment_kind": adjustment_kind,
            "amount": amount,
            "reason_code": reason_code,
            "reason_note": reason_note,
            "refunded_at": refunded_at,
            "refund_reference": refund_reference,
        }
        try:
            return await self.repo.create_payment_adjustment(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                operation_id=operation_id,
                request_hash=_pricing_request_hash("payment_adjustment_requested", payload),
                tenant_id=tenant_id,
                payment_id=payment_id,
                adjustment_kind=adjustment_kind,
                amount=amount,
                reason_code=reason_code,
                reason_note=reason_note,
                refunded_at=refunded_at,
                refund_reference=refund_reference,
            )
        except DBAPIError as exc:
            raise _financial_error(exc) from exc

    async def list_payment_adjustments(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        tenant_id: UUID,
        page: int,
        page_size: int,
    ) -> dict[str, object]:
        try:
            return await self.repo.list_payment_adjustments(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                tenant_id=tenant_id,
                page=page,
                page_size=page_size,
            )
        except DBAPIError as exc:
            raise _financial_error(exc) from exc

    async def approve_payment_adjustment(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        tenant_id: UUID,
        adjustment_id: UUID,
        expected_row_version: int,
    ) -> PlatformPricingCommandRecord:
        payload: dict[str, object] = {
            "tenant_id": tenant_id,
            "adjustment_id": adjustment_id,
            "expected_row_version": expected_row_version,
        }
        try:
            return await self.repo.approve_payment_adjustment(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                operation_id=operation_id,
                request_hash=_pricing_request_hash("payment_adjustment_approved", payload),
                tenant_id=tenant_id,
                adjustment_id=adjustment_id,
                expected_row_version=expected_row_version,
            )
        except DBAPIError as exc:
            raise _financial_error(exc) from exc

    async def reject_payment_adjustment(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        tenant_id: UUID,
        adjustment_id: UUID,
        expected_row_version: int,
        reason_code: str,
        reason_note: str | None,
    ) -> PlatformPricingCommandRecord:
        payload: dict[str, object] = {
            "tenant_id": tenant_id,
            "adjustment_id": adjustment_id,
            "expected_row_version": expected_row_version,
            "reason_code": reason_code,
            "reason_note": reason_note,
        }
        try:
            return await self.repo.reject_payment_adjustment(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                operation_id=operation_id,
                request_hash=_pricing_request_hash("payment_adjustment_rejected", payload),
                tenant_id=tenant_id,
                adjustment_id=adjustment_id,
                expected_row_version=expected_row_version,
                reason_code=reason_code,
                reason_note=reason_note,
            )
        except DBAPIError as exc:
            raise _financial_error(exc) from exc

    async def read_platform_financial_account(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        tenant_id: UUID,
    ) -> dict[str, object]:
        try:
            return await self.repo.read_platform_financial_account(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                tenant_id=tenant_id,
            )
        except DBAPIError as exc:
            raise _financial_error(exc) from exc

    async def read_tenant_financial_account(
        self,
        *,
        actor_user_id: UUID,
        tenant_id: UUID,
    ) -> dict[str, object]:
        try:
            return await self.repo.read_tenant_financial_account(
                actor_user_id=actor_user_id,
                tenant_id=tenant_id,
            )
        except DBAPIError as exc:
            raise _financial_error(exc) from exc

    async def create_tenant_payment_submission(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        tenant_id: UUID,
        target_invoice_id: UUID,
        amount: Decimal,
        paid_at: datetime,
        external_reference: str,
    ) -> PlatformPricingCommandRecord:
        payload: dict[str, object] = {
            "tenant_id": tenant_id,
            "target_invoice_id": target_invoice_id,
            "amount": amount,
            "paid_at": paid_at,
            "external_reference": external_reference,
        }
        try:
            return await self.repo.create_tenant_payment_submission(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                operation_id=operation_id,
                request_hash=_pricing_request_hash("payment_submission_created", payload),
                tenant_id=tenant_id,
                target_invoice_id=target_invoice_id,
                amount=amount,
                paid_at=paid_at,
                external_reference=external_reference,
            )
        except DBAPIError as exc:
            raise _financial_error(exc) from exc

    async def list_tenant_payment_submissions(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        tenant_id: UUID,
        page: int,
        page_size: int,
    ) -> dict[str, object]:
        try:
            return await self.repo.list_tenant_payment_submissions(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                tenant_id=tenant_id,
                page=page,
                page_size=page_size,
            )
        except DBAPIError as exc:
            raise _financial_error(exc) from exc

    async def withdraw_tenant_payment_submission(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        tenant_id: UUID,
        submission_id: UUID,
        expected_row_version: int,
    ) -> PlatformPricingCommandRecord:
        payload: dict[str, object] = {
            "tenant_id": tenant_id,
            "submission_id": submission_id,
            "expected_row_version": expected_row_version,
        }
        try:
            return await self.repo.withdraw_tenant_payment_submission(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                operation_id=operation_id,
                request_hash=_pricing_request_hash("payment_submission_withdrawn", payload),
                tenant_id=tenant_id,
                submission_id=submission_id,
                expected_row_version=expected_row_version,
            )
        except DBAPIError as exc:
            raise _financial_error(exc) from exc

    async def list_platform_payment_submissions(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        tenant_id: UUID,
        page: int,
        page_size: int,
    ) -> dict[str, object]:
        try:
            return await self.repo.list_platform_payment_submissions(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                tenant_id=tenant_id,
                page=page,
                page_size=page_size,
            )
        except DBAPIError as exc:
            raise _financial_error(exc) from exc

    async def read_platform_payment_submission(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        tenant_id: UUID,
        submission_id: UUID,
    ) -> dict[str, object]:
        try:
            return await self.repo.read_platform_payment_submission(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                tenant_id=tenant_id,
                submission_id=submission_id,
            )
        except DBAPIError as exc:
            raise _financial_error(exc) from exc

    async def promote_payment_submission_to_review(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        tenant_id: UUID,
        submission_id: UUID,
        expected_row_version: int,
        recipient_account_key: str,
    ) -> PlatformPricingCommandRecord:
        payload: dict[str, object] = {
            "tenant_id": tenant_id,
            "submission_id": submission_id,
            "expected_row_version": expected_row_version,
            "recipient_account_key": recipient_account_key,
        }
        try:
            return await self.repo.promote_payment_submission_to_review(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                operation_id=operation_id,
                request_hash=_pricing_request_hash("payment_submission_review_created", payload),
                tenant_id=tenant_id,
                submission_id=submission_id,
                expected_row_version=expected_row_version,
                recipient_account_key=recipient_account_key,
            )
        except DBAPIError as exc:
            raise _financial_error(exc) from exc

    async def reject_platform_payment_submission(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        operation_id: UUID,
        tenant_id: UUID,
        submission_id: UUID,
        expected_row_version: int,
        reason_code: str,
        reason_note: str | None,
    ) -> PlatformPricingCommandRecord:
        payload: dict[str, object] = {
            "tenant_id": tenant_id,
            "submission_id": submission_id,
            "expected_row_version": expected_row_version,
            "reason_code": reason_code,
            "reason_note": reason_note,
        }
        try:
            return await self.repo.reject_platform_payment_submission(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                operation_id=operation_id,
                request_hash=_pricing_request_hash("payment_submission_rejected", payload),
                tenant_id=tenant_id,
                submission_id=submission_id,
                expected_row_version=expected_row_version,
                reason_code=reason_code,
                reason_note=reason_note,
            )
        except DBAPIError as exc:
            raise _financial_error(exc) from exc

    async def list_platform_payment_reviews(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        tenant_id: UUID,
        page: int,
        page_size: int,
    ) -> dict[str, object]:
        try:
            return await self.repo.list_platform_payment_reviews(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                tenant_id=tenant_id,
                page=page,
                page_size=page_size,
            )
        except DBAPIError as exc:
            raise _financial_error(exc) from exc

    async def read_platform_payment_review(
        self,
        *,
        actor_user_id: UUID,
        actor_session_id: UUID,
        tenant_id: UUID,
        review_id: UUID,
    ) -> dict[str, object]:
        try:
            return await self.repo.read_platform_payment_review(
                actor_user_id=actor_user_id,
                actor_session_id=actor_session_id,
                tenant_id=tenant_id,
                review_id=review_id,
            )
        except DBAPIError as exc:
            raise _financial_error(exc) from exc

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

    async def list_platform_billing_tenants(
        self,
        *,
        query: str | None,
        page: int,
        page_size: int,
    ) -> tuple[list[PlatformBillingTenantRecord], int]:
        return await self.repo.list_platform_billing_tenants(
            query=query,
            page=page,
            page_size=page_size,
        )


class BillingWorkerService:
    """Runs bounded, retry-safe subscription transitions through DB commands."""

    def __init__(self, repo: BillingWorkerRepository) -> None:
        self.repo = repo

    async def process_trial_endings(self, *, limit: int) -> int:
        return await self.repo.process_trial_endings(limit=limit)

    async def process_grace_endings(self, *, limit: int) -> int:
        return await self.repo.process_grace_endings(limit=limit)
