"""Non-routable authorization kernel for a future Edge cash-sale dispatcher."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from app.core.errors import BusinessRuleError, PermissionDeniedError
from app.domains.pos.schemas import SaleCheckoutResult
from app.domains.pos.service import CheckoutPaymentInput
from app.domains.sync.offline_auth import OfflineAuthVerifier
from app.domains.sync.schemas import OfflineAuthScopeV0, SignedOfflineAuthGrantV0


@dataclass(frozen=True, slots=True)
class EdgeCashSaleCommand:
    scope: OfflineAuthScopeV0
    operation_id: UUID
    items: tuple[tuple[UUID, Decimal], ...]
    paid_amount: Decimal


class EdgeCashCheckoutPort(Protocol):
    async def checkout(
        self,
        *,
        tenant_id: UUID,
        register_id: UUID,
        cashier_user_id: UUID,
        operation_id: UUID,
        items: list[tuple[UUID, Decimal]],
        payments: list[CheckoutPaymentInput],
    ) -> SaleCheckoutResult: ...


class EdgeCashSaleKernel:
    """Authorize and dispatch the single operation allowed by ``cash_sale_v1``.

    This class intentionally has no route or runtime composition. Every command
    is re-authorized by the injected Edge Security Authority verifier.
    """

    def __init__(
        self,
        *,
        verifier: OfflineAuthVerifier,
        checkout: EdgeCashCheckoutPort,
    ) -> None:
        self.verifier = verifier
        self.checkout = checkout

    async def complete(
        self,
        *,
        grant: SignedOfflineAuthGrantV0,
        command: EdgeCashSaleCommand,
    ) -> SaleCheckoutResult:
        principal = await self.verifier.authorize(grant, "sale.cash.complete")
        if principal.command != "sale.cash.complete":
            raise PermissionDeniedError("Offline principal does not allow a cash sale")
        if principal.scope.capability != "cash_sale_v1" or principal.scope != command.scope:
            raise PermissionDeniedError("Offline principal scope does not match the command")
        if principal.user_id != principal.scope.user_id:
            raise PermissionDeniedError("Offline principal user scope does not match")
        if not command.items:
            raise BusinessRuleError("Cash sale must contain at least one item")
        if command.paid_amount <= 0:
            raise BusinessRuleError("Cash payment must be positive")

        scope = principal.scope
        result = await self.checkout.checkout(
            tenant_id=scope.tenant_id,
            register_id=scope.register_id,
            cashier_user_id=principal.user_id,
            operation_id=command.operation_id,
            items=list(command.items),
            payments=[("cash", command.paid_amount, None)],
        )
        if (
            result.tenant_id != scope.tenant_id
            or result.branch_id != scope.branch_id
            or result.register_id != scope.register_id
            or result.cashier_user_id != principal.user_id
        ):
            raise PermissionDeniedError("Cash sale result escaped the verified Edge scope")
        return result
