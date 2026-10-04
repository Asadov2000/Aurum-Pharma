"""Save and replay cashier intents; never accept cash or finalize a sale."""

import json
from hashlib import sha256
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, ValidationError

from app.core.errors import AurumError, BusinessRuleError, ConflictError, PermissionDeniedError
from app.domains.pos.unit_cart_quote import MixedCartQuoteV2
from app.domains.pos.unit_cart_repository import UnitCartRepository
from app.domains.pos.unit_cash_confirmation import (
    CashConfirmationRequestV2,
    UnitCashConfirmationService,
    ValidatedCashConfirmationV2,
)
from app.domains.pos.unit_cash_intent_models import UnitCashIntent
from app.domains.pos.unit_cash_intent_repository import UnitCashIntentRepository
from app.domains.pos.unit_contracts import FrozenContract


class SavedCashIntentV2(FrozenContract):
    schema_version: Literal[2] = 2
    state: Literal["saved_draft"] = "saved_draft"
    accepted_at: AwareDatetime
    confirmation: ValidatedCashConfirmationV2


def cash_intent_request_hash(request: CashConfirmationRequestV2) -> str:
    """Versioned request fingerprint, not an authorization token."""
    payload = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return sha256(("aurum-cash-intent-v1:" + payload).encode()).hexdigest()


class UnitCashIntentService:
    def __init__(self, repository: UnitCashIntentRepository) -> None:
        self.repository = repository
        self.cart_repository = UnitCartRepository(repository.session)

    async def save(
        self,
        *,
        tenant_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
        request: CashConfirmationRequestV2,
    ) -> SavedCashIntentV2:
        request = CashConfirmationRequestV2.model_validate(request)
        if not all(isinstance(value, UUID) for value in (tenant_id, actor_id)):
            raise BusinessRuleError("Cash confirmation scope must use UUIDs")
        if not isinstance(allowed_branch_ids, set) or not all(
            isinstance(branch, UUID) for branch in allowed_branch_ids
        ):
            raise BusinessRuleError("Allowed branches must be UUIDs")
        await self.cart_repository.assert_actor(tenant_id, actor_id)
        async with self.repository.session.begin_nested():
            intent = await self.repository.get(tenant_id, request.operation_id)
            if intent is None:
                # A concurrent first save may commit after this initial read.
                # Wait on the sale before re-reading the immutable operation;
                # its original result remains replayable after cart edits.
                await self.cart_repository.lock_sale(tenant_id, request.sale_id)
                intent = await self.repository.get(tenant_id, request.operation_id)
            if intent is None:
                validated = await UnitCashConfirmationService(self.cart_repository).validate(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    allowed_branch_ids=allowed_branch_ids,
                    request=request,
                )
                quote = validated.quote
                intent = await self.repository.claim(
                    UnitCashIntent(
                        tenant_id=tenant_id,
                        id=request.operation_id,
                        branch_id=quote.branch_id,
                        sale_id=quote.sale_id,
                        register_id=quote.register_id,
                        shift_id=quote.shift_id,
                        cashier_user_id=quote.cashier_id,
                        request_hash=cash_intent_request_hash(request),
                        revision_hash=request.expected_revision_hash,
                        expected_total_amount=request.expected_total_amount,
                        cash_tendered=request.cash_tendered,
                        change_amount=validated.change_amount,
                        currency=request.currency,
                        quote_payload=quote.model_dump(mode="json"),
                        created_by=actor_id,
                    )
                )
            if intent.branch_id not in allowed_branch_ids or intent.cashier_user_id != actor_id:
                raise PermissionDeniedError("Cash confirmation branch or cashier access denied")
            await self.cart_repository.assert_actor(tenant_id, actor_id, intent.branch_id)
            if intent.request_hash != cash_intent_request_hash(request):
                raise ConflictError("Cash operation was already used for a different request")
            return self._result(intent, request)

    @staticmethod
    def _result(intent: UnitCashIntent, request: CashConfirmationRequestV2) -> SavedCashIntentV2:
        try:
            if intent.currency != "TJS":
                raise ValueError("saved cash intent currency must be TJS")
            original = CashConfirmationRequestV2(
                operation_id=intent.id,
                sale_id=intent.sale_id,
                expected_revision_hash=intent.revision_hash,
                expected_total_amount=intent.expected_total_amount,
                cash_tendered=intent.cash_tendered,
                currency="TJS",
            )
            quote = MixedCartQuoteV2.model_validate(intent.quote_payload)
            if original != request or (
                quote.tenant_id,
                quote.branch_id,
                quote.register_id,
                quote.shift_id,
                quote.cashier_id,
            ) != (
                intent.tenant_id,
                intent.branch_id,
                intent.register_id,
                intent.shift_id,
                intent.cashier_user_id,
            ):
                raise ValueError("saved cash intent scope differs from its original request")
            return SavedCashIntentV2(
                accepted_at=intent.created_at,
                confirmation=ValidatedCashConfirmationV2(
                    request=original, quote=quote, change_amount=intent.change_amount
                ),
            )
        except (ValidationError, ValueError) as error:
            raise AurumError("Stored cash confirmation is inconsistent") from error
