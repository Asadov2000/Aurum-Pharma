"""Immutable read projections for electronic refund review, never payment commands."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, SerializerFunctionWrapHandler, model_serializer

from app.domains.inventory.unit_stock_v2 import SaleFormatV2
from app.domains.pos.mixed_documents import MixedCheckoutResultV4, MixedReceiptDocumentV4
from app.domains.pos.mixed_return_journal_schemas import LegacyOffset
from app.domains.pos.unit_contracts import LegacyQuantity, Money
from app.domains.pos.unit_electronic_refund_preview_schemas import (
    CheckoutComponentFact,
    PaymentFact,
    RefundComponentFact,
    RefundSaleFact,
)


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
    receipt_snapshot: MixedReceiptDocumentV4


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
    result_payload: MixedCheckoutResultV4
    receipt_payload: MixedReceiptDocumentV4


class MixedReturnLineFact(_Fact):
    tenant_id: UUID
    command_id: UUID
    line_id: UUID
    branch_id: UUID
    parent_checkout_id: UUID
    stock_command_id: UUID
    inventory_kind: str
    batch_id: UUID | None
    catalog_id: UUID
    legacy_quantity: LegacyQuantity | None
    legacy_offset: LegacyOffset | None
    offset: int | None
    base_units: int | None
    sale_format: SaleFormatV2 | None
    quantity: int | None
    net_amount: Money
    discount_amount: Money
    gross_amount: Money


class MixedRefundComponentFact(RefundComponentFact):
    mixed_electronic_refund_id: UUID | None = None

    @model_serializer(mode="wrap")
    def compatible_history(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        # An absent new parent must not change hashes of already saved reviews.
        result: dict[str, object] = handler(self)
        if self.mixed_electronic_refund_id is None:
            result.pop("mixed_electronic_refund_id", None)
        return result


class MixedElectronicRefundHistory(_Fact):
    sale: SaleFact
    checkout: CheckoutFact
    payments: tuple[PaymentFact, ...]
    components: tuple[CheckoutComponentFact, ...]
    return_lines: tuple[MixedReturnLineFact, ...]
    refund_sales: tuple[RefundSaleFact, ...]
    payouts: tuple[PaymentFact, ...]
    refund_components: tuple[MixedRefundComponentFact, ...]
