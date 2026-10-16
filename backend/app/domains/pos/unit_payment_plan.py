"""Transient electronic payment plans; never accept money or authorize stock posting."""

from decimal import Decimal, localcontext
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator

from app.core.errors import BusinessRuleError, ConflictError
from app.domains.pos.repository import POSRepository
from app.domains.pos.service import POSService
from app.domains.pos.unit_cart_quote import MixedCartQuoteV2, UnitCartQuoteService
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_cart_service import UnitCartService
from app.domains.pos.unit_contracts import EXACT_CONTEXT, FrozenContract, Money
from app.domains.sync.integrity import canonical_json_hash


class UnitPaymentPart(FrozenContract):
    method: Literal["cash", "card", "qr"]
    amount: Money

    @model_validator(mode="after")
    def positive_amount(self) -> Self:
        if self.amount <= 0:
            raise ValueError("Payment allocation must be positive")
        return self


class UnitPaymentPlanRequest(FrozenContract):
    sale_id: UUID
    expected_revision_hash: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")
    expected_total_amount: Money
    currency: Literal["TJS"] = "TJS"
    payments: tuple[UnitPaymentPart, ...] = Field(min_length=1, max_length=3)
    cash_tendered: Money | None = None

    @model_validator(mode="after")
    def balanced_plan(self) -> Self:
        methods = {part.method for part in self.payments}
        if len(methods) != len(self.payments):
            raise ValueError("Payment methods must be unique")
        if not methods.intersection({"card", "qr"}):
            raise ValueError("Payment plan requires an electronic allocation")
        with localcontext(EXACT_CONTEXT):
            total = sum((part.amount for part in self.payments), Decimal("0.00"))
        if self.expected_total_amount <= 0 or total != self.expected_total_amount:
            raise ValueError("Payment allocations must exactly match the expected total")
        cash = next((part.amount for part in self.payments if part.method == "cash"), None)
        if cash is None:
            if self.cash_tendered is not None:
                raise ValueError("Cash tendered is forbidden without a cash allocation")
        elif self.cash_tendered is None or self.cash_tendered < cash:
            raise ValueError("Cash tendered must cover the cash allocation")
        return self


def payment_plan_hash(request: UnitPaymentPlanRequest, quote: MixedCartQuoteV2) -> str:
    """Comparison fingerprint, not a saved operation or payment authorization."""
    return canonical_json_hash(
        {
            "kind": "unit_payment_plan_v1",
            "tenant_id": str(quote.tenant_id),
            "branch_id": str(quote.branch_id),
            "cashier_id": str(quote.cashier_id),
            "sale_id": str(quote.sale_id),
            "revision_hash": quote.revision_hash,
            "currency": request.currency,
            "payments": [
                {"method": part.method, "amount": format(part.amount, ".2f")}
                for part in sorted(request.payments, key=lambda part: part.method)
            ],
            "cash_tendered": (
                format(request.cash_tendered, ".2f") if request.cash_tendered is not None else None
            ),
        }
    )


class ValidatedUnitPaymentPlan(FrozenContract):
    state: Literal["validated_draft"] = "validated_draft"
    request: UnitPaymentPlanRequest
    quote: MixedCartQuoteV2
    change_amount: Money
    plan_hash: str = Field(strict=True, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def coherent_plan(self) -> Self:
        if (
            self.request.sale_id != self.quote.sale_id
            or self.request.expected_revision_hash != self.quote.revision_hash
            or self.request.expected_total_amount != self.quote.total_amount
        ):
            raise ValueError("Payment plan differs from the current quote")
        kinds = {line.inventory_kind for line in self.quote.lines}
        if "physical_unit" not in kinds or "prepared_unit" in kinds:
            raise ValueError("Payment plan requires a physical or mixed physical cart")
        cash = next(
            (part.amount for part in self.request.payments if part.method == "cash"), Decimal("0")
        )
        with localcontext(EXACT_CONTEXT):
            change = (self.request.cash_tendered or Decimal("0")) - cash
        if self.change_amount != change:
            raise ValueError("Payment plan change does not balance")
        if self.plan_hash != payment_plan_hash(self.request, self.quote):
            raise ValueError("Payment plan fingerprint differs")
        return self


class UnitPaymentPlanService:
    def __init__(self, repository: UnitCartRepository) -> None:
        self.repository = repository

    async def validate(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
        request: UnitPaymentPlanRequest,
    ) -> ValidatedUnitPaymentPlan:
        request = UnitPaymentPlanRequest.model_validate(request)
        session = self.repository.session
        # Savepoints autoflush ORM changes; a preview must never persist caller drafts.
        if session.new or session.dirty or session.deleted:
            raise ConflictError("Persist pending draft changes before payment preview")
        async with session.begin_nested():
            sale = await UnitCartService(self.repository).lock_editable_sale(
                tenant_id, request.sale_id, actor_id, allowed_branch_ids
            )
            if sale.is_test:
                raise BusinessRuleError("Physical test sales do not support payment plans")
            quote = await UnitCartQuoteService(self.repository).quote(
                tenant_id=tenant_id,
                sale_id=request.sale_id,
                actor_id=actor_id,
                allowed_branch_ids=allowed_branch_ids,
            )
            kinds = {line.inventory_kind for line in quote.lines}
            if "physical_unit" not in kinds or "prepared_unit" in kinds:
                raise BusinessRuleError("Payment plan requires a physical or mixed physical cart")
            if request.expected_revision_hash != quote.revision_hash:
                raise ConflictError("Cart changed; refresh its quote before planning payment")
            if request.expected_total_amount != quote.total_amount:
                raise ConflictError("Expected payment total differs from the current cart")
            await POSService(POSRepository(session))._validate_pos_payment_configuration(
                tenant_id=tenant_id, requested_methods={part.method for part in request.payments}
            )
            await self.repository.assert_actor(tenant_id, actor_id, quote.branch_id)
            cash = next(
                (part.amount for part in request.payments if part.method == "cash"), Decimal("0")
            )
            with localcontext(EXACT_CONTEXT):
                change = (request.cash_tendered or Decimal("0")) - cash
            return ValidatedUnitPaymentPlan(
                request=request,
                quote=quote,
                change_amount=change,
                plan_hash=payment_plan_hash(request, quote),
            )
