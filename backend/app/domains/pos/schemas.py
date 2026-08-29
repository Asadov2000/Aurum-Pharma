"""Pydantic schemas for the POS domain."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import UUID4, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domains.catalog.schemas import CatalogItemRead
from app.domains.customer_returns.reasons import RefundReasonCode

PAYMENT_METHODS = frozenset({"cash", "card", "qr"})
# Legacy clients can retry an operation created before QR became a distinct
# method. POSService still rejects a new bank_transfer after its idempotency
# lookup, so this wider parsing boundary does not re-enable it for new sales.
PAYMENT_METHOD_INPUTS = PAYMENT_METHODS | {"bank_transfer"}
PAYMENT_ATTEMPT_METHODS = frozenset({"card", "qr"})
PAYMENT_ATTEMPT_VOID_REASONS = frozenset(
    {
        "cashier_cancelled",
        "customer_cancelled",
        "terminal_declined",
        "timeout",
        "duplicate",
        "checkout_failed",
        "manager_override",
    }
)
REFUND_ATTEMPT_METHODS = frozenset({"card", "qr", "bank_transfer"})


# ---- personal POS favorites ----


class POSFavoriteCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    catalog_id: UUID4


class POSFavoriteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    catalog_id: UUID
    created_at: datetime


class POSFavoriteCatalogRead(POSFavoriteRead):
    catalog: CatalogItemRead


# ---- server-trusted payment attempts ----


class POSPaymentAttemptCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID4
    sale_id: UUID4
    payment_method: Literal["card", "qr"]
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2, allow_inf_nan=False)
    currency: Literal["TJS"] = "TJS"


class POSPaymentAttemptConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    terminal_id: str = Field(min_length=1, max_length=64)
    external_reference: str = Field(min_length=1, max_length=128)

    @field_validator("terminal_id", "external_reference", mode="before")
    @classmethod
    def _strip_evidence(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if any(ord(char) < 32 for char in stripped):
                raise ValueError("payment evidence contains control characters")
            return stripped
        return value


class POSPaymentAttemptVoid(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Literal[
        "cashier_cancelled",
        "customer_cancelled",
        "terminal_declined",
        "timeout",
        "duplicate",
        "checkout_failed",
        "manager_override",
    ]
    operator_note: str | None = Field(default=None, max_length=160)
    terminal_id: str | None = Field(default=None, min_length=1, max_length=64)
    external_reference: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("terminal_id", "external_reference", mode="before")
    @classmethod
    def _strip_evidence(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if any(ord(char) < 32 for char in stripped):
                raise ValueError("payment evidence contains control characters")
            return stripped
        return value

    @field_validator("operator_note", mode="before")
    @classmethod
    def _strip_non_pii_note(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        lowered = stripped.lower()
        if (
            "@" in stripped
            or "://" in lowered
            or "www." in lowered
            or any(ord(char) < 32 for char in stripped)
        ):
            raise ValueError("operator_note must not contain contact or link data")
        digits = "".join(char if char.isdigit() else " " for char in stripped)
        if any(len(group) >= 6 for group in digits.split()):
            raise ValueError("operator_note must not contain long numeric identifiers")
        return stripped or None

    @model_validator(mode="after")
    def _evidence_pair(self) -> Self:
        if (self.terminal_id is None) != (self.external_reference is None):
            raise ValueError("terminal_id and external_reference must be provided together")
        return self


class POSPaymentAttemptRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: UUID
    sale_id: UUID
    cashier_user_id: UUID
    operation_id: UUID
    payment_method: Literal["card", "qr"]
    amount: Decimal
    currency: Literal["TJS"]
    status: Literal["pending", "requires_reconciliation", "confirmed", "consumed", "voided"]
    evidence_required: bool
    reconciliation_started_at: datetime | None
    terminal_id: str | None
    external_reference: str | None
    resolved_by_user_id: UUID | None
    void_reason: str | None
    void_note: str | None
    created_at: datetime
    confirmed_at: datetime | None
    consumed_at: datetime | None
    voided_at: datetime | None


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

    operation_id: UUID4
    register_id: UUID


class SaleItemAdd(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID4
    catalog_id: UUID
    qty: Decimal = Field(gt=0, max_digits=14, decimal_places=3, allow_inf_nan=False)
    expired_sale_confirmed: bool = False


class SaleItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID4
    qty: Decimal = Field(gt=0, max_digits=14, decimal_places=3, allow_inf_nan=False)


class SaleItemDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID4


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
    payment_attempt_id: UUID4 | None = None

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
    payment_attempt_id: UUID | None = None
    payment_attempt_status: Literal["consumed"] | None = None


class SaleCheckoutResult(BaseModel):
    """Immutable response snapshot also stored as the outbox event payload."""

    model_config = ConfigDict(extra="forbid")

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


class SaleRefundItemResult(SaleCheckoutItemResult):
    """Immutable return line linked to its original sale line."""

    parent_sale_item_id: UUID


class SaleRefundResult(BaseModel):
    """Immutable refund snapshot stored in the sync outbox."""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    sale_id: UUID
    parent_sale_id: UUID
    parent_fully_refunded: bool
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
    currency: Literal["TJS"]
    is_test: bool
    items: list[SaleRefundItemResult] = Field(min_length=1, max_length=200)
    payments: list[SaleCheckoutPaymentResult] = Field(min_length=1, max_length=10)

    @model_validator(mode="after")
    def _validate_financial_snapshot(self) -> Self:
        item_ids = [item.id for item in self.items]
        parent_item_ids = [item.parent_sale_item_id for item in self.items]
        payment_ids = [payment.id for payment in self.payments]
        if len(item_ids) != len(set(item_ids)):
            raise ValueError("refund item ids must be unique")
        if len(parent_item_ids) != len(set(parent_item_ids)):
            raise ValueError("original sale item ids must be unique")
        if len(payment_ids) != len(set(payment_ids)):
            raise ValueError("refund payment ids must be unique")
        if self.total_amount <= 0:
            raise ValueError("refund total must be positive")
        if sum((item.total_price for item in self.items), Decimal("0")) != self.total_amount:
            raise ValueError("refund item total does not match refund total")
        if sum((payment.amount for payment in self.payments), Decimal("0")) != self.total_amount:
            raise ValueError("refund payment total does not match refund total")
        if any(item.currency != self.currency for item in self.items):
            raise ValueError("refund item currency does not match refund currency")
        if any(payment.currency != self.currency for payment in self.payments):
            raise ValueError("refund payment currency does not match refund currency")
        return self


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
    refund_attempt_id: UUID | None
    is_test: bool
    total_amount: Decimal
    currency: str
    voided_at: datetime | None
    voided_by_sale_id: UUID | None
    cashier_user_id: UUID
    created_at: datetime
    completed_at: datetime | None


class SaleItemDeleted(BaseModel):
    command_type: Literal["item.delete"] = "item.delete"
    sale_id: UUID
    item_id: UUID
    status: Literal["deleted"] = "deleted"


class POSSaleCreateCommandResult(BaseModel):
    command_type: Literal["sale.create"] = "sale.create"
    sale: SaleRead


class POSItemAddCommandResult(BaseModel):
    command_type: Literal["item.add"] = "item.add"
    item_add: SaleItemAdded


class POSItemUpdateCommandResult(BaseModel):
    command_type: Literal["item.update"] = "item.update"
    item: SaleItemRead


POSCommandResult = Annotated[
    POSSaleCreateCommandResult
    | POSItemAddCommandResult
    | POSItemUpdateCommandResult
    | SaleItemDeleted,
    Field(discriminator="command_type"),
]


class POSCommandRead(BaseModel):
    operation_id: UUID
    sale_id: UUID | None
    created_at: datetime
    result: POSCommandResult


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


class POSRefundAttemptCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID4
    items: list[RefundItem] = Field(min_length=1, max_length=200)


class POSRefundConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_method: Literal["card", "qr", "bank_transfer"]
    terminal_id: str = Field(min_length=1, max_length=64)
    document_number: str = Field(min_length=1, max_length=128)

    @field_validator("terminal_id", "document_number", mode="before")
    @classmethod
    def _strip_terminal_reference(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if any(ord(char) < 32 for char in stripped):
            raise ValueError("terminal reference contains control characters")
        return stripped


class POSRefundAttemptConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmations: list[POSRefundConfirmation] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def _unique_payment_methods(self) -> Self:
        methods = [confirmation.payment_method for confirmation in self.confirmations]
        if len(methods) != len(set(methods)):
            raise ValueError("each payment method can be confirmed only once")
        return self


class POSRefundAttemptVoid(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Literal[
        "cashier_cancelled",
        "customer_cancelled",
        "terminal_declined",
        "timeout",
        "duplicate",
        "refund_failed",
        "manager_override",
    ]
    operator_note: str | None = Field(default=None, max_length=160)

    @field_validator("operator_note", mode="before")
    @classmethod
    def _strip_non_pii_note(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        lowered = stripped.lower()
        if (
            "@" in stripped
            or "://" in lowered
            or "www." in lowered
            or any(ord(char) < 32 for char in stripped)
        ):
            raise ValueError("operator_note must not contain contact or link data")
        digits = "".join(char if char.isdigit() else " " for char in stripped)
        if any(len(group) >= 6 for group in digits.split()):
            raise ValueError("operator_note must not contain long numeric identifiers")
        return stripped or None


class POSRefundAttemptPaymentRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_method: Literal["card", "qr", "bank_transfer"]
    amount: Decimal
    terminal_id: str | None = None
    document_number: str | None = None
    confirmed_by_user_id: UUID | None = None
    confirmed_at: datetime | None = None


class POSRefundAttemptRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    tenant_id: UUID
    parent_sale_id: UUID
    register_id: UUID
    requested_by_user_id: UUID
    confirmed_by_user_id: UUID | None
    operation_id: UUID
    items: list[RefundItem]
    payments: list[POSRefundAttemptPaymentRead]
    total_amount: Decimal
    external_amount: Decimal
    currency: Literal["TJS"]
    status: Literal["pending", "requires_reconciliation", "confirmed", "consumed", "voided"]
    void_reason: str | None
    void_note: str | None
    created_at: datetime
    confirmed_at: datetime | None
    consumed_at: datetime | None
    voided_at: datetime | None


class RefundCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: UUID4
    items: list[RefundItem] = Field(min_length=1, max_length=200)
    reason: RefundReasonCode | None = None
    comment: str | None = Field(default=None, max_length=500)
    refund_attempt_id: UUID4 | None = None

    @field_validator("comment", mode="before")
    @classmethod
    def _strip_comment(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        return stripped or None


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
    original_receipt_number: str | None = None
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


class SalesSummaryDay(BaseModel):
    """Screen-friendly sales totals for one tenant-local calendar day."""

    day: date
    gross_sales: Decimal
    total_discounts: Decimal
    total_refunds: Decimal
    net: Decimal
    sales_count: int
    returns_count: int


class SalesSummaryOverview(BaseModel):
    """Compact report overview without receipt-level rows."""

    date_from: date
    date_to: date
    branch_name: str | None
    currency: str
    gross_sales: Decimal
    total_discounts: Decimal
    total_refunds: Decimal
    net: Decimal
    sales_count: int
    returns_count: int
    average_sale: Decimal
    payment_breakdown: ZReportPaymentBreakdown
    daily: list[SalesSummaryDay]


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
