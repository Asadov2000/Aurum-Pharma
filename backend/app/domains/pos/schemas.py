"""Pydantic schemas for the POS domain."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Self
from uuid import UUID

from pydantic import UUID4, BaseModel, ConfigDict, Field, field_validator, model_validator

PAYMENT_METHODS = frozenset({"cash", "card", "qr"})
# Legacy clients can retry an operation created before QR became a distinct
# method. POSService still rejects a new bank_transfer after its idempotency
# lookup, so this wider parsing boundary does not re-enable it for new sales.
PAYMENT_METHOD_INPUTS = PAYMENT_METHODS | {"bank_transfer"}


# ---- shift ----


class ShiftOpenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    register_id: UUID
    opening_cash: Decimal = Field(
        default=Decimal("0"),
        ge=0,
        max_digits=14,
        decimal_places=2,
        allow_inf_nan=False,
    )


class ShiftCloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    closing_cash_actual: Decimal = Field(
        ge=0,
        max_digits=14,
        decimal_places=2,
        allow_inf_nan=False,
    )
    notes: str | None = Field(default=None, max_length=2000)


class ShiftRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    branch_id: UUID
    register_id: UUID
    opened_by_user_id: UUID
    closed_by_user_id: UUID | None
    opened_at: datetime
    closed_at: datetime | None
    status: str
    opening_cash: Decimal
    closing_cash_actual: Decimal | None
    closing_cash_expected: Decimal | None
    closing_difference: Decimal | None
    totals: dict[str, Any] | None
    currency: str
    notes: str | None


class ShiftHistoryItem(BaseModel):
    id: UUID
    branch_id: UUID
    branch_name: str
    register_id: UUID
    register_name: str
    cashier_user_id: UUID
    cashier_name: str | None
    opened_at: datetime
    closed_at: datetime | None
    status: str
    opening_cash: Decimal
    closing_cash_actual: Decimal | None
    closing_cash_expected: Decimal | None
    closing_difference: Decimal | None
    sales_total: Decimal
    returns_total: Decimal
    sales_count: int
    returns_count: int
    currency: str


class ShiftHistoryList(BaseModel):
    items: list[ShiftHistoryItem]
    total: int
    page: int
    page_size: int


class ZReport(BaseModel):
    shift_id: UUID
    opened_at: datetime
    closed_at: datetime | None
    register_id: UUID
    cashier_user_id: UUID
    opening_cash: Decimal
    closing_cash_actual: Decimal | None
    closing_cash_expected: Decimal | None
    closing_difference: Decimal | None
    totals: dict[str, Any]
    sales_count: int
    returns_count: int


# ---- sale ----


class SaleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    register_id: UUID


class SaleItemAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_id: UUID
    qty: Decimal = Field(gt=0, max_digits=14, decimal_places=3, allow_inf_nan=False)
    expired_sale_confirmed: bool = False


class SaleItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qty: Decimal = Field(gt=0, max_digits=14, decimal_places=3, allow_inf_nan=False)


class PaymentAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID4
    payment_method: str
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    metadata: dict[str, Any] | None = None

    @field_validator("payment_method")
    @classmethod
    def _check_method(cls, v: str) -> str:
        if v not in PAYMENT_METHOD_INPUTS:
            raise ValueError(f"payment_method must be one of {sorted(PAYMENT_METHOD_INPUTS)}")
        return v


class SaleCheckoutItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_id: UUID
    qty: Decimal = Field(gt=0, max_digits=14, decimal_places=3, allow_inf_nan=False)


class SaleCheckoutPayment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_method: str
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    metadata: dict[str, Any] | None = None

    @field_validator("payment_method")
    @classmethod
    def _check_method(cls, v: str) -> str:
        if v not in PAYMENT_METHOD_INPUTS:
            raise ValueError(f"payment_method must be one of {sorted(PAYMENT_METHOD_INPUTS)}")
        return v


class _PrescriptionFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prescription_number: str | None = Field(default=None, max_length=500)
    doctor_name: str | None = Field(default=None, max_length=500)
    doctor_license: str | None = Field(default=None, max_length=500)
    patient_name: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator(
        "prescription_number",
        "doctor_name",
        "doctor_license",
        "patient_name",
        "notes",
        mode="before",
    )
    @classmethod
    def _strip_optional_text(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return value

    @model_validator(mode="after")
    def _require_meaningful_details(self) -> Self:
        if not any(
            (
                self.prescription_number,
                self.doctor_name,
                self.doctor_license,
                self.patient_name,
                self.notes,
            )
        ):
            raise ValueError("at least one prescription detail is required")
        return self


class SaleCheckoutPrescription(_PrescriptionFields):
    pass


class SaleCheckoutRequest(BaseModel):
    """One retry-safe command that creates every durable part of a sale."""

    model_config = ConfigDict(extra="forbid")

    operation_id: UUID4
    register_id: UUID
    draft_sale_id: UUID | None = None
    items: list[SaleCheckoutItem] = Field(min_length=1, max_length=200)
    payments: list[SaleCheckoutPayment] = Field(min_length=1, max_length=10)
    prescription: SaleCheckoutPrescription | None = None
    expired_sale_confirmed: bool = False


class SaleCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expired_sale_confirmed: bool = False


class SaleCheckoutItemResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    catalog_id: UUID
    batch_id: UUID
    qty: Decimal
    unit_price: Decimal
    total_price: Decimal
    currency: str
    discount_amount: Decimal
    position: int


class SaleCheckoutPaymentResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    payment_method: str
    amount: Decimal
    currency: str


class SaleCheckoutResult(BaseModel):
    """Immutable response snapshot also stored as the outbox event payload."""

    event_id: UUID
    sale_id: UUID
    operation_id: UUID
    tenant_id: UUID
    branch_id: UUID
    register_id: UUID
    shift_id: UUID
    cashier_user_id: UUID
    receipt_number: str
    receipt_seq: int
    created_at: datetime
    completed_at: datetime
    total_amount: Decimal
    currency: str
    is_test: bool
    items: list[SaleCheckoutItemResult]
    payments: list[SaleCheckoutPaymentResult]


class SaleItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sale_id: UUID
    catalog_id: UUID
    batch_id: UUID
    qty: Decimal
    unit_price: Decimal
    total_price: Decimal
    currency: str
    discount_amount: Decimal
    position: int
    # Additive read-only enrichment from the line's FEFO-chosen batch, so the
    # cashier can see which batch/expiry each line drew from. Null if unresolved.
    batch_number: str | None = None
    expires_at: date | None = None
    days_to_expiry: int | None = None
    refunded_qty: Decimal = Decimal("0")


class SaleItemAdded(BaseModel):
    """The /items endpoint may have split one request into several items
    when FEFO drew from multiple batches. requires_prescription_log is
    true if any item carries a 'prescription' dispensing_type."""

    items: list[SaleItemRead]
    requires_prescription_log: bool


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sale_id: UUID
    operation_id: UUID | None
    payment_method: str
    amount: Decimal
    currency: str


class SaleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    branch_id: UUID
    register_id: UUID
    shift_id: UUID
    sale_type: str
    parent_sale_id: UUID | None
    status: str
    receipt_number: str | None
    operation_id: UUID | None
    is_test: bool
    total_amount: Decimal
    currency: str
    voided_at: datetime | None
    voided_by_sale_id: UUID | None
    cashier_user_id: UUID
    created_at: datetime
    completed_at: datetime | None


class SaleDetails(SaleRead):
    items: list[SaleItemRead]
    payments: list[PaymentRead]


class PrescriptionLogCreate(_PrescriptionFields):
    sale_item_id: UUID | None = None


class PrescriptionLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    sale_id: UUID
    sale_item_id: UUID | None
    prescription_number: str | None
    doctor_name: str | None
    doctor_license: str | None
    patient_name: str | None
    notes: str | None
    created_at: datetime


class RefundItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sale_item_id: UUID
    qty: Decimal = Field(gt=0, max_digits=14, decimal_places=3, allow_inf_nan=False)


class RefundCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID4
    items: list[RefundItem] = Field(min_length=1, max_length=200)
    reason: str | None = Field(default=None, max_length=500)
    comment: str | None = Field(default=None, max_length=2000)
    external_refund_confirmed: bool = False


# ---- sales listing (receipt search) ----


class SaleListItem(BaseModel):
    """Legacy-friendly, fully-resolved row for the receipt search table.

    Names are resolved server-side (no raw UUIDs in the UI) and `has_refund` /
    `is_refund` are derived from the parent/child sale relationship rather than
    a stored column, so they can never drift from reality.
    """

    id: UUID
    receipt_number: str | None
    completed_at: datetime | None
    branch_name: str | None
    register_name: str | None
    cashier_name: str | None
    total_amount: Decimal
    currency: str
    payment_methods: list[str]
    is_refund: bool
    parent_sale_id: UUID | None
    parent_receipt_number: str | None
    has_refund: bool
    refund_receipt_number: str | None
    items_summary: str
    status: str


class SaleList(BaseModel):
    items: list[SaleListItem]
    total: int
    page: int
    page_size: int


# ---- receipt (print / PDF) ----


class ReceiptLine(BaseModel):
    """One printed line of a receipt — resolved name, no raw UUIDs."""

    position: int
    name: str
    qty: Decimal
    unit_price: Decimal
    discount_amount: Decimal
    total_price: Decimal


class ReceiptPayment(BaseModel):
    method: str
    amount: Decimal


class ReceiptData(BaseModel):
    """Everything the print view / PDF needs, fully resolved server-side."""

    sale_id: UUID
    is_refund: bool
    status: str
    # header
    pharmacy_name: str
    branch_name: str
    branch_address: str | None
    branch_license: str | None
    # meta
    receipt_number: str | None
    datetime: datetime | None
    cashier_name: str | None
    # body
    items: list[ReceiptLine]
    discount_total: Decimal
    total: Decimal
    currency: str
    payments: list[ReceiptPayment]
    paid_total: Decimal
    change: Decimal


# ---- Z-report (shift close XLSX) ----


class ZReportPaymentBreakdown(BaseModel):
    """Forward-sale totals grouped by the sale's payment method. A sale paid
    with two+ distinct methods lands in `mixed` (there is no sale.payment_method
    column — the bucket is derived from the sale_payment rows)."""

    cash: Decimal
    card: Decimal
    qr: Decimal
    bank_transfer: Decimal
    mixed: Decimal


class ZReportData(BaseModel):
    """Fully-resolved shift summary for the XLSX export."""

    shift_id: UUID
    status: str
    # header
    pharmacy_name: str
    branch_name: str
    register_id: UUID
    register_name: str
    cashier_user_id: UUID
    cashier_name: str | None
    opened_at: datetime
    closed_at: datetime | None
    # sales / returns
    sales_count: int
    total_sales: Decimal
    total_discounts: Decimal
    returns_count: int
    total_refunds: Decimal
    currency: str
    payment_breakdown: ZReportPaymentBreakdown
    # cash reconciliation
    initial_cash: Decimal
    expected_cash: Decimal | None
    actual_cash: Decimal | None
    cash_difference: Decimal | None
    difference_reason: str | None


# ---- sales summary (accountant XLSX, arbitrary date range) ----


class SalesSummaryRow(BaseModel):
    """One receipt line in the detail sheet. `kind` drives the status label:
    sale → Завершён, return → Возврат, voided → Отменён."""

    completed_at: datetime | None
    receipt_number: str | None
    cashier_name: str | None
    branch_name: str | None
    kind: str  # "sale" | "return" | "voided"
    payment_method: str  # cash | card | qr | bank_transfer | mixed | none
    gross: Decimal
    discount: Decimal
    net: Decimal


class SalesSummaryData(BaseModel):
    """Resolved sales summary for the accountant XLSX over [date_from, date_to].
    Totals count completed forward sales (gross/discounts/breakdown) and
    completed returns (refunds) — the same status='completed' basis as the
    Z-report, so a single shift's range reconciles with its Z-report."""

    date_from: date
    date_to: date
    branch_name: str | None  # set when filtered to one branch
    show_branch_column: bool
    currency: str
    rows: list[SalesSummaryRow]
    gross_sales: Decimal
    total_discounts: Decimal
    total_refunds: Decimal
    net: Decimal
    sales_count: int
    returns_count: int
    payment_breakdown: ZReportPaymentBreakdown


# ---- stock on date (accountant XLSX) ----


class StockRow(BaseModel):
    name: str
    inn: str | None
    branch_name: str | None
    batch_number: str | None
    expires_at: date | None
    qty: Decimal
    purchase_price: Decimal
    value: Decimal  # qty × purchase_price


class StockOnDateData(BaseModel):
    """Per-batch stock as of `on_date`, reconstructed from the batch_movement
    ledger (Σ qty_delta where movement date ≤ on_date)."""

    on_date: date
    branch_name: str | None
    show_branch_column: bool
    currency: str
    rows: list[StockRow]
    total_qty: Decimal
    total_value: Decimal
