"""Fail-closed contract for the future non-routable Edge cash dispatcher."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import pytest

from app.core.errors import BusinessRuleError, PermissionDeniedError
from app.domains.pos.edge_cash import EdgeCashSaleCommand, EdgeCashSaleKernel
from app.domains.pos.schemas import SaleCheckoutResult
from app.domains.pos.service import CheckoutPaymentInput
from app.domains.sync.offline_auth import (
    OfflineAuthUnavailableError,
    VerifiedOfflinePrincipalV0,
    offline_auth_claims_hash,
    runtime_offline_auth_verifier,
)
from app.domains.sync.schemas import (
    OfflineAuthDeviceBindingV0,
    OfflineAuthGrantClaimsV0,
    OfflineAuthScopeV0,
    OfflinePosCommand,
    SignedOfflineAuthGrantV0,
)

AUTHENTICATED_AT = datetime(2026, 8, 9, 8, 0, tzinfo=UTC)


def _scope() -> OfflineAuthScopeV0:
    return OfflineAuthScopeV0(
        activation_id=UUID("10000000-0000-4000-8000-000000000001"),
        tenant_id=UUID("20000000-0000-4000-8000-000000000001"),
        branch_id=UUID("30000000-0000-4000-8000-000000000001"),
        edge_node_id=UUID("40000000-0000-4000-8000-000000000001"),
        register_id=UUID("50000000-0000-4000-8000-000000000001"),
        writer_epoch=7,
        user_id=UUID("60000000-0000-4000-8000-000000000001"),
        capability="cash_sale_v1",
    )


def _grant(scope: OfflineAuthScopeV0) -> SignedOfflineAuthGrantV0:
    claims = OfflineAuthGrantClaimsV0(
        schema_version=1,
        grant_id=UUID("70000000-0000-4000-8000-000000000001"),
        issuer="aurum-cloud",
        audience="aurum-edge-offline-auth-v0",
        auth_context="fresh-online-interactive",
        scope=scope,
        allowed_commands=("sale.cash.complete",),
        device_binding=OfflineAuthDeviceBindingV0(
            method="tpm2",
            key_id=UUID("80000000-0000-4000-8000-000000000001"),
            spki_sha256="1" * 64,
        ),
        policy_revision=11,
        subject_revision=13,
        authenticated_at=AUTHENTICATED_AT,
        issued_at=AUTHENTICATED_AT + timedelta(minutes=1),
        expires_at=AUTHENTICATED_AT + timedelta(hours=8),
    )
    return SignedOfflineAuthGrantV0(
        claims=claims,
        claims_hash=offline_auth_claims_hash(claims),
        signing_key_id=UUID("90000000-0000-4000-8000-000000000001"),
        signature_algorithm="ed25519-v1",
        signature="ab" * 64,
    )


def _result(scope: OfflineAuthScopeV0, operation_id: UUID) -> SaleCheckoutResult:
    now = AUTHENTICATED_AT + timedelta(minutes=2)
    return SaleCheckoutResult(
        event_id=uuid4(),
        sale_id=uuid4(),
        operation_id=operation_id,
        tenant_id=scope.tenant_id,
        branch_id=scope.branch_id,
        register_id=scope.register_id,
        shift_id=uuid4(),
        cashier_user_id=scope.user_id,
        receipt_number="000001",
        receipt_seq=1,
        created_at=now,
        completed_at=now,
        total_amount=Decimal("20.00"),
        currency="TJS",
        is_test=False,
        items=[],
        payments=[],
    )


@dataclass
class _Verifier:
    principal: VerifiedOfflinePrincipalV0
    calls: list[tuple[SignedOfflineAuthGrantV0, OfflinePosCommand]] = field(default_factory=list)

    async def authorize(
        self,
        grant: SignedOfflineAuthGrantV0,
        command: OfflinePosCommand,
    ) -> VerifiedOfflinePrincipalV0:
        self.calls.append((grant, command))
        return self.principal


@dataclass
class _Checkout:
    result: SaleCheckoutResult
    calls: list[dict[str, object]] = field(default_factory=list)

    async def checkout(
        self,
        *,
        tenant_id: UUID,
        register_id: UUID,
        cashier_user_id: UUID,
        operation_id: UUID,
        items: list[tuple[UUID, Decimal]],
        payments: list[CheckoutPaymentInput],
    ) -> SaleCheckoutResult:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "register_id": register_id,
                "cashier_user_id": cashier_user_id,
                "operation_id": operation_id,
                "items": items,
                "payments": payments,
            }
        )
        return self.result


def _principal(scope: OfflineAuthScopeV0) -> VerifiedOfflinePrincipalV0:
    return VerifiedOfflinePrincipalV0(
        grant_id=UUID("70000000-0000-4000-8000-000000000001"),
        user_id=scope.user_id,
        command="sale.cash.complete",
        expires_at=AUTHENTICATED_AT + timedelta(hours=8),
        scope=scope,
    )


def _command(scope: OfflineAuthScopeV0, operation_id: UUID) -> EdgeCashSaleCommand:
    return EdgeCashSaleCommand(
        scope=scope,
        operation_id=operation_id,
        items=((uuid4(), Decimal("2")),),
        paid_amount=Decimal("20.00"),
    )


async def test_each_cash_dispatch_is_reauthorized_and_cash_only() -> None:
    scope = _scope()
    operation_id = uuid4()
    grant = _grant(scope)
    verifier = _Verifier(_principal(scope))
    checkout = _Checkout(_result(scope, operation_id))
    kernel = EdgeCashSaleKernel(verifier=verifier, checkout=checkout)
    command = _command(scope, operation_id)

    first = await kernel.complete(grant=grant, command=command)
    replay = await kernel.complete(grant=grant, command=command)

    assert first == replay
    assert verifier.calls == [
        (grant, "sale.cash.complete"),
        (grant, "sale.cash.complete"),
    ]
    assert len(checkout.calls) == 2
    assert checkout.calls[0]["payments"] == [("cash", Decimal("20.00"), None)]


@pytest.mark.parametrize(
    ("field_name", "different_value"),
    [
        ("activation_id", UUID("10000000-0000-4000-8000-000000000002")),
        ("tenant_id", UUID("20000000-0000-4000-8000-000000000002")),
        ("branch_id", UUID("30000000-0000-4000-8000-000000000002")),
        ("edge_node_id", UUID("40000000-0000-4000-8000-000000000002")),
        ("register_id", UUID("50000000-0000-4000-8000-000000000002")),
        ("writer_epoch", 8),
        ("user_id", UUID("60000000-0000-4000-8000-000000000002")),
    ],
)
async def test_command_rejects_every_scope_mismatch(
    field_name: str,
    different_value: object,
) -> None:
    scope = _scope()
    operation_id = uuid4()
    checkout = _Checkout(_result(scope, operation_id))
    kernel = EdgeCashSaleKernel(verifier=_Verifier(_principal(scope)), checkout=checkout)
    mismatched = scope.model_copy(update={field_name: different_value})

    with pytest.raises(PermissionDeniedError):
        await kernel.complete(
            grant=_grant(scope),
            command=_command(mismatched, operation_id),
        )
    assert checkout.calls == []


async def test_principal_user_must_match_signed_scope() -> None:
    scope = _scope()
    operation_id = uuid4()
    bad_principal = _principal(scope)
    bad_principal = bad_principal.__class__(
        grant_id=bad_principal.grant_id,
        user_id=uuid4(),
        command=bad_principal.command,
        expires_at=bad_principal.expires_at,
        scope=scope,
    )
    checkout = _Checkout(_result(scope, operation_id))
    kernel = EdgeCashSaleKernel(verifier=_Verifier(bad_principal), checkout=checkout)

    with pytest.raises(PermissionDeniedError):
        await kernel.complete(grant=_grant(scope), command=_command(scope, operation_id))
    assert checkout.calls == []


@pytest.mark.parametrize(
    "command_update",
    [
        {"items": ()},
        {"paid_amount": Decimal("0")},
        {"paid_amount": Decimal("-1")},
    ],
)
async def test_invalid_cash_command_never_reaches_checkout(
    command_update: dict[str, object],
) -> None:
    scope = _scope()
    operation_id = uuid4()
    checkout = _Checkout(_result(scope, operation_id))
    kernel = EdgeCashSaleKernel(verifier=_Verifier(_principal(scope)), checkout=checkout)
    command = _command(scope, operation_id)
    invalid = command.__class__(
        scope=command.scope,
        operation_id=command.operation_id,
        items=cast(
            tuple[tuple[UUID, Decimal], ...],
            command_update.get("items", command.items),
        ),
        paid_amount=cast(
            Decimal,
            command_update.get("paid_amount", command.paid_amount),
        ),
    )

    with pytest.raises(BusinessRuleError):
        await kernel.complete(grant=_grant(scope), command=invalid)
    assert checkout.calls == []


async def test_runtime_deny_all_verifier_keeps_checkout_unreachable() -> None:
    scope = _scope()
    operation_id = uuid4()
    checkout = _Checkout(_result(scope, operation_id))
    kernel = EdgeCashSaleKernel(
        verifier=runtime_offline_auth_verifier(),
        checkout=checkout,
    )

    with pytest.raises(OfflineAuthUnavailableError):
        await kernel.complete(
            grant=_grant(scope),
            command=_command(scope, operation_id),
        )
    assert checkout.calls == []


async def test_checkout_result_cannot_escape_verified_scope() -> None:
    scope = _scope()
    operation_id = uuid4()
    escaped = _result(scope, operation_id).model_copy(update={"branch_id": uuid4()})
    checkout = _Checkout(escaped)
    kernel = EdgeCashSaleKernel(verifier=_Verifier(_principal(scope)), checkout=checkout)

    with pytest.raises(PermissionDeniedError):
        await kernel.complete(
            grant=_grant(scope),
            command=_command(scope, operation_id),
        )
