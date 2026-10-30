"""Nonroutable Edge cash bridge; production execution remains fail-closed.

An injected trusted execution port must bind actual database identity and retain
writer, resource and saved-confirmation locks through result validation and commit.
The contracts below do not establish database identity or enable an Edge writer.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Literal, Never, Protocol, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.domains.pos.mixed_documents import MixedCheckoutResultV4
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from app.domains.pos.unit_contracts import FrozenContract
from app.domains.sync.offline_auth import (
    OFFLINE_AUTH_UNAVAILABLE_MESSAGE,
    OfflineAuthContractError,
    OfflineAuthUnavailableError,
)
from app.domains.sync.offline_auth_lease import (
    VerifiedOfflineLeasePrincipalV1,
    runtime_offline_auth_lease_verifier,
)
from app.domains.sync.schemas import (
    OfflineAuthScopeV0,
    OfflinePosCommand,
    SignedOfflineAuthLeaseV1,
)

type EdgeRetailCashKind = Literal["physical_cash", "mixed_cash"]


class EdgeRetailCashCommandV1(FrozenContract):
    schema_version: Literal[1] = 1
    kind: EdgeRetailCashKind
    scope: OfflineAuthScopeV0
    confirmation: CashConfirmationRequestV2

    @model_validator(mode="after")
    def sufficient_cash(self) -> Self:
        if self.confirmation.cash_tendered < self.confirmation.expected_total_amount:
            raise ValueError("cash tendered must cover the confirmed total")
        return self


class EdgeRetailCashExecutionStateV1(FrozenContract):
    scope: OfflineAuthScopeV0
    writer_mode: Literal["edge"]
    node_status: Literal["active"]
    supported_kinds: tuple[EdgeRetailCashKind, ...] = Field(min_length=1, max_length=2)
    sale_id: UUID
    shift_id: UUID
    is_test: Literal[False] = False
    saved_confirmation: CashConfirmationRequestV2

    @field_validator("supported_kinds")
    @classmethod
    def unique_sorted_kinds(
        cls, value: tuple[EdgeRetailCashKind, ...]
    ) -> tuple[EdgeRetailCashKind, ...]:
        if value != tuple(sorted(set(value))):
            raise ValueError("supported cash kinds must be unique and sorted")
        return value

    @field_validator("is_test", mode="before")
    @classmethod
    def production_sale(cls, value: object) -> object:
        if value is not False:
            raise ValueError("Edge retail cash requires a production sale")
        return value


class EdgeRetailCashLeaseVerifierV1(Protocol):
    async def authorize(
        self, lease: SignedOfflineAuthLeaseV1, command: OfflinePosCommand
    ) -> VerifiedOfflineLeasePrincipalV1: ...


class EdgeRetailCashExecutorV1(Protocol):
    @property
    def state(self) -> EdgeRetailCashExecutionStateV1: ...

    async def complete(
        self, *, command: EdgeRetailCashCommandV1
    ) -> PhysicalCheckoutResultV3 | MixedCheckoutResultV4: ...


class EdgeRetailCashExecutionPortV1(Protocol):
    """Trusted composition only, never an HTTP caller-provided dependency.

    The adapter must enforce the actual Edge database role and dispatcher, lock
    the writer epoch, active node, sale, shift and persisted confirmation, and
    retain those locks and its transaction through validation and durable commit.
    A Python state flag cannot replace database identity or writer admission.
    """

    def transaction(
        self,
        *,
        principal: VerifiedOfflineLeasePrincipalV1,
        command: EdgeRetailCashCommandV1,
    ) -> AbstractAsyncContextManager[EdgeRetailCashExecutorV1]: ...


def _assert_principal(
    principal: VerifiedOfflineLeasePrincipalV1,
    lease: SignedOfflineAuthLeaseV1,
    command: EdgeRetailCashCommandV1,
) -> None:
    scope = OfflineAuthScopeV0.model_validate(principal.scope.model_dump(mode="python"))
    if (
        principal.command != "sale.cash.complete"
        or principal.lease_id != lease.claims.lease_id
        or not isinstance(principal.session_id, UUID)
        or principal.session_id.version != 4
        or scope != lease.claims.scope
        or scope != command.scope
    ):
        raise OfflineAuthContractError("Authorized principal differs from the cash command")


def _assert_execution_state(
    state: EdgeRetailCashExecutionStateV1, command: EdgeRetailCashCommandV1
) -> None:
    if (
        state.scope != command.scope
        or state.writer_mode != "edge"
        or state.node_status != "active"
        or command.kind not in state.supported_kinds
        or state.sale_id != command.confirmation.sale_id
        or state.is_test is not False
        or state.saved_confirmation != command.confirmation
    ):
        raise OfflineAuthContractError("Locked execution state differs from the cash command")


def _validated_result(
    raw: PhysicalCheckoutResultV3 | MixedCheckoutResultV4,
    command: EdgeRetailCashCommandV1,
    state: EdgeRetailCashExecutionStateV1,
) -> PhysicalCheckoutResultV3 | MixedCheckoutResultV4:
    result: PhysicalCheckoutResultV3 | MixedCheckoutResultV4
    if command.kind == "physical_cash" and type(raw) is PhysicalCheckoutResultV3:
        result = PhysicalCheckoutResultV3.model_validate(raw.model_dump(mode="python"))
    elif command.kind == "mixed_cash" and type(raw) is MixedCheckoutResultV4:
        result = MixedCheckoutResultV4.model_validate(raw.model_dump(mode="python"))
    else:
        raise OfflineAuthContractError("Cash execution returned an unexpected result kind")
    if (
        result.tenant_id != command.scope.tenant_id
        or result.branch_id != command.scope.branch_id
        or result.register_id != command.scope.register_id
        or result.cashier_id != command.scope.user_id
        or result.sale_id != command.confirmation.sale_id
        or result.operation_id != command.confirmation.operation_id
        or result.shift_id != state.shift_id
        or result.is_test is not False
        or result.total_amount != command.confirmation.expected_total_amount
        or any(payment.method != "cash" for payment in result.payments)
    ):
        raise OfflineAuthContractError("Cash execution result differs from the authorized sale")
    return result


class EdgeRetailCashKernelV1:
    def __init__(
        self, *, verifier: EdgeRetailCashLeaseVerifierV1, execution: EdgeRetailCashExecutionPortV1
    ) -> None:
        self.verifier = verifier
        self.execution = execution

    async def complete(
        self, *, lease: SignedOfflineAuthLeaseV1, command: EdgeRetailCashCommandV1
    ) -> PhysicalCheckoutResultV3 | MixedCheckoutResultV4:
        # Dump first: model_construct/model_copy must not bypass nested validation.
        command = EdgeRetailCashCommandV1.model_validate(command.model_dump(mode="python"))
        lease = SignedOfflineAuthLeaseV1.model_validate(lease.model_dump(mode="python"))
        principal = await self.verifier.authorize(lease, "sale.cash.complete")
        _assert_principal(principal, lease, command)
        result: PhysicalCheckoutResultV3 | MixedCheckoutResultV4 | None = None
        async with self.execution.transaction(principal=principal, command=command) as executor:
            state = EdgeRetailCashExecutionStateV1.model_validate(
                executor.state.model_dump(mode="python")
            )
            _assert_execution_state(state, command)
            raw = await executor.complete(command=command)
            result = _validated_result(raw, command, state)
        # An unsuccessful __aexit__/durable commit must never expose a sale result.
        if result is None:
            raise OfflineAuthContractError("Cash execution transaction did not complete")
        return result


class DenyAllEdgeRetailCashExecutionV1:
    """No production adapter until Native Edge DB identity and recovery acceptance."""

    def transaction(
        self,
        *,
        principal: VerifiedOfflineLeasePrincipalV1,
        command: EdgeRetailCashCommandV1,
    ) -> Never:
        del principal, command
        raise OfflineAuthUnavailableError(OFFLINE_AUTH_UNAVAILABLE_MESSAGE)


def runtime_edge_retail_cash_kernel_v1() -> EdgeRetailCashKernelV1:
    """No route or environment switch enables this bounded composition."""
    return EdgeRetailCashKernelV1(
        verifier=runtime_offline_auth_lease_verifier(), execution=DenyAllEdgeRetailCashExecutionV1()
    )
