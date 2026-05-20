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

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.exc import IntegrityError, InternalError

from app.core.errors import (
    BusinessRuleError,
    ConflictError,
    NotFoundError,
)
from app.core.time import utc_now
from app.domains.catalog.models import TenantCatalog
from app.domains.foundation.models import Tenant
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
from app.domains.pos.repository import POSRepository

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
    ) -> Shift:
        existing = await self.repo.get_open_shift_for_register(register_id)
        if existing is not None:
            raise ConflictError(
                "Register already has an open shift",
                details={"shift_id": str(existing.id)},
            )
        # branch_id comes from the register row — fetched directly to avoid
        # a cross-domain dep on foundation.service.
        from app.domains.foundation.models import Register

        register = await self.repo.session.get(Register, register_id)
        if register is None or register.tenant_id != tenant_id:
            raise NotFoundError("Register not found")
        if not register.is_active:
            raise BusinessRuleError("Register is inactive")
        return await self.repo.create_shift(
            tenant_id=tenant_id,
            branch_id=register.branch_id,
            register_id=register_id,
            opened_by_user_id=opened_by_user_id,
            opening_cash=opening_cash,
        )

    async def get_current_shift(self, *, user_id: UUID, register_id: UUID) -> Shift | None:
        return await self.repo.get_open_shift_for_user(user_id, register_id)

    async def close_shift(
        self,
        *,
        shift_id: UUID,
        closing_cash_actual: Decimal,
        closed_by_user_id: UUID,
        notes: str | None = None,
    ) -> Shift:
        shift = await self.repo.get_shift(shift_id)
        if shift is None:
            raise NotFoundError("Shift not found")
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

    async def z_report(self, shift_id: UUID) -> dict[str, Any]:
        shift = await self.repo.get_shift(shift_id)
        if shift is None:
            raise NotFoundError("Shift not found")
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
    ) -> Sale:
        shift = await self.repo.get_open_shift_for_register(register_id)
        if shift is None:
            raise BusinessRuleError("No open shift for this register")
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

    async def get_sale_details(
        self, sale_id: UUID
    ) -> tuple[Sale, list[SaleItem], list[SalePayment]]:
        sale = await self.get_sale(sale_id)
        items = await self.repo.list_items(sale.id)
        payments = await self.repo.list_payments(sale.id)
        return sale, items, payments

    @staticmethod
    def _assert_draft(sale: Sale) -> None:
        if sale.status != "draft":
            raise ConflictError(
                "Sale is no longer editable",
                details={"status": sale.status},
            )

    # ---- items ----

    async def add_item(
        self,
        *,
        sale_id: UUID,
        catalog_id: UUID,
        qty: Decimal,
        today: date | None = None,
    ) -> tuple[list[SaleItem], bool]:
        """Returns (created items, requires_prescription_log)."""
        sale = await self.get_sale(sale_id)
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

    async def update_item(self, *, sale_id: UUID, item_id: UUID, qty: Decimal) -> SaleItem:
        sale = await self.get_sale(sale_id)
        self._assert_draft(sale)
        item = await self.repo.get_item(item_id)
        if item is None or item.sale_id != sale_id:
            raise NotFoundError("Sale item not found")
        total_price = (item.unit_price * qty).quantize(Decimal("0.01"))
        updated = await self.repo.update_item(item, qty=qty, total_price=total_price)
        await self._recompute_total(sale)
        return updated

    async def delete_item(self, *, sale_id: UUID, item_id: UUID) -> None:
        sale = await self.get_sale(sale_id)
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
        metadata: dict[str, Any] | None = None,
    ) -> SalePayment:
        sale = await self.get_sale(sale_id)
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
    ) -> PrescriptionLog:
        sale = await self.get_sale(sale_id)
        if sale.status == "voided":
            raise ConflictError("Sale is voided")
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

    async def complete(self, *, sale_id: UUID) -> Sale:
        sale = await self.get_sale(sale_id)
        self._assert_draft(sale)

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

            for batch_id, qty in per_batch.items():
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

        receipt = await self.repo.next_receipt_number(sale.shift_id)
        completed = await self.repo.update_sale(
            sale,
            status="completed",
            completed_at=utc_now(),
            receipt_number=receipt,
        )
        logger.info(
            "sale_completed",
            sale_id=str(sale.id),
            receipt_number=receipt,
            total=str(sale.total_amount),
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

    async def refund(
        self,
        *,
        parent_sale_id: UUID,
        items: list[tuple[UUID, Decimal]],
        reason: str | None,
        comment: str | None,
        cashier_user_id: UUID,
    ) -> Sale:
        """Create a `sale_type='return'` document tied to `parent_sale_id`.

        `items` is a list of (sale_item_id, qty). Each item must:
          - belong to the parent sale,
          - have qty <= original item qty - already refunded.

        On success the parent gets `voided_at`/`voided_by_sale_id` only if
        the refund covers EVERY original line in full.
        """
        parent = await self.get_sale(parent_sale_id)
        if parent.status != "completed":
            raise BusinessRuleError(
                "Can only refund a completed sale",
                details={"status": parent.status},
            )
        if parent.sale_type != "sale":
            raise BusinessRuleError("Only forward sales can be refunded")

        parent_items = {i.id: i for i in await self.repo.list_items(parent_sale_id)}
        # Already-refunded qty per parent item (sum across previous returns).
        already_refunded = await self._already_refunded(parent_sale_id)

        # Validate the requested refund lines.
        per_item: dict[UUID, Decimal] = {}
        for item_id, qty in items:
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
            per_item[item_id] = per_item.get(item_id, Decimal("0")) + qty

        # Create the return-sale shell + item rows + +qty movements.
        shift = await self.repo.get_shift(parent.shift_id)
        if shift is None or shift.status != "open":
            raise BusinessRuleError(
                "Refunds require an open shift on the original register",
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
        )

        inv_repo = InventoryRepository(self.repo.session)
        total = Decimal("0")
        for item_id, qty in per_item.items():
            parent_item = parent_items[item_id]
            unit_price = parent_item.unit_price
            total_price = (unit_price * qty).quantize(Decimal("0.01"))
            position = await self.repo.next_item_position(return_sale.id)
            await self.repo.insert_item(
                tenant_id=parent.tenant_id,
                sale_id=return_sale.id,
                catalog_id=parent_item.catalog_id,
                batch_id=parent_item.batch_id,
                qty=qty,
                unit_price=unit_price,
                total_price=total_price,
                position=position,
            )
            total += total_price
            if not parent.is_test:
                await inv_repo.insert_movement(
                    tenant_id=parent.tenant_id,
                    batch_id=parent_item.batch_id,
                    movement_type="sale_return",
                    qty_delta=qty,
                    source_table="sale",
                    source_id=return_sale.id,
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
        receipt = await self.repo.next_receipt_number(shift.id)
        await self.repo.update_sale(
            return_sale,
            status="completed",
            completed_at=utc_now(),
            receipt_number=receipt,
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
        """For each parent sale_item, sum of qty already refunded across all
        prior return-sales. Identifies parent items by (catalog_id, batch_id)
        match — refunds quote those columns from the original line."""
        from sqlalchemy import select

        # Previous return sales for this parent
        prev_returns_stmt = select(Sale).where(
            Sale.parent_sale_id == parent_sale_id,
            Sale.status == "completed",
        )
        prev_returns = (await self.repo.session.execute(prev_returns_stmt)).scalars().all()
        if not prev_returns:
            return {}

        refunded: dict[UUID, Decimal] = {}
        # For each return, pair its items with parent items by catalog+batch.
        parent_items = await self.repo.list_items(parent_sale_id)
        parent_by_key: dict[tuple[UUID, UUID], UUID] = {
            (it.catalog_id, it.batch_id): it.id for it in parent_items
        }
        for ret in prev_returns:
            ret_items = await self.repo.list_items(ret.id)
            for it in ret_items:
                key = (it.catalog_id, it.batch_id)
                parent_item_id = parent_by_key.get(key)
                if parent_item_id is None:
                    continue
                refunded[parent_item_id] = refunded.get(parent_item_id, Decimal("0")) + it.qty
        return refunded

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
