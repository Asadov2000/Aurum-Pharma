"""Validate a cashier's cash confirmation without accepting money or stock."""

from __future__ import annotations

from decimal import localcontext
from typing import Literal, Self
from uuid import UUID

from pydantic import UUID4, Field, model_validator

from app.core.errors import BusinessRuleError, ConflictError
from app.domains.pos.unit_cart_quote import MixedCartQuoteV2, UnitCartQuoteService
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_contracts import EXACT_CONTEXT, FrozenContract, Money


class CashConfirmationRequestV2(FrozenContract):
    schema_version: Literal[2] = 2
    operation_id: UUID4
    sale_id: UUID
    expected_revision_hash: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")
    expected_total_amount: Money
    cash_tendered: Money
    currency: Literal["TJS"] = "TJS"

    @model_validator(mode="after")
    def positive_amounts(self) -> Self:
        if self.expected_total_amount <= 0 or self.cash_tendered <= 0:
            raise ValueError("cash confirmation amounts must be positive")
        return self


class ValidatedCashConfirmationV2(FrozenContract):
    """Transient validation, never a receipt, reservation or replay result."""

    schema_version: Literal[2] = 2
    state: Literal["validated_draft"] = "validated_draft"
    request: CashConfirmationRequestV2
    quote: MixedCartQuoteV2
    change_amount: Money

    @model_validator(mode="after")
    def coherent_confirmation(self) -> Self:
        if (
            self.request.sale_id != self.quote.sale_id
            or self.request.expected_revision_hash != self.quote.revision_hash
            or self.request.expected_total_amount != self.quote.total_amount
        ):
            raise ValueError("cash confirmation differs from the current cart")
        with localcontext(EXACT_CONTEXT):
            change = self.request.cash_tendered - self.quote.total_amount
        if change < 0 or change != self.change_amount:
            raise ValueError("cash tendered and change must balance the cart")
        return self


class UnitCashConfirmationService:
    def __init__(self, repository: UnitCartRepository) -> None:
        self.repository = repository

    async def validate(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
        request: CashConfirmationRequestV2,
    ) -> ValidatedCashConfirmationV2:
        # Revalidate even model_construct inputs before any database operation.
        request = CashConfirmationRequestV2.model_validate(request)
        async with self.repository.session.begin_nested():
            quote = await UnitCartQuoteService(self.repository).quote(
                tenant_id=tenant_id,
                sale_id=request.sale_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
            )
            if request.expected_revision_hash != quote.revision_hash:
                raise ConflictError("Cart changed; refresh its total before confirming cash")
            if request.expected_total_amount != quote.total_amount:
                raise ConflictError("Confirmed amount differs from the current cart total")
            with localcontext(EXACT_CONTEXT):
                change = request.cash_tendered - quote.total_amount
            if change < 0:
                raise BusinessRuleError("Cash tendered is less than the current cart total")
            return ValidatedCashConfirmationV2(request=request, quote=quote, change_amount=change)
