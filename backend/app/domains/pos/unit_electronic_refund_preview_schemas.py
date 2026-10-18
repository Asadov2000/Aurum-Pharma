"""Immutable read projections for electronic refund review, never payment commands."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.domains.pos.physical_documents import PhysicalCheckoutResultV3, PhysicalReceiptDocumentV3
from app.domains.pos.unit_contracts import Money


class _Fact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class SaleFact(_Fact):
    id: UUID
    tenant_id: UUID
    branch_id: UUID
    register_id: UUID
    shift_id: UUID
    cashier_user_id: UUID
    parent_sale_id: UUID | None
    sale_type: str
    status: str
    currency: str
    is_test: bool
    total_amount: Money
    created_at: datetime
    completed_at: datetime | None
    receipt_number: str | None
    receipt_seq: int | None
    receipt_snapshot: PhysicalReceiptDocumentV3


class CheckoutFact(_Fact):
    id: UUID
    tenant_id: UUID
    sale_id: UUID
    branch_id: UUID
    register_id: UUID
    shift_id: UUID
    cashier_user_id: UUID
    cash_intent_id: UUID | None
    payment_plan_intent_id: UUID | None
    stock_command_id: UUID
    payment_id: UUID
    payment_journal_version: int
    request_hash: str
    result_payload: PhysicalCheckoutResultV3
    receipt_payload: PhysicalReceiptDocumentV3


class PaymentFact(_Fact):
    id: UUID
    tenant_id: UUID
    sale_id: UUID
    payment_method: str
    amount: Money
    currency: str
    operation_id: UUID | None
    operation_hash: str | None
    payment_attempt_id: UUID | None
    metadata: object | None


class CheckoutComponentFact(_Fact):
    id: UUID
    tenant_id: UUID
    sale_id: UUID
    physical_checkout_id: UUID | None
    mixed_checkout_id: UUID | None
    method: str
    amount: Money
    currency: str
    payment_attempt_id: UUID | None
    created_by: UUID


class PhysicalReturnLineFact(_Fact):
    tenant_id: UUID
    command_id: UUID
    line_id: UUID
    stock_command_id: UUID
    branch_id: UUID
    offset: int
    base_units: int
    net_amount: Money


class RefundSaleFact(_Fact):
    id: UUID
    tenant_id: UUID
    branch_id: UUID
    parent_sale_id: UUID
    status: str
    sale_type: str
    currency: str
    is_test: bool
    total_amount: Money
    cashier_user_id: UUID


class RefundComponentFact(_Fact):
    id: UUID
    tenant_id: UUID
    parent_sale_id: UUID
    sale_id: UUID
    original_payment_id: UUID
    physical_refund_id: UUID | None
    mixed_refund_id: UUID | None
    method: str
    amount: Money
    currency: str
    created_by: UUID


class ElectronicRefundHistory(_Fact):
    sale: SaleFact
    checkout: CheckoutFact
    payments: tuple[PaymentFact, ...]
    components: tuple[CheckoutComponentFact, ...]
    return_lines: tuple[PhysicalReturnLineFact, ...]
    refund_sales: tuple[RefundSaleFact, ...]
    payouts: tuple[PaymentFact, ...]
    refund_components: tuple[RefundComponentFact, ...]
