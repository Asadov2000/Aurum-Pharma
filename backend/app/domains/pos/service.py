"""Business logic for POS — shifts, sales, complete (FEFO + SELECT FOR UPDATE),
refunds with parent voiding, prescription requirement.

Critical invariants:
- A completed (or voided) sale is IMMUTABLE — any mutating method asserts
  `_assert_draft` first.
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
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import anyio
import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, InternalError

from app.core.errors import (
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
    ReceiptData,
    ReceiptLine,
    ReceiptPayment,
    SalesSummaryData,
    SalesSummaryRow,
    StockOnDateData,
    StockRow,
    ZReportData,
    ZReportPaymentBreakdown,
)
from app.domains.pos.stock_on_date_xlsx import render_stock_on_date_xlsx
from app.domains.pos.z_report_xlsx import get_or_render_z_report_xlsx

logger = structlog.get_logger("pos.service")


class POSService:
    def __init__(self, repo: POSRepository) -> None:
        self.repo = repo

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
        allowed_branch_ids: set[UUID] | None = None,
    ) -> Shift | None:
        shift = await self.repo.get_open_shift_for_user(user_id, register_id)
        if shift is not None:
            self._assert_branch_allowed(shift.branch_id, allowed_branch_ids=allowed_branch_ids)
        return shift

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
        totals = await self.repo.shift_totals(shift_id)
        cash_in = Decimal(totals.get("cash", "0"))
        expected = shift.opening_cash + cash_in
        diff = closing_cash_actual - expected
        return await self.repo.update_shift(
            shift,
            status="closed",
            closed_at=utc_now(),
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
        shift = await self.repo.get_shift(shift_id)
        if shift is None:
            raise NotFoundError("Shift not found")
        self._assert_branch_allowed(shift.branch_id, allowed_branch_ids=allowed_branch_ids)
        totals = shift.totals or await self.repo.shift_totals(shift_id)
        return {
            "shift_id": shift.id,
            "opened_at": shift.opened_at,
            "closed_at": shift.closed_at,
            "register_id": shift.register_id,
            "cashier_user_id": shift.opened_by_user_id,
            "opening_cash": shift.opening_cash,
            "closing_cash_actual": shift.closing_cash_actual,
            "closing_cash_expected": shift.closing_cash_expected,
            "closing_difference": shift.closing_difference,
            "totals": totals,
            "sales_count": int(totals.get("sales_count", 0)),
            "returns_count": int(totals.get("returns_count", 0)),
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
        allowed_branch_ids: set[UUID] | None = None,
    ) -> Sale:
        shift = await self.repo.lock_open_shift_for_register(register_id)
        if shift is None:
            raise BusinessRuleError("No open shift for this register")
        self._assert_branch_allowed(shift.branch_id, allowed_branch_ids=allowed_branch_ids)
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
        )

    async def get_sale(self, sale_id: UUID) -> Sale:
        sale = await self.repo.get_sale(sale_id)
        if sale is None:
            raise NotFoundError("Sale not found")
        return sale

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
        items = await self.repo.list_items(sale.id)
        payments = await self.repo.list_payments(sale.id)

        tenant = await self.repo.session.get(Tenant, sale.tenant_id)
        branch = await self.repo.session.get(Branch, sale.branch_id)
        cashier_name = await self.repo.get_user_display_name(sale.cashier_user_id)

        lines: list[ReceiptLine] = []
        for it in items:
            catalog = await self.repo.session.get(TenantCatalog, it.catalog_id)
            lines.append(
                ReceiptLine(
                    position=it.position,
                    name=catalog.brand_name if catalog is not None else str(it.catalog_id),
                    qty=it.qty,
                    unit_price=it.unit_price,
                    discount_amount=it.discount_amount,
                    total_price=it.total_price,
                )
            )

        discount_total = sum((it.discount_amount for it in items), Decimal("0"))
        paid_total = sum((p.amount for p in payments), Decimal("0"))
        change = paid_total - sale.total_amount
        if change < 0:
            change = Decimal("0")

        return ReceiptData(
            sale_id=sale.id,
            is_refund=sale.sale_type == "return",
            status=sale.status,
            pharmacy_name=tenant.name if tenant is not None else "",
            branch_name=branch.name if branch is not None else "",
            branch_address=branch.address if branch is not None else None,
            branch_license=branch.license_number if branch is not None else None,
            receipt_number=sale.receipt_number,
            datetime=sale.completed_at or sale.created_at,
            cashier_name=cashier_name,
            items=lines,
            discount_total=discount_total,
            total=sale.total_amount,
            currency=sale.currency,
            payments=[ReceiptPayment(method=p.payment_method, amount=p.amount) for p in payments],
            paid_total=paid_total,
            change=change,
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
            register_name=register.name if register is not None else "",
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
        if can_manage_tenant:
            return
        POSService._assert_branch_allowed(sale.branch_id, allowed_branch_ids=allowed_branch_ids)
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
            raise PermissionDeniedError("Cannot close another cashier's shift")

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

        created: list[SaleItem] = []
        for pick in selection.picks:
            unit_price = pick.batch.sale_price or catalog.base_price or Decimal("0")
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

    async def add_payment(
        self,
        *,
        sale_id: UUID,
        payment_method: str,
        amount: Decimal,
        actor_id: UUID | None = None,
        can_manage_tenant: bool = False,
        allowed_branch_ids: set[UUID] | None = None,
        allowed_manage_branch_ids: set[UUID] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SalePayment:
        sale = await self._lock_sale(sale_id)
        self._assert_sale_owned_or_managed(
            sale,
            actor_id=actor_id,
            can_manage_tenant=can_manage_tenant,
            allowed_branch_ids=allowed_branch_ids,
            allowed_manage_branch_ids=allowed_manage_branch_ids,
        )
        self._assert_draft(sale)
        return await self.repo.insert_payment(
            tenant_id=sale.tenant_id,
            sale_id=sale.id,
            payment_method=payment_method,
            amount=amount,
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
        payload = {
            **fields,
            "tenant_id": sale.tenant_id,
            "sale_id": sale.id,
            "created_by": actor_id,
        }
        return await self.repo.insert_prescription(**payload)

    # =========================================================================
    # Complete — the critical path
    # =========================================================================

    async def complete(
        self,
        *,
        sale_id: UUID,
        actor_id: UUID | None = None,
        can_manage_tenant: bool = False,
        allowed_branch_ids: set[UUID] | None = None,
        allowed_manage_branch_ids: set[UUID] | None = None,
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

        # Payment check
        paid = await self.repo.payments_total(sale.id)
        if paid < sale.total_amount:
            raise BusinessRuleError(
                "Insufficient payment",
                details={"paid": str(paid), "total": str(sale.total_amount)},
            )

        # Prescription check
        await self._assert_prescriptions_present(sale, items)

        # Real sales (not test): lock and write off each batch
        if not sale.is_test:
            inv_repo = InventoryRepository(self.repo.session)
            # Aggregate qty per batch (one item per batch is the FEFO output,
            # but PATCH-edits could create two rows on the same batch).
            per_batch: dict[UUID, Decimal] = {}
            for item in items:
                per_batch[item.batch_id] = per_batch.get(item.batch_id, Decimal("0")) + item.qty

            for batch_id in sorted(per_batch, key=str):
                qty = per_batch[batch_id]
                locked = await self.repo.lock_batch(batch_id)
                if locked is None:
                    raise NotFoundError("Batch disappeared mid-checkout")
                if locked.qty_remaining < qty:
                    raise BusinessRuleError(
                        "Insufficient stock at checkout",
                        details={
                            "batch_id": str(batch_id),
                            "available": str(locked.qty_remaining),
                            "needed": str(qty),
                        },
                    )
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
                    # Belt-and-braces: trigger / CHECK will refuse a negative
                    # qty even if the FOR UPDATE check above passed.
                    msg = str(exc).lower()
                    if "qty_remaining" in msg:
                        raise BusinessRuleError(
                            "Insufficient stock at checkout",
                            details={"batch_id": str(batch_id)},
                        ) from exc
                    raise
                await self.repo.session.refresh(locked)

        receipt_seq, receipt = await self._allocate_receipt(sale.register_id)
        completed = await self.repo.update_sale(
            sale,
            status="completed",
            completed_at=utc_now(),
            receipt_number=receipt,
            receipt_seq=receipt_seq,
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
        reason: str | None,
        comment: str | None,
    ) -> str:
        payload = {
            "parent_sale_id": str(parent_sale_id),
            "items": [
                [str(item_id), format(qty.normalize(), "f")]
                for item_id, qty in sorted(items.items(), key=lambda pair: str(pair[0]))
            ],
            "reason": reason,
            "comment": comment,
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

    async def _find_existing_refund(
        self,
        *,
        parent: Sale,
        operation_id: UUID,
        operation_hash: str,
    ) -> Sale | None:
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

    async def _validate_refund_items(
        self,
        *,
        parent_sale_id: UUID,
        per_item: dict[UUID, Decimal],
    ) -> tuple[dict[UUID, SaleItem], dict[UUID, Decimal]]:
        parent_items = {i.id: i for i in await self.repo.list_items(parent_sale_id)}
        already_refunded = await self._already_refunded(parent_sale_id)
        for item_id, qty in per_item.items():
            if item_id not in parent_items:
                raise NotFoundError("Sale item not found in this sale")
            available = parent_items[item_id].qty - already_refunded.get(item_id, Decimal("0"))
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

    async def _insert_refund_items_and_movements(
        self,
        *,
        parent: Sale,
        return_sale: Sale,
        parent_items: dict[UUID, SaleItem],
        per_item: dict[UUID, Decimal],
    ) -> Decimal:
        total = Decimal("0")
        returned_by_batch: dict[UUID, Decimal] = {}
        ordered_items = sorted(
            per_item.items(),
            key=lambda pair: parent_items[pair[0]].position,
        )
        for item_id, qty in ordered_items:
            parent_item = parent_items[item_id]
            total_price = (parent_item.unit_price * qty).quantize(Decimal("0.01"))
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
        allowed_branch_ids: set[UUID] | None = None,
    ) -> Sale:
        """Create a `sale_type='return'` document tied to `parent_sale_id`.

        `items` is a list of (sale_item_id, qty). Each item must:
          - belong to the parent sale,
          - have qty <= original item qty - already refunded.

        On success the parent gets `voided_at`/`voided_by_sale_id` only if
        the refund covers EVERY original line in full.
        """
        per_item = self._aggregate_refund_items(items)
        effective_operation_id = operation_id or uuid4()
        operation_hash = self._refund_operation_hash(
            parent_sale_id=parent_sale_id,
            items=per_item,
            reason=reason,
            comment=comment,
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

        parent_items, already_refunded = await self._validate_refund_items(
            parent_sale_id=parent_sale_id,
            per_item=per_item,
        )

        # Create the return-sale shell + item rows + +qty movements.
        shift = await self._lock_open_shift(
            parent.shift_id,
            error_message="Refunds require an open shift on the original register",
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
        )

        # Single refund payment (cash by default). Reason/comment go into
        # the metadata blob — keeps the SQL surface small.
        await self.repo.insert_payment(
            tenant_id=parent.tenant_id,
            sale_id=return_sale.id,
            payment_method="cash",
            amount=total,
            metadata_json={"reason": reason, "comment": comment},
        )
        receipt_seq, receipt = await self._allocate_receipt(parent.register_id)
        await self.repo.update_sale(
            return_sale,
            status="completed",
            completed_at=utc_now(),
            receipt_number=receipt,
            receipt_seq=receipt_seq,
            total_amount=total,
        )

        # Mark parent as voided iff this refund made every original line
        # 100% refunded.
        full = self._is_fully_refunded(parent_items, already_refunded, per_item)
        if full:
            await self.repo.update_sale(
                parent,
                voided_at=utc_now(),
                voided_by_sale_id=return_sale.id,
                status="voided",
            )

        logger.info(
            "refund_completed",
            return_sale_id=str(return_sale.id),
            parent_sale_id=str(parent.id),
            full=full,
        )
        return return_sale

    async def _already_refunded(self, parent_sale_id: UUID) -> dict[UUID, Decimal]:
        return await self.repo.refunded_quantities(parent_sale_id)

    @staticmethod
    def _is_fully_refunded(
        parent_items: dict[UUID, SaleItem],
        already_refunded: dict[UUID, Decimal],
        this_refund: dict[UUID, Decimal],
    ) -> bool:
        for pid, item in parent_items.items():
            done = already_refunded.get(pid, Decimal("0")) + this_refund.get(pid, Decimal("0"))
            if done < item.qty:
                return False
        return True


# Kept for static analysers
_ = FoundationRepository
