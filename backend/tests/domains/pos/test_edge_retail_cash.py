"""Bounded Edge admission reuses lease v1 without activating an offline POS."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.domains.pos.edge_retail_cash import (
    EdgeRetailCashCommandV1,
    EdgeRetailCashExecutionStateV1,
    EdgeRetailCashKernelV1,
    EdgeRetailCashKind,
    runtime_edge_retail_cash_kernel_v1,
)
from app.domains.pos.mixed_documents import MixedCheckoutResultV4
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from app.domains.sync.offline_auth import OfflineAuthContractError, OfflineAuthUnavailableError
from app.domains.sync.offline_auth_lease import VerifiedOfflineLeasePrincipalV1
from app.domains.sync.schemas import OfflineAuthScopeV0, OfflinePosCommand, SignedOfflineAuthLeaseV1
from tests.domains.pos.test_retail_sale_events import result as retail_result
from tests.domains.sync.test_offline_auth_lease_pipeline_v1 import NOW, _claims, _signed

RetailResult = PhysicalCheckoutResultV3 | MixedCheckoutResultV4
KINDS: tuple[EdgeRetailCashKind, ...] = ("mixed_cash", "physical_cash")
DENIALS = (OfflineAuthContractError, ValidationError)


@dataclass
class AllowVerifier:
    principal: VerifiedOfflineLeasePrincipalV1
    calls: list[tuple[SignedOfflineAuthLeaseV1, OfflinePosCommand]] = field(default_factory=list)

    async def authorize(
        self, lease: SignedOfflineAuthLeaseV1, command: OfflinePosCommand
    ) -> VerifiedOfflineLeasePrincipalV1:
        self.calls.append((lease, command))
        return self.principal


@dataclass
class Execution:
    """Synthetic atomic port: effects become durable only on context exit."""

    state: EdgeRetailCashExecutionStateV1
    result: RetailResult
    calls: list[EdgeRetailCashCommandV1] = field(default_factory=list)
    principals: list[VerifiedOfflineLeasePrincipalV1] = field(default_factory=list)
    entered: int = 0
    committed: int = 0
    rolled_back: int = 0
    effects: int = 0
    active: bool = False
    fail_commit: bool = False
    suppress_errors: bool = False
    commit_started: asyncio.Event | None = None
    release_commit: asyncio.Event | None = None

    @asynccontextmanager
    async def transaction(
        self, *, principal: VerifiedOfflineLeasePrincipalV1, command: EdgeRetailCashCommandV1
    ) -> AsyncIterator[Execution]:
        self.principals.append(principal)
        self.entered += 1
        self.active = True
        before = self.effects
        try:
            yield self
            if self.commit_started is not None:
                self.commit_started.set()
            if self.release_commit is not None:
                await self.release_commit.wait()
            if self.fail_commit:
                raise RuntimeError("synthetic commit failure")
            self.committed += 1
        except BaseException:
            self.effects = before
            self.rolled_back += 1
            if not self.suppress_errors:
                raise
        finally:
            self.active = False

    async def complete(self, *, command: EdgeRetailCashCommandV1) -> RetailResult:
        assert self.active, "completion must run inside the trusted transaction"
        self.calls.append(command)
        self.effects += 1
        return self.result


@dataclass
class Case:
    lease: SignedOfflineAuthLeaseV1
    command: EdgeRetailCashCommandV1
    verifier: AllowVerifier
    execution: Execution

    @property
    def kernel(self) -> EdgeRetailCashKernelV1:
        return EdgeRetailCashKernelV1(verifier=self.verifier, execution=self.execution)


def case(kind: EdgeRetailCashKind, scope: OfflineAuthScopeV0 | None = None) -> Case:
    lease = _signed(_claims())
    selected = scope or lease.claims.scope
    original = retail_result("physical" if kind == "physical_cash" else "mixed")
    result = original.model_copy(
        update={
            "tenant_id": selected.tenant_id,
            "branch_id": selected.branch_id,
            "register_id": selected.register_id,
            "cashier_id": selected.user_id,
            "operation_id": uuid4(),
        }
    )
    confirmation = CashConfirmationRequestV2(
        operation_id=result.operation_id,
        sale_id=result.sale_id,
        expected_revision_hash="a" * 64,
        expected_total_amount=result.total_amount,
        cash_tendered=result.total_amount + Decimal("10.00"),
    )
    command = EdgeRetailCashCommandV1(kind=kind, scope=selected, confirmation=confirmation)
    state = EdgeRetailCashExecutionStateV1(
        scope=selected,
        writer_mode="edge",
        node_status="active",
        supported_kinds=KINDS,
        sale_id=result.sale_id,
        shift_id=result.shift_id,
        saved_confirmation=confirmation,
    )
    principal = VerifiedOfflineLeasePrincipalV1(
        lease_id=lease.claims.lease_id,
        session_id=uuid4(),
        scope=selected,
        command="sale.cash.complete",
        expires_at=NOW,
    )
    return Case(lease, command, AllowVerifier(principal), Execution(state, result))


@pytest.mark.parametrize("kind", KINDS)
async def test_each_physical_or_mixed_dispatch_reauthorizes_before_atomic_execution(
    kind: EdgeRetailCashKind,
) -> None:
    c = case(kind)
    first = await c.kernel.complete(lease=c.lease, command=c.command)
    replay = await c.kernel.complete(lease=c.lease, command=deepcopy(c.command))
    assert first == replay == c.execution.result
    assert c.verifier.calls == [(c.lease, "sale.cash.complete")] * 2
    assert c.execution.calls == [c.command, c.command]
    assert c.execution.principals == [c.verifier.principal] * 2
    assert c.execution.committed == 2 and c.execution.rolled_back == 0
    assert not c.execution.active


SCOPE_FIELDS = (
    "activation_id",
    "tenant_id",
    "branch_id",
    "edge_node_id",
    "register_id",
    "user_id",
    "writer_epoch",
    "capability",
)


def different_scope(scope: OfflineAuthScopeV0, name: str) -> OfflineAuthScopeV0:
    value: object = uuid4()
    if name == "writer_epoch":
        value = scope.writer_epoch + 1
    elif name == "capability":
        value = "unapproved_physical_cash"
    return scope.model_copy(update={name: value})


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("name", SCOPE_FIELDS)
async def test_command_cannot_borrow_a_lease_from_another_scope(
    kind: EdgeRetailCashKind, name: str
) -> None:
    c = case(kind)
    command = c.command.model_copy(update={"scope": different_scope(c.command.scope, name)})
    with pytest.raises(DENIALS):
        await c.kernel.complete(lease=c.lease, command=command)
    assert c.execution.entered == 0 and c.execution.calls == []


@pytest.mark.parametrize("name", [*SCOPE_FIELDS, "lease_id", "command", "session_id"])
async def test_injected_verifier_cannot_return_an_unbound_principal(name: str) -> None:
    c = case("physical_cash")
    if name in SCOPE_FIELDS:
        c.verifier.principal = replace(
            c.verifier.principal, scope=different_scope(c.command.scope, name)
        )
    elif name == "lease_id":
        c.verifier.principal = replace(c.verifier.principal, lease_id=uuid4())
    elif name == "session_id":
        # Runtime dataclasses can carry forged values despite their annotations.
        c.verifier.principal = replace(
            c.verifier.principal, session_id=cast(UUID, "not-an-identifier")
        )
    else:
        c.verifier.principal = replace(c.verifier.principal, command="receipt.print")
    with pytest.raises(DENIALS):
        await c.kernel.complete(lease=c.lease, command=c.command)
    assert c.execution.entered == 0 and c.execution.calls == []


@pytest.mark.parametrize("name", SCOPE_FIELDS)
async def test_execution_requires_current_protected_writer_scope(name: str) -> None:
    c = case("mixed_cash")
    c.execution.state = c.execution.state.model_copy(
        update={"scope": different_scope(c.command.scope, name)}
    )
    with pytest.raises(DENIALS):
        await c.kernel.complete(lease=c.lease, command=c.command)
    assert c.execution.calls == [] and c.execution.committed == 0
    assert c.execution.rolled_back == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"writer_mode": "cloud"},
        {"node_status": "revoked"},
        {"supported_kinds": ("mixed_cash",)},
        {"supported_kinds": ()},
        {"supported_kinds": ("physical_cash", "mixed_cash")},
        {"supported_kinds": ("physical_cash", "physical_cash")},
        {"sale_id": UUID(int=999)},
        {"is_test": True},
    ],
)
async def test_ineligible_or_corrupt_execution_state_never_completes(
    changes: dict[str, object],
) -> None:
    c = case("physical_cash")
    c.execution.state = c.execution.state.model_copy(update=changes)
    with pytest.raises(DENIALS):
        await c.kernel.complete(lease=c.lease, command=c.command)
    assert c.execution.calls == [] and c.execution.committed == 0


@pytest.mark.parametrize(
    "name,value",
    [
        ("schema_version", 3),
        ("operation_id", UUID("10000000-0000-4000-8000-000000000099")),
        ("sale_id", UUID(int=999)),
        ("expected_revision_hash", "b" * 64),
        ("expected_total_amount", Decimal("24.00")),
        ("cash_tendered", Decimal("99.00")),
        ("currency", "USD"),
    ],
)
async def test_every_cash_confirmation_field_must_match_saved_confirmation(
    name: str, value: object
) -> None:
    c = case("mixed_cash")
    saved = c.command.confirmation.model_copy(update={name: value})
    c.execution.state = c.execution.state.model_copy(update={"saved_confirmation": saved})
    with pytest.raises(DENIALS):
        await c.kernel.complete(lease=c.lease, command=c.command)
    assert c.execution.calls == [] and c.execution.committed == 0


@pytest.mark.parametrize("kind", KINDS)
async def test_even_matching_saved_underpayment_never_completes(kind: EdgeRetailCashKind) -> None:
    c = case(kind)
    request = c.command.confirmation.model_copy(update={"cash_tendered": Decimal("0.01")})
    c.command = c.command.model_copy(update={"confirmation": request})
    c.execution.state = c.execution.state.model_copy(update={"saved_confirmation": request})
    with pytest.raises(DENIALS):
        await c.kernel.complete(lease=c.lease, command=c.command)
    assert c.execution.calls == [] and c.execution.committed == 0


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "name",
    [
        "kind",
        "tenant_id",
        "branch_id",
        "register_id",
        "cashier_id",
        "sale_id",
        "operation_id",
        "shift_id",
        "is_test",
        "total_amount",
        "card",
        "qr",
        "lines",
    ],
)
async def test_result_is_validated_inside_transaction_and_mismatch_rolls_back(
    kind: EdgeRetailCashKind, name: str
) -> None:
    c = case(kind)
    original = c.execution.result
    if name == "kind":
        c.execution.result = case(
            "mixed_cash" if kind == "physical_cash" else "physical_cash"
        ).execution.result
    elif name in ("card", "qr"):
        payment = original.payments[0].model_copy(update={"method": name})
        c.execution.result = original.model_copy(update={"payments": (payment,)})
    elif name == "total_amount":
        payload = original.model_dump(mode="python")
        payload["total_amount"] = original.total_amount + Decimal("0.01")
        lines = [row.model_dump(mode="python") for row in original.lines]
        lines[-1]["discount_amount"] -= Decimal("0.01")
        lines[-1]["net_amount"] += Decimal("0.01")
        payload["lines"] = lines
        payload["payments"] = [
            {**original.payments[0].model_dump(mode="python"), "amount": payload["total_amount"]}
        ]
        c.execution.result = type(original).model_validate(payload)
    else:
        value: object = uuid4()
        if name == "is_test":
            value = True
        elif name == "lines":
            value = ()
        c.execution.result = original.model_copy(update={name: value})
    with pytest.raises(DENIALS):
        await c.kernel.complete(lease=c.lease, command=c.command)
    assert c.execution.calls == [c.command]
    assert c.execution.rolled_back == 1 and c.execution.committed == 0
    assert c.execution.effects == 0 and not c.execution.active


async def test_no_result_escapes_until_execution_commit_finishes() -> None:
    c = case("physical_cash")
    c.execution.commit_started = asyncio.Event()
    c.execution.release_commit = asyncio.Event()
    task = asyncio.create_task(c.kernel.complete(lease=c.lease, command=c.command))
    try:
        await asyncio.wait_for(c.execution.commit_started.wait(), timeout=2)
        assert not task.done() and c.execution.committed == 0
        c.execution.release_commit.set()
        assert await asyncio.wait_for(task, timeout=2) == c.execution.result
        assert c.execution.committed == 1
    finally:
        c.execution.release_commit.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_execution_commit_failure_propagates_without_a_success_result() -> None:
    c = case("mixed_cash")
    c.execution.fail_commit = True
    with pytest.raises(RuntimeError, match="synthetic commit failure"):
        await c.kernel.complete(lease=c.lease, command=c.command)
    assert c.execution.rolled_back == 1 and c.execution.committed == 0
    assert c.execution.effects == 0


async def test_suppressed_result_validation_error_cannot_become_success() -> None:
    c = case("physical_cash")
    c.execution.suppress_errors = True
    c.execution.result = c.execution.result.model_copy(update={"branch_id": uuid4()})
    with pytest.raises(OfflineAuthContractError):
        await c.kernel.complete(lease=c.lease, command=c.command)
    assert c.execution.rolled_back == 1 and c.execution.effects == 0


@pytest.mark.parametrize("construction", ["copy", "construct"])
@pytest.mark.parametrize("target", ["command", "confirmation", "scope", "lease"])
async def test_unvalidated_objects_are_rechecked_before_any_port(
    construction: str, target: str
) -> None:
    c = case("physical_cash")
    model = {
        "command": c.command,
        "confirmation": c.command.confirmation,
        "scope": c.command.scope,
        "lease": c.lease,
    }[target]
    changes: dict[str, object] = {"schema_version": True}
    if target == "scope":
        changes = {"writer_epoch": True}
    elif target == "lease":
        changes = {"signature": "invalid"}
    if construction == "copy":
        malformed = deepcopy(model).model_copy(update=changes)
    else:
        malformed = type(model).model_construct(**{**dict(model), **changes})
    if target == "lease":
        c.lease = cast(SignedOfflineAuthLeaseV1, malformed)
    elif target == "command":
        c.command = cast(EdgeRetailCashCommandV1, malformed)
    else:
        c.command = c.command.model_copy(update={target: malformed})
    with pytest.raises(DENIALS):
        await c.kernel.complete(lease=c.lease, command=c.command)
    assert c.verifier.calls == [] and c.execution.entered == 0


@pytest.mark.parametrize("kind", ["unknown", "physical_electronic", "mixed_electronic", "refund"])
async def test_non_cash_or_unknown_command_is_denied_without_authority_or_execution(
    kind: str,
) -> None:
    c = case("physical_cash")
    command = c.command.model_copy(update={"kind": kind})
    with pytest.raises(ValidationError):
        await c.kernel.complete(lease=c.lease, command=command)
    assert c.verifier.calls == [] and c.execution.entered == 0


async def test_production_factory_never_authorizes_or_executes_a_cash_sale() -> None:
    c = case("physical_cash")
    with pytest.raises(OfflineAuthUnavailableError):
        await runtime_edge_retail_cash_kernel_v1().complete(lease=c.lease, command=c.command)


async def test_allowing_verifier_alone_cannot_open_production_execution() -> None:
    c = case("mixed_cash")
    runtime = runtime_edge_retail_cash_kernel_v1()
    runtime.verifier = c.verifier
    with pytest.raises(OfflineAuthUnavailableError):
        await runtime.complete(lease=c.lease, command=c.command)
    assert len(c.verifier.calls) == 1
