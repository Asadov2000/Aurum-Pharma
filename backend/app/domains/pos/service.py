"""Business logic for POS — shifts, sales, complete (FEFO + SELECT FOR UPDATE),
linked return documents, prescription requirement.

Critical invariants:
- A completed sale and all its components are immutable. Corrections are new
  linked return documents; a fully refunded state is derived for reads.
- complete() takes a lock on every batch it touches via SELECT FOR UPDATE
  before inserting the negative movement, so two concurrent completes
  serialize correctly and the second sees the up-to-date qty_remaining.
- Tenants in `setup` status book sales as `is_test=true`; the stock
  movement step is skipped for test sales so the inventory ledger stays
  clean until the tenant officially goes live.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import anyio
import structlog
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, InternalError

from app.core.errors import (
    AurumError,
    BusinessRuleError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.time import utc_now
from app.domains.catalog.models import TenantCatalog
from app.domains.foundation.models import Branch, Register, Tenant
from app.domains.foundation.repository import FoundationRepository
from app.domains.inventory.repository import InventoryRepository
from app.domains.inventory.service import InventoryService
from app.domains.pos.models import (
    PrescriptionLog,
    Sale,
    SaleItem,
    SalePayment,
    Shift,
)
from app.domains.pos.receipt_pdf import get_or_render_receipt_pdf
from app.domains.pos.repository import POSRepository
from app.domains.pos.sales_summary_xlsx import render_sales_summary_xlsx
from app.domains.pos.schemas import (
    PAYMENT_METHODS,
    ReceiptData,
    ReceiptLine,
    ReceiptPayment,
    SaleCheckoutItemResult,
    SaleCheckoutPaymentResult,
    SaleCheckoutResult,
    SalesSummaryData,
    SalesSummaryDay,
    SalesSummaryOverview,
    SalesSummaryRow,
    StockOnDateData,
    StockRow,
    ZReportData,
    ZReportPaymentBreakdown,
)
from app.domains.pos.stock_on_date_xlsx import render_stock_on_date_xlsx
from app.domains.pos.z_report_xlsx import get_or_render_z_report_xlsx
from app.domains.sync.integrity import (
    canonical_json_hash,
    projection_stream_checksum,
    sale_projection_hash,
    source_stream_checksum,
)
from app.domains.sync.models import SyncOutboxEvent
from app.domains.sync.repository import SyncOutboxRepository

logger = structlog.get_logger("pos.service")
_MONEY_TEXT_PATTERN = re.compile(r"^(?:0|[1-9]\d{0,11})\.\d{2}$")
_PAYMENT_METADATA_MAX_BYTES = 4096
_MAX_SALES_OVERVIEW_DAYS = 366


class POSService:
    def __init__(
        self,
        repo: POSRepository,
        *,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.repo = repo
        self._now = now

    # =========================================================================
    # Shifts
    # =========================================================================

    async def open_shift(
        self,
        *,
        tenant_id: UUID,
        register_id: UUID,
        opened_by_user_id: UUID,
        opening_cash: Decimal,
        allowed_branch_ids: set[UUID] | None = None,
    ) -> Shift:
        register_result = await self.repo.session.execute(
            select(Register).where(Register.id == register_id).with_for_update()
        )
        register = register_result.scalar_one_or_none()
        if register is None or register.tenant_id != tenant_id:
            raise NotFoundError("Register not found")
        if not register.is_active:
            raise BusinessRuleError("Register is inactive")
        branch_result = await self.repo.session.execute(
            select(Branch).where(Branch.id == register.branch_id).with_for_update()
        )
        branch = branch_result.scalar_one_or_none()
        if branch is None or branch.tenant_id != tenant_id:
            raise NotFoundError("Branch not found")
        self._assert_branch_allowed(branch.id, allowed_branch_ids=allowed_branch_ids)
        if not branch.is_active:
            raise BusinessRuleError("Branch is inactive")

        existing = await self.repo.get_open_shift_for_register(register_id)
        if existing is not None:
            raise ConflictError(
                "Register already has an open shift",
                details={"shift_id": str(existing.id)},
            )
        return await self.repo.create_shift(
            tenant_id=tenant_id,
            branch_id=register.branch_id,
            register_id=register_id,
            opened_by_user_id=opened_by_user_id,
            opening_cash=opening_cash,
        )

    async def get_current_shift(
        self,
        *,
        user_id: UUID,
        register_id: UUID,
        can_manage_tenant: bool = False,
        allowed_branch_ids: set[UUID] | None = None,
        allowed_manage_branch_ids: set[UUID] | None = None,
    ) -> Shift | None:
        shift = await self.repo.get_open_shift_for_user(user_id, register_id)
        if shift is None and (can_manage_tenant or bool(allowed_manage_branch_ids)):
            managed_shift = await self.repo.get_open_shift_for_register(register_id)
            if managed_shift is not None and (
                can_manage_tenant
                or (
                    allowed_manage_branch_ids is not None
                    and managed_shift.branch_id in allowed_manage_branch_ids
                )
            ):
                shift = managed_shift
        if shift is not None:
            self._assert_branch_allowed(shift.branch_id, allowed_branch_ids=allowed_branch_ids)
        return shift

    async def list_shifts(
        self,
        *,
        tenant_id: UUID,
        status: str | None,
        branch_id: UUID | None,
        register_id: UUID | None,
        cashier_id: UUID | None,
        cashier_query: str | None,
        date_from: date | None,
        date_to: date | None,
        allowed_branch_ids: set[UUID] | None,
        page: int,
        page_size: int,
    ) -> tuple[list[dict[str, Any]], int]:
        if date_from is not None and date_to is not None and date_from > date_to:
            raise BusinessRuleError("date_from must be on or before date_to")
        if (
            branch_id is not None
            and allowed_branch_ids is not None
            and branch_id not in allowed_branch_ids
        ):
            raise PermissionDeniedError("Branch access denied")

        normalized_cashier_query = cashier_query.strip() if cashier_query is not None else None
        return await self.repo.list_shifts(
            tenant_id=tenant_id,
            status=status,
            branch_id=branch_id,
            register_id=register_id,
            cashier_id=cashier_id,
            cashier_query=normalized_cashier_query or None,
            date_from=date_from,
            date_to=date_to,
            branch_ids=allowed_branch_ids,
            page=page,
            page_size=page_size,
            tz=await self._report_tz(tenant_id),
        )

    async def close_shift(
        self,
        *,
        shift_id: UUID,
        closing_cash_actual: Decimal,
        closed_by_user_id: UUID,
        can_manage_tenant: bool = False,
        allowed_branch_ids: set[UUID] | None = None,
        allowed_manage_branch_ids: set[UUID] | None = None,
        notes: str | None = None,
    ) -> Shift:
        shift = await self.repo.lock_shift(shift_id)
        if shift is None:
            raise NotFoundError("Shift not found")
        self._assert_branch_allowed(shift.branch_id, allowed_branch_ids=allowed_branch_ids)
        self._assert_shift_owned_or_managed(
            shift,
            actor_id=closed_by_user_id,
            can_manage_tenant=can_manage_tenant,
            allowed_manage_branch_ids=allowed_manage_branch_ids,
        )
        if shift.status == "closed":
            if (
                shift.closed_by_user_id == closed_by_user_id
                and shift.closing_cash_actual == closing_cash_actual
                and shift.notes == notes
            ):
                return shift
            raise BusinessRuleError("Shift is already closed")
        if shift.status != "open":
            raise BusinessRuleError("Shift is not open", details={"status": shift.status})
        if await self.repo.has_active_draft_sales(shift_id):
            raise BusinessRuleError(
                "Cannot close a shift while unfinished sales contain items or payments"
            )
        totals = await self.repo.shift_totals(shift_id)
        cash_in = Decimal(totals.get("cash", "0"))
        expected = shift.opening_cash + cash_in
        diff = closing_cash_actual - expected
        return await self.repo.update_shift(
            shift,
            status="closed",
            closed_at=self._now(),
            closed_by_user_id=closed_by_user_id,
            closing_cash_actual=closing_cash_actual,
            closing_cash_expected=expected,
            closing_difference=diff,
            totals=totals,
            notes=notes,
        )

    async def z_report(
        self,
        shift_id: UUID,
        *,
        allowed_branch_ids: set[UUID] | None = None,
    ) -> dict[str, Any]:
        report = await self.build_z_report(
            shift_id,
            allowed_branch_ids=allowed_branch_ids,
        )
        return {
            "shift_id": report.shift_id,
            "opened_at": report.opened_at,
            "closed_at": report.closed_at,
            "register_id": report.register_id,
            "cashier_user_id": report.cashier_user_id,
            "opening_cash": report.initial_cash,
            "closing_cash_actual": report.actual_cash,
            "closing_cash_expected": report.expected_cash,
            "closing_difference": report.cash_difference,
            "totals": {
                "sales_total": report.total_sales,
                "returns_total": report.total_refunds,
                "discounts_total": report.total_discounts,
                "by_method": report.payment_breakdown.model_dump(),
            },
            "sales_count": report.sales_count,
            "returns_count": report.returns_count,
        }

    # =========================================================================
    # Sales — drafts
    # =========================================================================

    async def create_sale(
        self,
        *,
        tenant_id: UUID,
        register_id: UUID,
        cashier_user_id: UUID,
        operation_id: UUID | None = None,
        operation_hash: str | None = None,
        can_manage_tenant: bool = False,
        allowed_branch_ids: set[UUID] | None = None,
        allowed_manage_branch_ids: set[UUID] | None = None,
    ) -> Sale:
        shift = await self.repo.lock_open_shift_for_register(register_id)
        if shift is None or shift.tenant_id != tenant_id:
            raise BusinessRuleError("No open shift for this register")
        self._assert_branch_allowed(shift.branch_id, allowed_branch_ids=allowed_branch_ids)
        self._assert_shift_owned_or_managed(
            shift,
            actor_id=cashier_user_id,
            can_manage_tenant=can_manage_tenant,
            allowed_manage_branch_ids=allowed_manage_branch_ids,
        )
        # is_test if the tenant is still in 'setup'.
        tenant = await self.repo.session.get(Tenant, tenant_id)
        is_test = bool(tenant is not None and tenant.status == "setup")
        return await self.repo.create_sale(
            tenant_id=tenant_id,
            branch_id=shift.branch_id,
            register_id=register_id,
            shift_id=shift.id,
            cashier_user_id=cashier_user_id,
            is_test=is_test,
            operation_id=operation_id,
            operation_hash=operation_hash,
        )

    @staticmethod
    def _aggregate_checkout_items(
        items: list[tuple[UUID, Decimal]],
    ) -> list[tuple[UUID, Decimal]]:
        per_catalog: dict[UUID, Decimal] = {}
        for catalog_id, qty in items:
            if qty <= 0:
                raise BusinessRuleError("Sale quantity must be positive")
            per_catalog[catalog_id] = per_catalog.get(catalog_id, Decimal("0")) + qty
        if not per_catalog:
            raise BusinessRuleError("Sale must contain at least one item")
        return list(per_catalog.items())

    @staticmethod
    def _checkout_operation_hash(
        *,
        register_id: UUID,
        draft_sale_id: UUID | None,
        items: list[tuple[UUID, Decimal]],
        payments: list[tuple[str, Decimal, Mapping[str, object] | None]],
        prescription: Mapping[str, object] | None,
        expired_sale_confirmed: bool,
    ) -> str:
        payload = {
            "kind": "sale_checkout_v1",
            "register_id": str(register_id),
            "draft_sale_id": str(draft_sale_id) if draft_sale_id is not None else None,
            "items": [
                [str(catalog_id), format(qty.normalize(), "f")]
                for catalog_id, qty in sorted(items, key=lambda pair: str(pair[0]))
            ],
            "payments": [
                {
                    "payment_method": payment_method,
                    "amount": format(amount.normalize(), "f"),
                    "metadata": dict(metadata) if metadata is not None else None,
                }
                for payment_method, amount, metadata in payments
            ],
            "prescription": dict(prescription) if prescription is not None else None,
            "expired_sale_confirmed": expired_sale_confirmed,
        }
        try:
            canonical = json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise BusinessRuleError("Checkout payload must contain valid JSON") from exc
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _checkout_result_hash(payload: Mapping[str, object]) -> str:
        try:
            return canonical_json_hash(payload)
        except (TypeError, ValueError) as exc:
            raise AurumError("Checkout result is not serializable") from exc

    async def _find_existing_checkout(
        self,
        *,
        tenant_id: UUID,
        operation_id: UUID,
        operation_hash: str,
        actor_id: UUID,
        can_manage_tenant: bool,
        allowed_branch_ids: set[UUID] | None,
        allowed_manage_branch_ids: set[UUID] | None,
    ) -> SaleCheckoutResult | None:
        existing_payment = await self.repo.get_payment_by_operation_id(
            tenant_id=tenant_id,
            operation_id=operation_id,
        )
        if existing_payment is not None:
            raise ConflictError("Operation ID was already used for another POS operation")

        existing = await self.repo.get_sale_by_operation_id(
            tenant_id=tenant_id,
            operation_id=operation_id,
        )
        outbox_event = await SyncOutboxRepository(self.repo.session).get_by_operation_id(
            tenant_id=tenant_id,
            operation_id=operation_id,
        )
        if existing is None:
            if outbox_event is not None:
                raise AurumError("Checkout outbox event has no sale aggregate")
            return None
        if existing.sale_type != "sale" or existing.operation_hash != operation_hash:
            raise ConflictError("Operation ID was already used for another sale command")
        return self._restore_checkout_result(
            sale=existing,
            outbox_event=outbox_event,
            tenant_id=tenant_id,
            operation_id=operation_id,
            actor_id=actor_id,
            can_manage_tenant=can_manage_tenant,
            allowed_branch_ids=allowed_branch_ids,
            allowed_manage_branch_ids=allowed_manage_branch_ids,
        )

    def _restore_checkout_result(
        self,
        *,
        sale: Sale,
        outbox_event: SyncOutboxEvent | None,
        tenant_id: UUID,
        operation_id: UUID,
        actor_id: UUID,
        can_manage_tenant: bool,
        allowed_branch_ids: set[UUID] | None,
        allowed_manage_branch_ids: set[UUID] | None,
    ) -> SaleCheckoutResult:
        self._assert_sale_owned_or_managed(
            sale,
            actor_id=actor_id,
            can_manage_tenant=can_manage_tenant,
            allowed_branch_ids=allowed_branch_ids,
            allowed_manage_branch_ids=allowed_manage_branch_ids,
        )
        if (
            sale.sale_type != "sale"
            or sale.receipt_number is None
            or sale.status not in {"completed", "voided"}
        ):
            raise AurumError("Checkout sale aggregate is incomplete")
        if (
            outbox_event is None
            or outbox_event.aggregate_type != "sale"
            or outbox_event.aggregate_id != sale.id
            or outbox_event.event_type != "pos.sale.completed.v1"
            or outbox_event.schema_version != 1
        ):
            raise AurumError("Checkout result snapshot is unavailable")
        if outbox_event.payload_hash != self._checkout_result_hash(outbox_event.payload):
            raise AurumError("Checkout result snapshot failed integrity validation")
        try:
            result = SaleCheckoutResult.model_validate(outbox_event.payload)
        except PydanticValidationError as exc:
            raise AurumError("Checkout result snapshot is invalid") from exc
        if (
            result.event_id != outbox_event.event_id
            or result.sale_id != sale.id
            or result.operation_id != operation_id
            or result.tenant_id != tenant_id
        ):
            raise AurumError("Checkout result snapshot does not match the sale")
        return result

    async def get_checkout_result(
        self,
        *,
        tenant_id: UUID,
        operation_id: UUID,
        actor_id: UUID,
        can_manage_tenant: bool = False,
        allowed_branch_ids: set[UUID] | None = None,
        allowed_manage_branch_ids: set[UUID] | None = None,
    ) -> SaleCheckoutResult:
        await self.repo.lock_operation_id(operation_id)
        if (
            await self.repo.get_payment_by_operation_id(
                tenant_id=tenant_id,
                operation_id=operation_id,
            )
            is not None
        ):
            raise NotFoundError("Checkout operation not found")
        sale = await self.repo.get_sale_by_operation_id(
            tenant_id=tenant_id,
            operation_id=operation_id,
        )
        if sale is None or sale.sale_type != "sale":
            raise NotFoundError("Checkout operation not found")
        outbox_event = await SyncOutboxRepository(self.repo.session).get_by_operation_id(
            tenant_id=tenant_id,
            operation_id=operation_id,
        )
        return self._restore_checkout_result(
            sale=sale,
            outbox_event=outbox_event,
            tenant_id=tenant_id,
            operation_id=operation_id,
            actor_id=actor_id,
            can_manage_tenant=can_manage_tenant,
            allowed_branch_ids=allowed_branch_ids,
            allowed_manage_branch_ids=allowed_manage_branch_ids,
        )

    @staticmethod
    def _validate_checkout_payments(
        payments: list[tuple[str, Decimal, Mapping[str, object] | None]],
    ) -> None:
        for payment_method, amount, metadata in payments:
            if amount <= 0:
                raise BusinessRuleError("Payment amount must be positive")
            POSService._validate_payment_metadata(
                payment_method=payment_method,
                amount=amount,
                metadata=metadata,
            )

    @staticmethod
    def _validate_payment_metadata(
        *,
        payment_method: str,
        amount: Decimal,
        metadata: Mapping[str, object] | None,
    ) -> None:
        if metadata is None:
            if payment_method in {"card", "qr"}:
                raise BusinessRuleError(
                    "Card and QR payments must be confirmed in the external terminal"
                )
            return
        try:
            serialized = json.dumps(
                dict(metadata),
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise BusinessRuleError("Payment metadata must be valid JSON") from exc
        if len(serialized) > _PAYMENT_METADATA_MAX_BYTES:
            raise BusinessRuleError("Payment metadata is too large")

        raw_external_confirmed = metadata.get("external_confirmed")
        if payment_method in {"card", "qr"}:
            if raw_external_confirmed is not True:
                raise BusinessRuleError(
                    "Card and QR payments must be confirmed in the external terminal"
                )
        elif "external_confirmed" in metadata:
            raise BusinessRuleError("external_confirmed is valid only for card and QR payments")

        raw_cash_received = metadata.get("cash_received")
        if raw_cash_received is None:
            return
        if payment_method != "cash":
            raise BusinessRuleError("cash_received is valid only for cash payments")
        if not isinstance(raw_cash_received, str) or not _MONEY_TEXT_PATTERN.fullmatch(
            raw_cash_received
        ):
            raise BusinessRuleError("cash_received must be a valid money amount")
        cash_received = Decimal(raw_cash_received)
        if cash_received < amount:
            raise BusinessRuleError(
                "Cash received cannot be less than the allocated payment",
                details={"received": str(cash_received), "allocated": str(amount)},
            )

    @staticmethod
    def _payment_tendered_amount(payment: SalePayment) -> Decimal:
        if payment.payment_method != "cash" or payment.metadata_json is None:
            return payment.amount
        raw_cash_received = payment.metadata_json.get("cash_received")
        if not isinstance(raw_cash_received, str) or not _MONEY_TEXT_PATTERN.fullmatch(
            raw_cash_received
        ):
            return payment.amount
        cash_received = Decimal(raw_cash_received)
        return cash_received if cash_received >= payment.amount else payment.amount

    async def _validate_pos_payment_configuration(
        self,
        *,
        tenant_id: UUID,
        requested_methods: set[str],
        existing_methods: set[str] | None = None,
    ) -> None:
        unsupported = requested_methods - PAYMENT_METHODS
        if unsupported:
            raise BusinessRuleError(
                "Unsupported POS payment method",
                details={"methods": sorted(unsupported)},
            )
        settings = await FoundationRepository(self.repo.session).get_settings_for_pos(tenant_id)
        if settings is None:
            raise AurumError("POS payment settings are unavailable")
        disabled = requested_methods - set(settings.pos_payment_methods)
        if disabled:
            raise BusinessRuleError(
                "POS payment method is disabled",
                details={"methods": sorted(disabled)},
            )
        combined_methods = requested_methods | (existing_methods or set())
        if not settings.pos_mixed_payment_enabled and len(combined_methods) > 1:
            raise BusinessRuleError("Mixed POS payments are disabled")

    async def _prepare_checkout_sale(
        self,
        *,
        tenant_id: UUID,
        register_id: UUID,
        cashier_user_id: UUID,
        operation_id: UUID,
        operation_hash: str,
        draft_sale_id: UUID | None,
        can_manage_tenant: bool,
        allowed_branch_ids: set[UUID] | None,
        allowed_manage_branch_ids: set[UUID] | None,
    ) -> Sale:
        if draft_sale_id is None:
            return await self.create_sale(
                tenant_id=tenant_id,
                register_id=register_id,
                cashier_user_id=cashier_user_id,
                operation_id=operation_id,
                operation_hash=operation_hash,
                can_manage_tenant=can_manage_tenant,
                allowed_branch_ids=allowed_branch_ids,
                allowed_manage_branch_ids=allowed_manage_branch_ids,
            )

        sale = await self._lock_sale(draft_sale_id)
        self._assert_sale_owned_or_managed(
            sale,
            actor_id=cashier_user_id,
            can_manage_tenant=can_manage_tenant,
            allowed_branch_ids=allowed_branch_ids,
            allowed_manage_branch_ids=allowed_manage_branch_ids,
        )
        if (
            sale.tenant_id != tenant_id
            or sale.register_id != register_id
            or sale.sale_type != "sale"
        ):
            raise NotFoundError("Checkout draft not found")
        self._assert_draft(sale)
        if sale.operation_id is not None or sale.operation_hash is not None:
            raise ConflictError("Checkout draft already has an operation")
        has_payments = bool(await self.repo.list_payments(sale.id))
        has_prescriptions = bool(await self.repo.list_prescriptions(sale.id))
        if has_payments or has_prescriptions:
            raise BusinessRuleError("Checkout draft contains legacy financial data")
        await self.repo.delete_items(sale.id)
        return await self.repo.update_sale(
            sale,
            total_amount=Decimal("0"),
            operation_id=operation_id,
            operation_hash=operation_hash,
        )

    async def checkout(
        self,
        *,
        tenant_id: UUID,
        register_id: UUID,
        cashier_user_id: UUID,
        operation_id: UUID,
        draft_sale_id: UUID | None = None,
        items: list[tuple[UUID, Decimal]],
        payments: list[tuple[str, Decimal, Mapping[str, object] | None]],
        prescription: Mapping[str, object] | None = None,
        expired_sale_confirmed: bool = False,
        can_manage_tenant: bool = False,
        allowed_branch_ids: set[UUID] | None = None,
        allowed_manage_branch_ids: set[UUID] | None = None,
    ) -> SaleCheckoutResult:
        """Create and complete a sale as one request transaction.

        The FastAPI ``get_db`` dependency owns the transaction boundary. Any
        exception from this method therefore removes the sale shell, lines,
        payments, prescription, stock movements, and receipt allocation.
        """
        aggregated_items = self._aggregate_checkout_items(items)
        self._validate_checkout_payments(payments)
        operation_hash = self._checkout_operation_hash(
            register_id=register_id,
            draft_sale_id=draft_sale_id,
            items=aggregated_items,
            payments=payments,
            prescription=prescription,
            expired_sale_confirmed=expired_sale_confirmed,
        )

        await self.repo.lock_operation_id(operation_id)
        existing = await self._find_existing_checkout(
            tenant_id=tenant_id,
            operation_id=operation_id,
            operation_hash=operation_hash,
            actor_id=cashier_user_id,
            can_manage_tenant=can_manage_tenant,
            allowed_branch_ids=allowed_branch_ids,
            allowed_manage_branch_ids=allowed_manage_branch_ids,
        )
        if existing is not None:
            return existing

        await self._validate_pos_payment_configuration(
            tenant_id=tenant_id,
            requested_methods={method for method, _amount, _metadata in payments},
        )
        sale = await self._prepare_checkout_sale(
            tenant_id=tenant_id,
            register_id=register_id,
            cashier_user_id=cashier_user_id,
            operation_id=operation_id,
            operation_hash=operation_hash,
            draft_sale_id=draft_sale_id,
            can_manage_tenant=can_manage_tenant,
            allowed_branch_ids=allowed_branch_ids,
            allowed_manage_branch_ids=allowed_manage_branch_ids,
        )
        for catalog_id, qty in aggregated_items:
            await self.add_item(
                sale_id=sale.id,
                catalog_id=catalog_id,
                qty=qty,
                actor_id=cashier_user_id,
                can_manage_tenant=can_manage_tenant,
                allowed_branch_ids=allowed_branch_ids,
                allowed_manage_branch_ids=allowed_manage_branch_ids,
                expired_sale_confirmed=expired_sale_confirmed,
            )

        paid_total = sum((amount for _method, amount, _metadata in payments), Decimal("0"))
        if paid_total != sale.total_amount:
            raise BusinessRuleError(
                "Payment total does not match sale total",
                details={"paid": str(paid_total), "total": str(sale.total_amount)},
            )
        for payment_method, amount, metadata in payments:
            await self.repo.insert_payment(
                tenant_id=tenant_id,
                sale_id=sale.id,
                payment_method=payment_method,
                amount=amount,
                metadata_json=dict(metadata) if metadata is not None else None,
            )

        if prescription is not None:
            await self.add_prescription(
                sale_id=sale.id,
                fields=dict(prescription),
                actor_id=cashier_user_id,
                can_manage_tenant=can_manage_tenant,
                allowed_branch_ids=allowed_branch_ids,
                allowed_manage_branch_ids=allowed_manage_branch_ids,
            )
        completed = await self.complete(
            sale_id=sale.id,
            actor_id=cashier_user_id,
            can_manage_tenant=can_manage_tenant,
            allowed_branch_ids=allowed_branch_ids,
            allowed_manage_branch_ids=allowed_manage_branch_ids,
            expired_sale_confirmed=expired_sale_confirmed,
        )
        if (
            completed.receipt_number is None
            or completed.receipt_seq is None
            or completed.completed_at is None
        ):
            raise AurumError("Completed sale has no receipt snapshot")

        sale_items = await self.repo.list_items(completed.id)
        sale_payments = await self.repo.list_payments(completed.id)
        event_id = uuid4()
        result = SaleCheckoutResult(
            event_id=event_id,
            sale_id=completed.id,
            operation_id=operation_id,
            tenant_id=completed.tenant_id,
            branch_id=completed.branch_id,
            register_id=completed.register_id,
            shift_id=completed.shift_id,
            cashier_user_id=completed.cashier_user_id,
            receipt_number=completed.receipt_number,
            receipt_seq=completed.receipt_seq,
            created_at=completed.created_at,
            completed_at=completed.completed_at,
            total_amount=completed.total_amount,
            currency=completed.currency,
            is_test=completed.is_test,
            items=[SaleCheckoutItemResult.model_validate(item) for item in sale_items],
            payments=[
                SaleCheckoutPaymentResult.model_validate(payment) for payment in sale_payments
            ],
        )
        event_payload = result.model_dump(mode="json")
        outbox = SyncOutboxRepository(self.repo.session)
        position = await outbox.reserve_position(
            tenant_id=completed.tenant_id,
            branch_id=completed.branch_id,
        )
        event_payload_hash = self._checkout_result_hash(event_payload)
        event_projection_hash = sale_projection_hash(event_payload)
        stream_checksum = source_stream_checksum(
            previous_checksum=position.previous_checksum,
            event_id=event_id,
            tenant_id=completed.tenant_id,
            branch_id=completed.branch_id,
            origin_node_id=position.origin_node_id,
            writer_epoch=position.writer_epoch,
            sequence=position.sequence,
            operation_id=operation_id,
            aggregate_type="sale",
            aggregate_id=completed.id,
            event_type="pos.sale.completed.v1",
            schema_version=1,
            occurred_at=completed.completed_at,
            payload_hash=event_payload_hash,
        )
        projection_checksum = projection_stream_checksum(
            previous_checksum=position.previous_projection_checksum,
            origin_node_id=position.origin_node_id,
            writer_epoch=position.writer_epoch,
            sequence=position.sequence,
            sale_id=completed.id,
            projection_hash=event_projection_hash,
        )
        await outbox.enqueue(
            event_id=event_id,
            tenant_id=completed.tenant_id,
            branch_id=completed.branch_id,
            origin_node_id=position.origin_node_id,
            writer_epoch=position.writer_epoch,
            sequence=position.sequence,
            operation_id=operation_id,
            aggregate_type="sale",
            aggregate_id=completed.id,
            event_type="pos.sale.completed.v1",
            schema_version=1,
            occurred_at=completed.completed_at,
            payload=event_payload,
            payload_hash=event_payload_hash,
            stream_checksum=stream_checksum,
            projection_hash=event_projection_hash,
            projection_checksum=projection_checksum,
        )
        await outbox.finalize_position(
            stream_id=position.stream_id,
            sequence=position.sequence,
            stream_checksum=stream_checksum,
            projection_checksum=projection_checksum,
        )
        return result

    async def get_sale(self, sale_id: UUID) -> Sale:
        sale = await self.repo.get_sale(sale_id)
        if sale is None:
            raise NotFoundError("Sale not found")
        return sale

    async def get_sale_lifecycle(self, sale: Sale) -> dict[str, object]:
        lifecycle = await self.repo.sale_lifecycle(sale)
        return {
            "status": lifecycle.status,
            "voided_at": lifecycle.voided_at,
            "voided_by_sale_id": lifecycle.voided_by_sale_id,
        }

    async def get_refunded_quantities(self, parent_sale_id: UUID) -> dict[UUID, Decimal]:
        return await self.repo.refunded_quantities(parent_sale_id)

    async def _lock_sale(self, sale_id: UUID) -> Sale:
        sale = await self.repo.lock_sale(sale_id)
        if sale is None:
            raise NotFoundError("Sale not found")
        return sale

    async def _lock_open_shift(self, shift_id: UUID, *, error_message: str) -> Shift:
        shift = await self.repo.lock_shift(shift_id)
        if shift is None:
            raise NotFoundError("Shift not found")
        if shift.status != "open":
            raise BusinessRuleError(error_message, details={"status": shift.status})
        return shift

    async def _allocate_receipt(self, register_id: UUID) -> tuple[int, str]:
        allocation = await self.repo.allocate_receipt_number(register_id)
        if allocation is None:
            raise NotFoundError("Register not found")
        return allocation

    async def _report_tz(self, tenant_id: UUID) -> str:
        """Tenant's report timezone (date ranges are interpreted in local time).
        Falls back to Asia/Dushanbe if settings are somehow missing."""
        settings = await FoundationRepository(self.repo.session).get_settings(tenant_id)
        return settings.report_timezone if settings is not None else "Asia/Dushanbe"

    async def list_sales(
        self,
        *,
        tenant_id: UUID,
        cashier_id: UUID | None,
        branch_id: UUID | None,
        register_id: UUID | None,
        receipt_number: str | None,
        date_from: date | None,
        date_to: date | None,
        has_refund: bool | None,
        min_total: Decimal | None,
        max_total: Decimal | None,
        branch_ids: set[UUID] | None = None,
        page: int,
        page_size: int,
        viewer_id: UUID | None = None,
        own_branch_ids: set[UUID] | None = None,
        tenant_view_branch_ids: set[UUID] | None = None,
        can_view_tenant: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        return await self.repo.list_sales(
            tenant_id=tenant_id,
            cashier_id=cashier_id,
            branch_id=branch_id,
            register_id=register_id,
            receipt_number=receipt_number,
            date_from=date_from,
            date_to=date_to,
            has_refund=has_refund,
            min_total=min_total,
            max_total=max_total,
            branch_ids=branch_ids,
            page=page,
            page_size=page_size,
            tz=await self._report_tz(tenant_id),
            viewer_id=viewer_id,
            own_branch_ids=own_branch_ids,
            tenant_view_branch_ids=tenant_view_branch_ids,
            can_view_tenant=can_view_tenant,
        )

    async def get_sale_details(
        self,
        sale_id: UUID,
        *,
        viewer_id: UUID | None = None,
        can_view_tenant: bool = False,
        allowed_branch_ids: set[UUID] | None = None,
        allowed_view_branch_ids: set[UUID] | None = None,
    ) -> tuple[Sale, list[tuple[SaleItem, str | None, date | None, int | None]], list[SalePayment]]:
        sale = await self.get_sale(sale_id)
        if viewer_id is not None:
            self._assert_sale_viewable(
                sale,
                viewer_id=viewer_id,
                can_view_tenant=can_view_tenant,
                allowed_branch_ids=allowed_branch_ids,
                allowed_view_branch_ids=allowed_view_branch_ids,
            )
        items = await self.repo.list_items_with_batch(sale.id)
        payments = await self.repo.list_payments(sale.id)
        return sale, items, payments

    # ---- receipt (print / PDF) ----

    async def _compose_receipt_data(
        self,
        *,
        sale: Sale,
        items: list[SaleItem],
        payments: list[SalePayment],
        status: str,
        receipt_number: str | None,
        receipt_datetime: datetime,
        total_amount: Decimal,
    ) -> ReceiptData:
        tenant = await self.repo.session.get(Tenant, sale.tenant_id)
        branch = await self.repo.session.get(Branch, sale.branch_id)
        cashier_name = await self.repo.get_user_display_name(sale.cashier_user_id)
        timezone_name = await self._report_tz(sale.tenant_id)
        try:
            local_receipt_datetime = receipt_datetime.astimezone(ZoneInfo(timezone_name))
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise AurumError("Tenant report timezone is invalid") from exc

        original_return_names: dict[UUID, str] = {}
        if sale.sale_type == "return" and sale.parent_sale_id is not None:
            parent = await self.repo.get_sale(sale.parent_sale_id)
            if parent is not None and parent.receipt_snapshot is not None:
                try:
                    parent_receipt = ReceiptData.model_validate(parent.receipt_snapshot)
                except PydanticValidationError as exc:
                    raise AurumError("Parent receipt snapshot is invalid") from exc
                parent_items = await self.repo.list_items(parent.id)
                name_by_position = {line.position: line.name for line in parent_receipt.items}
                original_return_names = {
                    item.id: name_by_position[item.position]
                    for item in parent_items
                    if item.position in name_by_position
                }

        lines: list[ReceiptLine] = []
        for item in items:
            original_name = (
                original_return_names.get(item.parent_sale_item_id)
                if item.parent_sale_item_id is not None
                else None
            )
            catalog = (
                None
                if original_name is not None
                else await self.repo.session.get(TenantCatalog, item.catalog_id)
            )
            lines.append(
                ReceiptLine(
                    position=item.position,
                    name=(
                        original_name
                        or (catalog.brand_name if catalog is not None else str(item.catalog_id))
                    ),
                    qty=item.qty,
                    unit_price=item.unit_price,
                    discount_amount=item.discount_amount,
                    total_price=item.total_price,
                )
            )

        discount_total = sum((item.discount_amount for item in items), Decimal("0"))
        paid_total = sum(
            (self._payment_tendered_amount(payment) for payment in payments),
            Decimal("0"),
        )
        change = max(Decimal("0"), paid_total - total_amount)
        return ReceiptData(
            sale_id=sale.id,
            is_refund=sale.sale_type == "return",
            status=status,
            pharmacy_name=tenant.name if tenant is not None else "",
            branch_name=branch.name if branch is not None else "",
            branch_address=branch.address if branch is not None else None,
            branch_license=branch.license_number if branch is not None else None,
            receipt_number=receipt_number,
            datetime=local_receipt_datetime,
            cashier_name=cashier_name,
            items=lines,
            discount_total=discount_total,
            total=total_amount,
            currency=sale.currency,
            payments=[
                ReceiptPayment(method=payment.payment_method, amount=payment.amount)
                for payment in payments
            ],
            paid_total=paid_total,
            change=change,
        )

    async def build_receipt(
        self,
        sale_id: UUID,
        *,
        viewer_id: UUID | None = None,
        can_view_tenant: bool = False,
        allowed_branch_ids: set[UUID] | None = None,
        allowed_view_branch_ids: set[UUID] | None = None,
    ) -> ReceiptData:
        """Assemble everything a printed receipt needs, fully resolved (names,
        not UUIDs). Shared by the JSON print view and the PDF generator. RLS
        scopes every fetch to the caller's tenant."""
        sale = await self.get_sale(sale_id)
        if viewer_id is not None:
            self._assert_sale_viewable(
                sale,
                viewer_id=viewer_id,
                can_view_tenant=can_view_tenant,
                allowed_branch_ids=allowed_branch_ids,
                allowed_view_branch_ids=allowed_view_branch_ids,
            )
        if sale.receipt_snapshot is not None:
            try:
                snapshot = ReceiptData.model_validate(sale.receipt_snapshot)
            except PydanticValidationError as exc:
                raise AurumError("Receipt snapshot is invalid") from exc
            if snapshot.sale_id != sale.id:
                raise AurumError("Receipt snapshot does not match the sale")
            return snapshot

        items = await self.repo.list_items(sale.id)
        payments = await self.repo.list_payments(sale.id)
        lifecycle = await self.repo.sale_lifecycle(sale)
        return await self._compose_receipt_data(
            sale=sale,
            items=items,
            payments=payments,
            status=lifecycle.status,
            receipt_number=sale.receipt_number,
            receipt_datetime=sale.completed_at or sale.created_at,
            total_amount=sale.total_amount,
        )

    async def get_receipt_pdf(
        self,
        sale_id: UUID,
        *,
        viewer_id: UUID | None = None,
        can_view_tenant: bool = False,
        allowed_branch_ids: set[UUID] | None = None,
        allowed_view_branch_ids: set[UUID] | None = None,
    ) -> bytes:
        """Lazily render (and cache in MinIO) the receipt PDF. Completed sales
        are immutable, so a cached PDF stays valid forever; drafts render fresh
        each time and are never cached. The blocking render + MinIO IO runs in a
        worker thread so it doesn't stall the event loop."""
        data = await self.build_receipt(
            sale_id,
            viewer_id=viewer_id,
            can_view_tenant=can_view_tenant,
            allowed_branch_ids=allowed_branch_ids,
            allowed_view_branch_ids=allowed_view_branch_ids,
        )
        return await anyio.to_thread.run_sync(get_or_render_receipt_pdf, data)

    # ---- Z-report (shift close XLSX) ----

    async def build_z_report(
        self,
        shift_id: UUID,
        *,
        allowed_branch_ids: set[UUID] | None = None,
    ) -> ZReportData:
        """Assemble the shift summary (names resolved) for the XLSX export."""
        shift = await self.repo.get_shift(shift_id)
        if shift is None:
            raise NotFoundError("Shift not found")
        self._assert_branch_allowed(shift.branch_id, allowed_branch_ids=allowed_branch_ids)

        tenant = await self.repo.session.get(Tenant, shift.tenant_id)
        branch = await self.repo.session.get(Branch, shift.branch_id)
        register = await self.repo.session.get(Register, shift.register_id)
        cashier_name = await self.repo.get_user_display_name(shift.opened_by_user_id)
        agg = await self.repo.z_report_aggregates(shift_id)
        bd = agg["payment_breakdown"]

        return ZReportData(
            shift_id=shift.id,
            status=shift.status,
            pharmacy_name=tenant.name if tenant is not None else "",
            branch_name=branch.name if branch is not None else "",
            register_id=shift.register_id,
            register_name=register.name if register is not None else "",
            cashier_user_id=shift.opened_by_user_id,
            cashier_name=cashier_name,
            opened_at=shift.opened_at,
            closed_at=shift.closed_at,
            sales_count=agg["sales_count"],
            total_sales=agg["total_sales"],
            total_discounts=agg["total_discounts"],
            returns_count=agg["returns_count"],
            total_refunds=agg["total_refunds"],
            currency=shift.currency,
            payment_breakdown=ZReportPaymentBreakdown(
                cash=bd["cash"],
                card=bd["card"],
                qr=bd["qr"],
                bank_transfer=bd["bank_transfer"],
                mixed=bd["mixed"],
            ),
            initial_cash=shift.opening_cash,
            expected_cash=shift.closing_cash_expected,
            actual_cash=shift.closing_cash_actual,
            cash_difference=shift.closing_difference,
            # No dedicated difference_reason column — the close dialog's free-text
            # note doubles as the explanation.
            difference_reason=shift.notes,
        )

    async def get_z_report_xlsx(
        self,
        shift_id: UUID,
        *,
        allowed_branch_ids: set[UUID] | None = None,
    ) -> bytes:
        """Lazily render + cache the Z-report XLSX in MinIO. Only closed shifts
        qualify — an open shift's totals are incomplete."""
        shift = await self.repo.get_shift(shift_id)
        if shift is None:
            raise NotFoundError("Shift not found")
        self._assert_branch_allowed(shift.branch_id, allowed_branch_ids=allowed_branch_ids)
        if shift.status != "closed":
            raise BusinessRuleError(
                "Z-report is available only after the shift is closed",
                details={"status": shift.status},
            )
        data = await self.build_z_report(shift_id, allowed_branch_ids=allowed_branch_ids)
        return await anyio.to_thread.run_sync(get_or_render_z_report_xlsx, data)

    # ---- sales summary (accountant XLSX, arbitrary range) ----

    async def build_sales_summary(
        self,
        *,
        tenant_id: UUID,
        date_from: date,
        date_to: date,
        branch_id: UUID | None,
    ) -> SalesSummaryData:
        agg = await self.repo.sales_summary(
            tenant_id=tenant_id,
            date_from=date_from,
            date_to=date_to,
            branch_id=branch_id,
            tz=await self._report_tz(tenant_id),
        )
        foundation = FoundationRepository(self.repo.session)

        if branch_id is not None:
            branch = await foundation.get_branch(branch_id)
            branch_name = branch.name if branch is not None else None
            show_branch_column = False
        else:
            branch_name = None
            show_branch_column = await foundation.count_active_branches(tenant_id) > 1

        rows: list[SalesSummaryRow] = []
        currency = "TJS"
        for r in agg["rows"]:
            currency = r["currency"] or currency
            if r["sale_type"] == "return":
                kind = "return"
            elif r["status"] == "voided":
                kind = "voided"
            else:
                kind = "sale"
            gross = Decimal(str(r["gross"]))
            discount = Decimal(str(r["discount"]))
            rows.append(
                SalesSummaryRow(
                    completed_at=r["completed_at"],
                    receipt_number=r["receipt_number"],
                    cashier_name=r["cashier_name"],
                    branch_name=r["branch_name"],
                    kind=kind,
                    payment_method=r["payment_method"] or "none",
                    gross=gross,
                    discount=discount,
                    net=gross - discount,
                )
            )

        bd = agg["payment_breakdown"]
        net = agg["gross_sales"] - agg["total_discounts"] - agg["total_refunds"]
        return SalesSummaryData(
            date_from=date_from,
            date_to=date_to,
            branch_name=branch_name,
            show_branch_column=show_branch_column,
            currency=currency,
            rows=rows,
            gross_sales=agg["gross_sales"],
            total_discounts=agg["total_discounts"],
            total_refunds=agg["total_refunds"],
            net=net,
            sales_count=agg["sales_count"],
            returns_count=agg["returns_count"],
            payment_breakdown=ZReportPaymentBreakdown(
                cash=bd["cash"],
                card=bd["card"],
                qr=bd["qr"],
                bank_transfer=bd["bank_transfer"],
                mixed=bd["mixed"],
            ),
        )

    async def get_sales_summary_xlsx(
        self,
        *,
        tenant_id: UUID,
        date_from: date,
        date_to: date,
        branch_id: UUID | None,
    ) -> bytes:
        """Render the accountant sales summary on the fly (no MinIO cache — the
        range is arbitrary and the data isn't frozen). An empty period yields a
        valid workbook with zero totals, not an error."""
        if date_from > date_to:
            raise BusinessRuleError(
                "date_from must not be after date_to",
                details={"from": date_from.isoformat(), "to": date_to.isoformat()},
            )
        data = await self.build_sales_summary(
            tenant_id=tenant_id, date_from=date_from, date_to=date_to, branch_id=branch_id
        )
        return await anyio.to_thread.run_sync(render_sales_summary_xlsx, data)

    async def get_sales_summary_overview(
        self,
        *,
        tenant_id: UUID,
        date_from: date,
        date_to: date,
        branch_id: UUID | None,
    ) -> SalesSummaryOverview:
        if date_from > date_to:
            raise BusinessRuleError(
                "date_from must not be after date_to",
                details={"from": date_from.isoformat(), "to": date_to.isoformat()},
            )
        if date_to - date_from >= timedelta(days=_MAX_SALES_OVERVIEW_DAYS):
            raise BusinessRuleError(
                "sales overview period must not exceed 366 days",
                details={"max_days": _MAX_SALES_OVERVIEW_DAYS},
            )

        agg = await self.repo.sales_summary(
            tenant_id=tenant_id,
            date_from=date_from,
            date_to=date_to,
            branch_id=branch_id,
            tz=await self._report_tz(tenant_id),
            include_rows=False,
            include_daily=True,
        )
        foundation = FoundationRepository(self.repo.session)
        branch_name: str | None = None
        if branch_id is not None:
            branch = await foundation.get_branch(branch_id)
            branch_name = branch.name if branch is not None else None

        currency = "TJS"
        money_quantum = Decimal("0.01")
        gross_sales = Decimal(str(agg["gross_sales"])).quantize(money_quantum)
        total_discounts = Decimal(str(agg["total_discounts"])).quantize(money_quantum)
        total_refunds = Decimal(str(agg["total_refunds"])).quantize(money_quantum)
        sales_count = int(agg["sales_count"])
        average_sale = (
            ((gross_sales - total_discounts) / sales_count).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if sales_count
            else Decimal("0.00")
        )
        breakdown = agg["payment_breakdown"]
        daily: list[SalesSummaryDay] = []
        for item in agg["daily"]:
            day_gross = Decimal(str(item["gross_sales"])).quantize(money_quantum)
            day_discounts = Decimal(str(item["total_discounts"])).quantize(money_quantum)
            day_refunds = Decimal(str(item["total_refunds"])).quantize(money_quantum)
            daily.append(
                SalesSummaryDay(
                    day=item["day"],
                    gross_sales=day_gross,
                    total_discounts=day_discounts,
                    total_refunds=day_refunds,
                    net=day_gross - day_discounts - day_refunds,
                    sales_count=int(item["sales_count"]),
                    returns_count=int(item["returns_count"]),
                )
            )

        return SalesSummaryOverview(
            date_from=date_from,
            date_to=date_to,
            branch_name=branch_name,
            currency=currency,
            gross_sales=gross_sales,
            total_discounts=total_discounts,
            total_refunds=total_refunds,
            net=gross_sales - total_discounts - total_refunds,
            sales_count=sales_count,
            returns_count=int(agg["returns_count"]),
            average_sale=average_sale,
            payment_breakdown=ZReportPaymentBreakdown(
                cash=breakdown["cash"],
                card=breakdown["card"],
                qr=breakdown["qr"],
                bank_transfer=breakdown["bank_transfer"],
                mixed=breakdown["mixed"],
            ),
            daily=daily,
        )

    # ---- stock on date (accountant XLSX) ----

    async def build_stock_on_date(
        self,
        *,
        tenant_id: UUID,
        on_date: date,
        branch_id: UUID | None,
    ) -> StockOnDateData:
        raw = await self.repo.stock_on_date(
            tenant_id=tenant_id,
            on_date=on_date,
            branch_id=branch_id,
            tz=await self._report_tz(tenant_id),
        )
        foundation = FoundationRepository(self.repo.session)

        if branch_id is not None:
            branch = await foundation.get_branch(branch_id)
            branch_name = branch.name if branch is not None else None
            show_branch_column = False
        else:
            branch_name = None
            show_branch_column = await foundation.count_active_branches(tenant_id) > 1

        rows: list[StockRow] = []
        total_qty = Decimal("0")
        total_value = Decimal("0")
        currency = "TJS"
        for r in raw:
            currency = r["currency"] or currency
            qty = Decimal(str(r["qty"]))
            purchase_price = Decimal(str(r["purchase_price"]))
            value = (qty * purchase_price).quantize(Decimal("0.01"))
            total_qty += qty
            total_value += value
            rows.append(
                StockRow(
                    name=r["name"] or "—",
                    inn=r["inn"],
                    branch_name=r["branch_name"],
                    batch_number=r["batch_number"],
                    expires_at=r["expires_at"],
                    qty=qty,
                    purchase_price=purchase_price,
                    value=value,
                )
            )

        return StockOnDateData(
            on_date=on_date,
            branch_name=branch_name,
            show_branch_column=show_branch_column,
            currency=currency,
            rows=rows,
            total_qty=total_qty,
            total_value=total_value,
        )

    async def get_stock_on_date_xlsx(
        self,
        *,
        tenant_id: UUID,
        on_date: date,
        branch_id: UUID | None,
    ) -> bytes:
        """Render the stock-on-date workbook on the fly (no MinIO cache — live
        data, arbitrary date). An empty result yields a valid zero file."""
        data = await self.build_stock_on_date(
            tenant_id=tenant_id, on_date=on_date, branch_id=branch_id
        )
        return await anyio.to_thread.run_sync(render_stock_on_date_xlsx, data)

    @staticmethod
    def _assert_draft(sale: Sale) -> None:
        if sale.status != "draft":
            raise ConflictError(
                "Sale is no longer editable",
                details={"status": sale.status},
            )

    @staticmethod
    def _assert_sale_viewable(
        sale: Sale,
        *,
        viewer_id: UUID,
        can_view_tenant: bool,
        allowed_branch_ids: set[UUID] | None = None,
        allowed_view_branch_ids: set[UUID] | None = None,
    ) -> None:
        if can_view_tenant:
            return
        POSService._assert_branch_allowed(sale.branch_id, allowed_branch_ids=allowed_branch_ids)
        if allowed_view_branch_ids is not None and sale.branch_id in allowed_view_branch_ids:
            return
        if sale.cashier_user_id != viewer_id:
            raise PermissionDeniedError("Cannot view another cashier's sale")

    @staticmethod
    def _assert_sale_owned_or_managed(
        sale: Sale,
        *,
        actor_id: UUID | None,
        can_manage_tenant: bool,
        allowed_branch_ids: set[UUID] | None = None,
        allowed_manage_branch_ids: set[UUID] | None = None,
    ) -> None:
        POSService._assert_branch_allowed(
            sale.branch_id,
            allowed_branch_ids=allowed_branch_ids,
        )
        if can_manage_tenant:
            return
        if actor_id is None:
            return
        if allowed_manage_branch_ids is not None and sale.branch_id in allowed_manage_branch_ids:
            return
        if sale.cashier_user_id != actor_id:
            raise PermissionDeniedError("Cannot modify another cashier's sale")

    @staticmethod
    def _assert_shift_owned_or_managed(
        shift: Shift,
        *,
        actor_id: UUID,
        can_manage_tenant: bool,
        allowed_manage_branch_ids: set[UUID] | None = None,
    ) -> None:
        if can_manage_tenant:
            return
        if allowed_manage_branch_ids is not None and shift.branch_id in allowed_manage_branch_ids:
            return
        if shift.opened_by_user_id != actor_id:
            raise PermissionDeniedError("Cannot use another cashier's shift")

    @staticmethod
    def _assert_branch_allowed(
        branch_id: UUID,
        *,
        allowed_branch_ids: set[UUID] | None,
    ) -> None:
        if allowed_branch_ids is not None and branch_id not in allowed_branch_ids:
            raise PermissionDeniedError("Branch access denied")

    # ---- items ----

    async def add_item(
        self,
        *,
        sale_id: UUID,
        catalog_id: UUID,
        qty: Decimal,
        actor_id: UUID | None = None,
        can_manage_tenant: bool = False,
        allowed_branch_ids: set[UUID] | None = None,
        allowed_manage_branch_ids: set[UUID] | None = None,
        today: date | None = None,
        expired_sale_confirmed: bool = False,
    ) -> tuple[list[SaleItem], bool]:
        """Returns (created items, requires_prescription_log)."""
        sale = await self._lock_sale(sale_id)
        self._assert_sale_owned_or_managed(
            sale,
            actor_id=actor_id,
            can_manage_tenant=can_manage_tenant,
            allowed_branch_ids=allowed_branch_ids,
            allowed_manage_branch_ids=allowed_manage_branch_ids,
        )
        self._assert_draft(sale)

        catalog = await self.repo.session.get(TenantCatalog, catalog_id)
        if catalog is None or catalog.tenant_id != sale.tenant_id:
            raise NotFoundError("Catalog item not found")

        inventory = InventoryService(InventoryRepository(self.repo.session))
        selection = await inventory.find_batches_fefo(
            tenant_id=sale.tenant_id,
            catalog_id=catalog_id,
            branch_id=sale.branch_id,
            qty_needed=qty,
            today=today,
        )
        if selection.total_picked < qty:
            raise BusinessRuleError(
                "Insufficient stock for this catalog item",
                details={
                    "requested": str(qty),
                    "available": str(selection.total_picked),
                },
            )
        if selection.requires_warning and not expired_sale_confirmed:
            raise BusinessRuleError(
                "Expired stock requires cashier confirmation",
                details={"reason": "expired_sale_confirmation_required"},
            )

        created: list[SaleItem] = []
        for pick in selection.picks:
            unit_price = pick.batch.sale_price or catalog.base_price or Decimal("0")
            if unit_price <= 0:
                raise BusinessRuleError(
                    "Catalog item has no valid sale price",
                    details={"catalog_id": str(catalog_id)},
                )
            total_price = (unit_price * pick.qty).quantize(Decimal("0.01"))
            position = await self.repo.next_item_position(sale.id)
            item = await self.repo.insert_item(
                tenant_id=sale.tenant_id,
                sale_id=sale.id,
                catalog_id=catalog_id,
                batch_id=pick.batch.id,
                qty=pick.qty,
                unit_price=unit_price,
                total_price=total_price,
                position=position,
            )
            created.append(item)

        # Recompute sale total
        await self._recompute_total(sale)
        requires_rx = catalog.dispensing_type == "prescription"
        return created, requires_rx

    async def update_item(
        self,
        *,
        sale_id: UUID,
        item_id: UUID,
        qty: Decimal,
        actor_id: UUID | None = None,
        can_manage_tenant: bool = False,
        allowed_branch_ids: set[UUID] | None = None,
        allowed_manage_branch_ids: set[UUID] | None = None,
    ) -> SaleItem:
        sale = await self._lock_sale(sale_id)
        self._assert_sale_owned_or_managed(
            sale,
            actor_id=actor_id,
            can_manage_tenant=can_manage_tenant,
            allowed_branch_ids=allowed_branch_ids,
            allowed_manage_branch_ids=allowed_manage_branch_ids,
        )
        self._assert_draft(sale)
        item = await self.repo.get_item(item_id)
        if item is None or item.sale_id != sale_id:
            raise NotFoundError("Sale item not found")
        total_price = (item.unit_price * qty).quantize(Decimal("0.01"))
        updated = await self.repo.update_item(item, qty=qty, total_price=total_price)
        await self._recompute_total(sale)
        return updated

    async def delete_item(
        self,
        *,
        sale_id: UUID,
        item_id: UUID,
        actor_id: UUID | None = None,
        can_manage_tenant: bool = False,
        allowed_branch_ids: set[UUID] | None = None,
        allowed_manage_branch_ids: set[UUID] | None = None,
    ) -> None:
        sale = await self._lock_sale(sale_id)
        self._assert_sale_owned_or_managed(
            sale,
            actor_id=actor_id,
            can_manage_tenant=can_manage_tenant,
            allowed_branch_ids=allowed_branch_ids,
            allowed_manage_branch_ids=allowed_manage_branch_ids,
        )
        self._assert_draft(sale)
        rows = await self.repo.delete_item(item_id, sale_id=sale_id)
        if rows == 0:
            raise NotFoundError("Sale item not found")
        await self._recompute_total(sale)

    async def _recompute_total(self, sale: Sale) -> None:
        items = await self.repo.list_items(sale.id)
        total = sum((i.total_price for i in items), Decimal("0"))
        await self.repo.update_sale(sale, total_amount=total)

    # ---- payments ----

    @staticmethod
    def _payment_operation_hash(
        *,
        sale_id: UUID,
        payment_method: str,
        amount: Decimal,
        metadata: dict[str, Any] | None,
    ) -> str:
        payload = {
            "sale_id": str(sale_id),
            "payment_method": payment_method,
            "amount": format(amount.normalize(), "f"),
            "metadata": metadata,
        }
        try:
            canonical = json.dumps(
                payload,
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise BusinessRuleError("Payment metadata must be valid JSON") from exc
        return hashlib.sha256(canonical).hexdigest()

    async def _find_existing_payment(
        self,
        *,
        sale: Sale,
        operation_id: UUID,
        operation_hash: str,
    ) -> SalePayment | None:
        existing_sale = await self.repo.get_sale_by_operation_id(
            tenant_id=sale.tenant_id,
            operation_id=operation_id,
        )
        if existing_sale is not None:
            raise ConflictError("Operation ID was already used for another POS operation")

        existing = await self.repo.get_payment_by_operation_id(
            tenant_id=sale.tenant_id,
            operation_id=operation_id,
        )
        if existing is None:
            return None
        if existing.sale_id != sale.id or existing.operation_hash != operation_hash:
            raise ConflictError("Operation ID was already used for another payment")
        return existing

    async def add_payment(
        self,
        *,
        sale_id: UUID,
        payment_method: str,
        amount: Decimal,
        operation_id: UUID | None = None,
        actor_id: UUID | None = None,
        can_manage_tenant: bool = False,
        allowed_branch_ids: set[UUID] | None = None,
        allowed_manage_branch_ids: set[UUID] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SalePayment:
        if amount <= 0:
            raise BusinessRuleError("Payment amount must be positive")
        self._validate_payment_metadata(
            payment_method=payment_method,
            amount=amount,
            metadata=metadata,
        )
        effective_operation_id = operation_id or uuid4()
        operation_hash = self._payment_operation_hash(
            sale_id=sale_id,
            payment_method=payment_method,
            amount=amount,
            metadata=metadata,
        )
        await self.repo.lock_operation_id(effective_operation_id)

        sale = await self._lock_sale(sale_id)
        self._assert_sale_owned_or_managed(
            sale,
            actor_id=actor_id,
            can_manage_tenant=can_manage_tenant,
            allowed_branch_ids=allowed_branch_ids,
            allowed_manage_branch_ids=allowed_manage_branch_ids,
        )
        existing = await self._find_existing_payment(
            sale=sale,
            operation_id=effective_operation_id,
            operation_hash=operation_hash,
        )
        if existing is not None:
            return existing
        await self._validate_pos_payment_configuration(
            tenant_id=sale.tenant_id,
            requested_methods={payment_method},
            existing_methods=await self.repo.payment_methods(sale.id),
        )
        self._assert_draft(sale)
        paid_total = await self.repo.payments_total(sale.id)
        if paid_total + amount > sale.total_amount:
            raise BusinessRuleError(
                "Payment exceeds sale total",
                details={
                    "paid": str(paid_total),
                    "attempted": str(amount),
                    "total": str(sale.total_amount),
                },
            )
        return await self.repo.insert_payment(
            tenant_id=sale.tenant_id,
            sale_id=sale.id,
            payment_method=payment_method,
            amount=amount,
            operation_id=effective_operation_id,
            operation_hash=operation_hash,
            metadata_json=metadata,
        )

    # ---- prescription log ----

    async def add_prescription(
        self,
        *,
        sale_id: UUID,
        fields: dict[str, Any],
        actor_id: UUID | None,
        can_manage_tenant: bool = False,
        allowed_branch_ids: set[UUID] | None = None,
        allowed_manage_branch_ids: set[UUID] | None = None,
    ) -> PrescriptionLog:
        sale = await self._lock_sale(sale_id)
        self._assert_sale_owned_or_managed(
            sale,
            actor_id=actor_id,
            can_manage_tenant=can_manage_tenant,
            allowed_branch_ids=allowed_branch_ids,
            allowed_manage_branch_ids=allowed_manage_branch_ids,
        )
        self._assert_draft(sale)
        allowed_fields = {
            "sale_item_id",
            "prescription_number",
            "doctor_name",
            "doctor_license",
            "patient_name",
            "notes",
        }
        if unknown_fields := set(fields) - allowed_fields:
            raise BusinessRuleError(
                "Unsupported prescription fields",
                details={"fields": sorted(unknown_fields)},
            )

        clean_fields = dict(fields)
        text_fields = allowed_fields - {"sale_item_id"}
        for field_name in text_fields:
            value = clean_fields.get(field_name)
            if value is None:
                continue
            if not isinstance(value, str):
                raise BusinessRuleError("Prescription details must be text")
            clean_fields[field_name] = value.strip() or None
        if not any(clean_fields.get(field_name) for field_name in text_fields):
            raise BusinessRuleError("At least one prescription detail is required")

        sale_items = await self.repo.list_items(sale.id)
        item_by_id = {item.id: item for item in sale_items}
        requested_item_id = clean_fields.get("sale_item_id")
        if requested_item_id is not None and requested_item_id not in item_by_id:
            raise NotFoundError("Sale item not found in this sale")

        rx_item_ids: set[UUID] = set()
        for item in sale_items:
            catalog = await self.repo.session.get(TenantCatalog, item.catalog_id)
            if catalog is not None and catalog.dispensing_type == "prescription":
                rx_item_ids.add(item.id)
        if not rx_item_ids:
            raise BusinessRuleError("Prescription log is not applicable to this sale")
        if requested_item_id is not None and requested_item_id not in rx_item_ids:
            raise BusinessRuleError("Prescription log must reference a prescription item")

        payload = {
            **clean_fields,
            "tenant_id": sale.tenant_id,
            "sale_id": sale.id,
            "created_by": actor_id,
        }
        return await self.repo.insert_prescription(**payload)

    # =========================================================================
    # Complete — the critical path
    # =========================================================================

    async def _consume_sale_batches(
        self,
        sale: Sale,
        items: list[SaleItem],
        *,
        expired_sale_confirmed: bool,
    ) -> None:
        settings = await FoundationRepository(self.repo.session).get_settings_for_pos(
            sale.tenant_id
        )
        if settings is None:
            raise AurumError("POS settings are unavailable")
        try:
            local_today = self._now().astimezone(ZoneInfo(settings.report_timezone)).date()
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise AurumError("Tenant report timezone is invalid") from exc

        # FEFO normally creates one line per batch, but quantity edits may
        # leave multiple lines referencing the same batch.
        per_batch: dict[UUID, Decimal] = {}
        for item in items:
            per_batch[item.batch_id] = per_batch.get(item.batch_id, Decimal("0")) + item.qty

        inv_repo = InventoryRepository(self.repo.session)
        for batch_id in sorted(per_batch, key=str):
            qty = per_batch[batch_id]
            locked = await self.repo.lock_batch(batch_id)
            if locked is None:
                raise NotFoundError("Batch disappeared mid-checkout")
            if locked.is_blocked:
                raise BusinessRuleError(
                    "Batch is blocked at checkout",
                    details={"batch_id": str(batch_id)},
                )
            if settings.expired_sale_mode == "strict" and locked.expires_at <= local_today:
                raise BusinessRuleError(
                    "Expired batch cannot be sold",
                    details={"batch_id": str(batch_id), "expires_at": str(locked.expires_at)},
                )
            if (
                settings.expired_sale_mode == "warning"
                and locked.expires_at <= local_today
                and not expired_sale_confirmed
            ):
                raise BusinessRuleError(
                    "Expired stock requires cashier confirmation",
                    details={
                        "reason": "expired_sale_confirmation_required",
                        "batch_id": str(batch_id),
                        "expires_at": str(locked.expires_at),
                    },
                )
            if locked.qty_remaining < qty:
                raise BusinessRuleError(
                    "Insufficient stock at checkout",
                    details={
                        "batch_id": str(batch_id),
                        "available": str(locked.qty_remaining),
                        "needed": str(qty),
                    },
                )
            if sale.is_test:
                continue
            try:
                await inv_repo.insert_movement(
                    tenant_id=sale.tenant_id,
                    batch_id=batch_id,
                    movement_type="sale",
                    qty_delta=-qty,
                    source_table="sale",
                    source_id=sale.id,
                    operation_key=f"pos:sale:{sale.id}:sale:{batch_id}",
                )
            except (IntegrityError, InternalError) as exc:
                # The trigger and CHECK remain the final barrier if stock
                # changes after the explicit lock-time validation.
                if "qty_remaining" in str(exc).lower():
                    raise BusinessRuleError(
                        "Insufficient stock at checkout",
                        details={"batch_id": str(batch_id)},
                    ) from exc
                raise
            await self.repo.session.refresh(locked)

    async def complete(
        self,
        *,
        sale_id: UUID,
        actor_id: UUID | None = None,
        can_manage_tenant: bool = False,
        allowed_branch_ids: set[UUID] | None = None,
        allowed_manage_branch_ids: set[UUID] | None = None,
        expired_sale_confirmed: bool = False,
    ) -> Sale:
        sale = await self._lock_sale(sale_id)
        self._assert_sale_owned_or_managed(
            sale,
            actor_id=actor_id,
            can_manage_tenant=can_manage_tenant,
            allowed_branch_ids=allowed_branch_ids,
            allowed_manage_branch_ids=allowed_manage_branch_ids,
        )
        if sale.status in {"completed", "voided"} and sale.receipt_number is not None:
            return sale
        self._assert_draft(sale)

        await self._lock_open_shift(
            sale.shift_id,
            error_message="Cannot complete a sale in a closed shift",
        )

        items = await self.repo.list_items(sale.id)
        if not items:
            raise BusinessRuleError("Cannot complete a sale with no items")
        if sale.total_amount <= 0:
            raise BusinessRuleError("Cannot complete a sale with a non-positive total")

        # Payment check
        paid = await self.repo.payments_total(sale.id)
        if paid != sale.total_amount:
            raise BusinessRuleError(
                "Payment total does not match sale total",
                details={"paid": str(paid), "total": str(sale.total_amount)},
            )

        # Prescription check
        await self._assert_prescriptions_present(sale, items)
        await self._consume_sale_batches(
            sale,
            items,
            expired_sale_confirmed=expired_sale_confirmed,
        )

        receipt_seq, receipt = await self._allocate_receipt(sale.register_id)
        completed_at = self._now()
        receipt_snapshot = await self._compose_receipt_data(
            sale=sale,
            items=items,
            payments=await self.repo.list_payments(sale.id),
            status="completed",
            receipt_number=receipt,
            receipt_datetime=completed_at,
            total_amount=sale.total_amount,
        )
        completed = await self.repo.update_sale(
            sale,
            status="completed",
            completed_at=completed_at,
            receipt_number=receipt,
            receipt_seq=receipt_seq,
            receipt_snapshot=receipt_snapshot.model_dump(mode="json"),
        )
        logger.info(
            "sale_completed",
            sale_id=str(sale.id),
            is_test=sale.is_test,
        )
        return completed

    async def _assert_prescriptions_present(self, sale: Sale, items: list[SaleItem]) -> None:
        # For each sale_item whose catalog is dispensing_type='prescription'
        # there must be at least one prescription_log row referencing the
        # sale (the sale_item_id field stays optional in case the cashier
        # logs the script once for the whole receipt).
        rx_items: list[SaleItem] = []
        for item in items:
            catalog = await self.repo.session.get(TenantCatalog, item.catalog_id)
            if catalog is not None and catalog.dispensing_type == "prescription":
                rx_items.append(item)
        if not rx_items:
            return
        logs = await self.repo.list_prescriptions(sale.id)
        if not logs:
            raise BusinessRuleError(
                "Prescription log required before completing a Rx sale",
                details={"rx_items": [str(i.id) for i in rx_items]},
            )

    # =========================================================================
    # Refund
    # =========================================================================

    @staticmethod
    def _refund_operation_hash(
        *,
        parent_sale_id: UUID,
        items: dict[UUID, Decimal],
        external_refund_confirmed: bool,
    ) -> str:
        payload = {
            "kind": "refund_financial_command_v2",
            "parent_sale_id": str(parent_sale_id),
            "items": [
                [str(item_id), format(qty.normalize(), "f")]
                for item_id, qty in sorted(items.items(), key=lambda pair: str(pair[0]))
            ],
            "external_refund_confirmed": external_refund_confirmed,
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @staticmethod
    def _aggregate_refund_items(items: list[tuple[UUID, Decimal]]) -> dict[UUID, Decimal]:
        per_item: dict[UUID, Decimal] = {}
        for item_id, qty in items:
            if qty <= 0:
                raise BusinessRuleError("Refund quantity must be positive")
            per_item[item_id] = per_item.get(item_id, Decimal("0")) + qty
        if not per_item:
            raise BusinessRuleError("Refund must contain at least one item")
        return per_item

    @staticmethod
    def _refund_payment_allocations(
        *,
        refund_total: Decimal,
        original_payments: list[SalePayment],
        already_refunded: Mapping[str, Decimal],
    ) -> list[tuple[str, Decimal]]:
        per_method: dict[str, Decimal] = {}
        for payment in original_payments:
            if payment.amount > 0:
                per_method[payment.payment_method] = (
                    per_method.get(payment.payment_method, Decimal("0")) + payment.amount
                )
        source_total = sum(per_method.values(), Decimal("0"))
        if source_total <= 0:
            raise BusinessRuleError("Original sale has no valid payment allocation")

        quantum = Decimal("0.01")
        methods = list(per_method)
        remaining = {
            method: max(
                Decimal("0"),
                per_method[method] - already_refunded.get(method, Decimal("0")),
            )
            for method in methods
        }
        remaining_total = sum(remaining.values(), Decimal("0"))
        if refund_total > remaining_total:
            raise BusinessRuleError("Refund exceeds the remaining original payment allocation")
        if refund_total == remaining_total:
            return [(method, remaining[method]) for method in methods if remaining[method] > 0]

        exact = {method: refund_total * per_method[method] / source_total for method in methods}
        allocated = {
            method: min(
                remaining[method],
                exact[method].quantize(quantum, rounding=ROUND_DOWN),
            )
            for method in methods
        }
        missing_cents = int(
            ((refund_total - sum(allocated.values(), Decimal("0"))) / quantum).to_integral_value()
        )
        remainder_order = sorted(
            methods,
            key=lambda method: (
                exact[method] - allocated[method],
                remaining[method] - allocated[method],
            ),
            reverse=True,
        )
        for _index in range(missing_cents):
            method = next(
                (
                    candidate
                    for candidate in remainder_order
                    if allocated[candidate] + quantum <= remaining[candidate]
                ),
                None,
            )
            if method is None:
                raise AurumError("Unable to allocate refund across original payment methods")
            allocated[method] += quantum

        return [(method, allocated[method]) for method in methods if allocated[method] > 0]

    async def _find_existing_refund(
        self,
        *,
        parent: Sale,
        operation_id: UUID,
        operation_hash: str,
    ) -> Sale | None:
        existing_payment = await self.repo.get_payment_by_operation_id(
            tenant_id=parent.tenant_id,
            operation_id=operation_id,
        )
        if existing_payment is not None:
            raise ConflictError("Operation ID was already used for another POS operation")

        existing = await self.repo.get_sale_by_operation_id(
            tenant_id=parent.tenant_id,
            operation_id=operation_id,
        )
        if existing is None:
            return None
        if (
            existing.sale_type != "return"
            or existing.parent_sale_id != parent.id
            or existing.operation_hash != operation_hash
        ):
            raise ConflictError("Operation ID was already used for another refund")
        return existing

    async def get_refund_result(
        self,
        *,
        tenant_id: UUID,
        operation_id: UUID,
        allowed_branch_ids: set[UUID] | None = None,
    ) -> Sale:
        """Return a committed refund after a client lost the POST response."""
        await self.repo.lock_operation_id(operation_id)
        if (
            await self.repo.get_payment_by_operation_id(
                tenant_id=tenant_id,
                operation_id=operation_id,
            )
            is not None
        ):
            raise NotFoundError("Refund operation not found")

        sale = await self.repo.get_sale_by_operation_id(
            tenant_id=tenant_id,
            operation_id=operation_id,
        )
        if sale is None or sale.sale_type != "return":
            raise NotFoundError("Refund operation not found")
        self._assert_branch_allowed(sale.branch_id, allowed_branch_ids=allowed_branch_ids)
        if (
            sale.status != "completed"
            or sale.parent_sale_id is None
            or sale.receipt_number is None
            or sale.completed_at is None
        ):
            raise AurumError("Refund sale aggregate is incomplete")
        return sale

    async def _validate_refund_items(
        self,
        *,
        parent_sale_id: UUID,
        per_item: dict[UUID, Decimal],
    ) -> tuple[
        dict[UUID, SaleItem],
        dict[UUID, tuple[Decimal, Decimal, Decimal]],
    ]:
        parent_items = {i.id: i for i in await self.repo.list_items(parent_sale_id)}
        already_refunded = await self.repo.refunded_line_totals(parent_sale_id)
        for item_id, qty in per_item.items():
            if item_id not in parent_items:
                raise NotFoundError("Sale item not found in this sale")
            refunded_qty = already_refunded.get(
                item_id,
                (Decimal("0"), Decimal("0"), Decimal("0")),
            )[0]
            available = parent_items[item_id].qty - refunded_qty
            if qty > available:
                raise BusinessRuleError(
                    "Refund quantity exceeds what's left on this line",
                    details={
                        "sale_item_id": str(item_id),
                        "requested": str(qty),
                        "available": str(available),
                    },
                )
        return parent_items, already_refunded

    @staticmethod
    def _refund_line_amounts(
        *,
        parent_item: SaleItem,
        qty: Decimal,
        already_refunded: tuple[Decimal, Decimal, Decimal] | None,
    ) -> tuple[Decimal, Decimal]:
        refunded_qty, refunded_total, refunded_discount = already_refunded or (
            Decimal("0"),
            Decimal("0"),
            Decimal("0"),
        )
        remaining_qty = parent_item.qty - refunded_qty
        remaining_total = max(
            Decimal("0"),
            parent_item.total_price - refunded_total,
        )
        remaining_discount = max(
            Decimal("0"),
            parent_item.discount_amount - refunded_discount,
        )
        if qty == remaining_qty:
            return remaining_total, remaining_discount

        quantum = Decimal("0.01")
        total = (parent_item.total_price * qty / parent_item.qty).quantize(
            quantum,
            rounding=ROUND_HALF_UP,
        )
        discount = (parent_item.discount_amount * qty / parent_item.qty).quantize(
            quantum,
            rounding=ROUND_HALF_UP,
        )
        return (
            max(Decimal("0"), min(total, remaining_total)),
            max(Decimal("0"), min(discount, remaining_discount)),
        )

    async def _insert_refund_items_and_movements(
        self,
        *,
        parent: Sale,
        return_sale: Sale,
        parent_items: dict[UUID, SaleItem],
        per_item: dict[UUID, Decimal],
        already_refunded: dict[UUID, tuple[Decimal, Decimal, Decimal]],
    ) -> Decimal:
        total = Decimal("0")
        returned_by_batch: dict[UUID, Decimal] = {}
        ordered_items = sorted(
            per_item.items(),
            key=lambda pair: parent_items[pair[0]].position,
        )
        for item_id, qty in ordered_items:
            parent_item = parent_items[item_id]
            total_price, discount_amount = self._refund_line_amounts(
                parent_item=parent_item,
                qty=qty,
                already_refunded=already_refunded.get(item_id),
            )
            position = await self.repo.next_item_position(return_sale.id)
            await self.repo.insert_item(
                tenant_id=parent.tenant_id,
                sale_id=return_sale.id,
                parent_sale_item_id=parent_item.id,
                catalog_id=parent_item.catalog_id,
                batch_id=parent_item.batch_id,
                qty=qty,
                unit_price=parent_item.unit_price,
                total_price=total_price,
                discount_amount=discount_amount,
                position=position,
            )
            total += total_price
            returned_by_batch[parent_item.batch_id] = (
                returned_by_batch.get(parent_item.batch_id, Decimal("0")) + qty
            )

        if parent.is_test:
            return total

        inv_repo = InventoryRepository(self.repo.session)
        for batch_id in sorted(returned_by_batch, key=str):
            await inv_repo.insert_movement(
                tenant_id=parent.tenant_id,
                batch_id=batch_id,
                movement_type="sale_return",
                qty_delta=returned_by_batch[batch_id],
                source_table="sale",
                source_id=return_sale.id,
                operation_key=f"pos:sale:{return_sale.id}:return:{batch_id}",
            )
        return total

    async def refund(
        self,
        *,
        parent_sale_id: UUID,
        items: list[tuple[UUID, Decimal]],
        reason: str | None,
        comment: str | None,
        cashier_user_id: UUID,
        operation_id: UUID | None = None,
        external_refund_confirmed: bool = False,
        can_manage_tenant: bool = False,
        allowed_branch_ids: set[UUID] | None = None,
        allowed_manage_branch_ids: set[UUID] | None = None,
    ) -> Sale:
        """Create a `sale_type='return'` document tied to `parent_sale_id`.

        `items` is a list of (sale_item_id, qty). Each item must:
          - belong to the parent sale,
          - have qty <= original item qty - already refunded.

        The parent row remains immutable. A fully refunded display state is
        derived from completed linked return documents.
        """
        per_item = self._aggregate_refund_items(items)
        normalized_reason = reason.strip() if reason is not None else None
        normalized_comment = comment.strip() if comment is not None else None
        normalized_reason = normalized_reason or None
        normalized_comment = normalized_comment or None
        effective_operation_id = operation_id or uuid4()
        operation_hash = self._refund_operation_hash(
            parent_sale_id=parent_sale_id,
            items=per_item,
            external_refund_confirmed=external_refund_confirmed,
        )
        await self.repo.lock_operation_id(effective_operation_id)

        parent = await self._lock_sale(parent_sale_id)
        self._assert_branch_allowed(parent.branch_id, allowed_branch_ids=allowed_branch_ids)
        existing = await self._find_existing_refund(
            parent=parent,
            operation_id=effective_operation_id,
            operation_hash=operation_hash,
        )
        if existing is not None:
            return existing

        if parent.status != "completed":
            raise BusinessRuleError(
                "Can only refund a completed sale",
                details={"status": parent.status},
            )
        if parent.sale_type != "sale":
            raise BusinessRuleError("Only forward sales can be refunded")

        settings = await FoundationRepository(self.repo.session).get_settings_for_pos(
            parent.tenant_id
        )
        if settings is None:
            raise AurumError("POS settings are unavailable")
        if settings.refund_reason_mode == "off" and (
            normalized_reason is not None or normalized_comment is not None
        ):
            raise BusinessRuleError("Refund reason fields are disabled")
        if settings.refund_reason_mode in {"required", "required_with_text"} and (
            normalized_reason is None
        ):
            raise BusinessRuleError("Refund reason is required")
        if settings.refund_reason_mode == "required_with_text" and normalized_comment is None:
            raise BusinessRuleError("Refund comment is required")

        parent_items, already_refunded = await self._validate_refund_items(
            parent_sale_id=parent_sale_id,
            per_item=per_item,
        )
        original_payments = await self.repo.list_payments(parent.id)
        refund_total = sum(
            (
                self._refund_line_amounts(
                    parent_item=parent_items[item_id],
                    qty=qty,
                    already_refunded=already_refunded.get(item_id),
                )[0]
                for item_id, qty in per_item.items()
            ),
            Decimal("0"),
        )
        if refund_total <= 0:
            raise BusinessRuleError("Refund total must be positive")
        payment_allocations = self._refund_payment_allocations(
            refund_total=refund_total,
            original_payments=original_payments,
            already_refunded=await self.repo.refunded_payment_totals(parent.id),
        )
        requires_external_refund = any(method != "cash" for method, _amount in payment_allocations)
        if requires_external_refund and not external_refund_confirmed:
            raise BusinessRuleError("Non-cash refund must be confirmed in the external terminal")
        if not requires_external_refund and external_refund_confirmed:
            raise BusinessRuleError(
                "External terminal confirmation is valid only for non-cash refunds"
            )

        # The original shift may be closed days ago. Book the refund in the
        # currently open shift of the same register for correct accountability.
        shift = await self.repo.lock_open_shift_for_register(parent.register_id)
        if shift is None:
            raise BusinessRuleError("Refunds require an open shift on the original register")
        self._assert_shift_owned_or_managed(
            shift,
            actor_id=cashier_user_id,
            can_manage_tenant=can_manage_tenant,
            allowed_manage_branch_ids=allowed_manage_branch_ids,
        )
        return_sale = await self.repo.create_sale(
            tenant_id=parent.tenant_id,
            branch_id=parent.branch_id,
            register_id=parent.register_id,
            shift_id=shift.id,
            sale_type="return",
            parent_sale_id=parent.id,
            cashier_user_id=cashier_user_id,
            status="draft",
            is_test=parent.is_test,
            operation_id=effective_operation_id,
            operation_hash=operation_hash,
        )

        total = await self._insert_refund_items_and_movements(
            parent=parent,
            return_sale=return_sale,
            parent_items=parent_items,
            per_item=per_item,
            already_refunded=already_refunded,
        )

        # Preserve the original tender mix. A card/QR sale must never silently
        # become a cash refund in accounting.
        for payment_method, amount in payment_allocations:
            await self.repo.insert_payment(
                tenant_id=parent.tenant_id,
                sale_id=return_sale.id,
                payment_method=payment_method,
                amount=amount,
                metadata_json={
                    "reason": normalized_reason,
                    "comment": normalized_comment,
                    "external_refund_confirmed": external_refund_confirmed,
                },
            )
        receipt_seq, receipt = await self._allocate_receipt(parent.register_id)
        completed_at = self._now()
        receipt_snapshot = await self._compose_receipt_data(
            sale=return_sale,
            items=await self.repo.list_items(return_sale.id),
            payments=await self.repo.list_payments(return_sale.id),
            status="completed",
            receipt_number=receipt,
            receipt_datetime=completed_at,
            total_amount=total,
        )
        await self.repo.update_sale(
            return_sale,
            status="completed",
            completed_at=completed_at,
            receipt_number=receipt,
            receipt_seq=receipt_seq,
            total_amount=total,
            receipt_snapshot=receipt_snapshot.model_dump(mode="json"),
        )

        # Keep the finalized parent row unchanged. The read model derives the
        # "voided" state once completed returns cover every original line.
        full = self._is_fully_refunded(parent_items, already_refunded, per_item)

        logger.info(
            "refund_completed",
            return_sale_id=str(return_sale.id),
            parent_sale_id=str(parent.id),
            full=full,
        )
        return return_sale

    @staticmethod
    def _is_fully_refunded(
        parent_items: dict[UUID, SaleItem],
        already_refunded: dict[UUID, tuple[Decimal, Decimal, Decimal]],
        this_refund: dict[UUID, Decimal],
    ) -> bool:
        for pid, item in parent_items.items():
            refunded_qty = already_refunded.get(
                pid,
                (Decimal("0"), Decimal("0"), Decimal("0")),
            )[0]
            done = refunded_qty + this_refund.get(pid, Decimal("0"))
            if done < item.qty:
                return False
        return True


# Kept for static analysers
_ = FoundationRepository
