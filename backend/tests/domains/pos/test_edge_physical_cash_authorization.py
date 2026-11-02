"""Trusted-time rechecks must fail inside the still-uncommitted cash transaction."""

from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest

from app.domains.pos.edge_retail_cash import (
    EdgeRetailCashKernelV1,
    EdgeRetailCashReplayStateV1,
)
from app.domains.sync.offline_auth import OfflineAuthContractError, OfflineAuthUnavailableError
from app.domains.sync.offline_auth_lease import VerifiedOfflineLeasePrincipalV1
from app.domains.sync.schemas import OfflinePosCommand, SignedOfflineAuthLeaseV1
from tests.domains.pos.test_edge_retail_cash import case


class ChangingVerifier:
    def __init__(
        self,
        original: VerifiedOfflineLeasePrincipalV1,
        *,
        at: int,
        changed: VerifiedOfflineLeasePrincipalV1 | None = None,
    ) -> None:
        self.original = original
        self.at = at
        self.changed = changed
        self.calls = 0

    async def authorize(
        self, lease: SignedOfflineAuthLeaseV1, command: OfflinePosCommand
    ) -> VerifiedOfflineLeasePrincipalV1:
        assert command == "sale.cash.complete" and lease.claims.lease_id == self.original.lease_id
        self.calls += 1
        if self.calls != self.at:
            return self.original
        if self.changed is None:
            raise OfflineAuthUnavailableError("Synthetic trusted authority denial")
        return self.changed


@pytest.mark.parametrize("at", [2, 3], ids=["after-locks", "before-commit"])
async def test_revocation_after_wait_or_posting_rolls_back_without_result(at: int) -> None:
    c = case("physical_cash")
    verifier = ChangingVerifier(c.verifier.principal, at=at)
    kernel = EdgeRetailCashKernelV1(verifier=verifier, execution=c.execution)
    with pytest.raises(OfflineAuthUnavailableError):
        await kernel.complete(lease=c.lease, command=c.command)
    assert c.execution.effects == 0 and c.execution.committed == 0
    assert c.execution.rolled_back == 1
    assert len(c.execution.calls) == (1 if at == 3 else 0)
    assert verifier.calls == at


@pytest.mark.parametrize("at", [2, 3])
@pytest.mark.parametrize("field", ["session", "deadline"])
async def test_renewed_session_or_changed_deadline_cannot_extend_admitted_operation(
    at: int, field: str
) -> None:
    c = case("physical_cash")
    principal = c.verifier.principal
    changed = (
        replace(principal, session_id=uuid4())
        if field == "session"
        else replace(principal, expires_at=principal.expires_at + timedelta(seconds=1))
    )
    kernel = EdgeRetailCashKernelV1(
        verifier=ChangingVerifier(principal, at=at, changed=changed), execution=c.execution
    )
    with pytest.raises(OfflineAuthContractError, match="authorization changed"):
        await kernel.complete(lease=c.lease, command=c.command)
    assert c.execution.effects == 0 and c.execution.committed == 0
    assert c.execution.rolled_back == 1


async def test_suppressed_final_authority_failure_cannot_expose_a_result() -> None:
    c = case("physical_cash")
    c.execution.suppress_errors = True
    kernel = EdgeRetailCashKernelV1(
        verifier=ChangingVerifier(c.verifier.principal, at=3), execution=c.execution
    )
    with pytest.raises(OfflineAuthContractError, match="transaction did not complete"):
        await kernel.complete(lease=c.lease, command=c.command)
    assert c.execution.effects == 0 and c.execution.committed == 0


async def test_historical_replay_does_not_invent_current_writer_status() -> None:
    c = case("physical_cash")
    c.execution.state = EdgeRetailCashReplayStateV1(
        scope=c.command.scope,
        kind=c.command.kind,
        sale_id=c.command.confirmation.sale_id,
        shift_id=c.execution.result.shift_id,
        saved_confirmation=c.command.confirmation,
    )
    assert "writer_mode" not in c.execution.state.model_dump()
    assert "node_status" not in c.execution.state.model_dump()
    result = await c.kernel.complete(lease=c.lease, command=c.command)
    assert result == c.execution.result
    assert len(c.verifier.calls) == 3
    assert c.execution.committed == 1


@pytest.mark.parametrize("field", ["sale_id", "kind"])
async def test_historical_state_is_still_bound_to_the_exact_command(field: str) -> None:
    c = case("physical_cash")
    c.execution.state = EdgeRetailCashReplayStateV1(
        scope=c.command.scope,
        kind="mixed_cash" if field == "kind" else c.command.kind,
        sale_id=uuid4() if field == "sale_id" else c.command.confirmation.sale_id,
        shift_id=c.execution.result.shift_id,
        saved_confirmation=c.command.confirmation,
    )
    with pytest.raises(OfflineAuthContractError, match="Historical execution state"):
        await c.kernel.complete(lease=c.lease, command=c.command)
    assert c.execution.calls == [] and c.execution.committed == 0
