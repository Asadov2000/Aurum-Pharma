"""Exercise the existing v1 decision pipeline before the bounded retail port."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest

from app.domains.pos.edge_retail_cash import EdgeRetailCashKernelV1, EdgeRetailCashKind
from app.domains.sync.offline_auth import OfflineAuthContractError, OfflineAuthUnavailableError
from tests.domains.pos.test_edge_retail_cash import KINDS, case, different_scope
from tests.domains.sync.test_offline_auth_lease_pipeline_v1 import (
    _changed,
    _require_confirmation,
)
from tests.domains.sync.test_offline_auth_lease_pipeline_v1 import (
    _Harness as LeaseHarness,
)
from tests.domains.sync.test_offline_auth_lease_pipeline_v1 import (
    harness as lease_harness,
)

# This fixture intentionally reuses the established synthetic trusted adapters;
# these tests do not imply that a native production Edge runtime has been enabled.
__all__ = ["lease_harness"]


@pytest.mark.parametrize("kind", KINDS)
async def test_existing_v1_pipeline_authorizes_both_cash_kinds_without_new_commands(
    lease_harness: LeaseHarness, kind: EdgeRetailCashKind
) -> None:
    c = case(kind, lease_harness.lease.claims.scope)
    kernel = EdgeRetailCashKernelV1(verifier=lease_harness.pipeline, execution=c.execution)
    first = await kernel.complete(lease=lease_harness.lease, command=c.command)
    replay = await kernel.complete(lease=lease_harness.lease, command=c.command)
    assert first == replay == c.execution.result
    assert c.execution.committed == 2 and c.execution.rolled_back == 0
    assert len(lease_harness.state.observations) == 2
    assert lease_harness.state.events.count("signature") == 2
    assert lease_harness.state.events.count("commit") == 2
    assert all(p.command == "sale.cash.complete" for p in c.execution.principals)
    assert lease_harness.state.session is not None
    assert all(
        p.session_id == lease_harness.state.session.session_id for p in c.execution.principals
    )


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize(
    "failure",
    ["expired", "revoked", "missing_session", "reconfirmation", "commit", "scope", "protected"],
)
async def test_lease_denials_never_enter_retail_execution(
    lease_harness: LeaseHarness, kind: EdgeRetailCashKind, failure: str
) -> None:
    c = case(kind, lease_harness.lease.claims.scope)
    state = lease_harness.state
    assert state.authorization is not None and state.session is not None
    if failure == "expired":
        state.clock = replace(state.clock, utc_now=lease_harness.lease.claims.expires_at)
    elif failure == "revoked":
        state.authorization = _changed(state.authorization, "status", "revoked")
    elif failure == "missing_session":
        state.session = None
    elif failure == "reconfirmation":
        _require_confirmation(lease_harness)
        assert state.session is not None
        state.session = _changed(state.session, "confirmation", None)
    elif failure == "commit":
        state.error_at = "commit"
    elif failure == "scope":
        state.session = _changed(state.session, "scope.branch_id", uuid4())
    else:
        state.sealed_tenant_access_revision = state.authorization.tenant_access_revision + 1
    kernel = EdgeRetailCashKernelV1(verifier=lease_harness.pipeline, execution=c.execution)
    with pytest.raises(OfflineAuthUnavailableError):
        await kernel.complete(lease=lease_harness.lease, command=c.command)
    assert c.execution.entered == 0 and c.execution.calls == []
    assert c.execution.effects == 0 and c.execution.committed == 0
    assert "rollback" in state.events and not state.lock.locked()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("field_name", ["tenant_id", "branch_id", "user_id", "writer_epoch"])
async def test_valid_lease_cannot_replace_current_locked_writer_or_resource_scope(
    lease_harness: LeaseHarness, kind: EdgeRetailCashKind, field_name: str
) -> None:
    c = case(kind, lease_harness.lease.claims.scope)
    c.execution.state = c.execution.state.model_copy(
        update={"scope": different_scope(c.command.scope, field_name)}
    )
    kernel = EdgeRetailCashKernelV1(verifier=lease_harness.pipeline, execution=c.execution)
    with pytest.raises(OfflineAuthContractError):
        await kernel.complete(lease=lease_harness.lease, command=c.command)
    assert lease_harness.state.events.count("commit") == 1
    assert c.execution.entered == 1 and c.execution.calls == []
    assert c.execution.rolled_back == 1 and c.execution.committed == 0


async def test_retail_execution_waits_for_durable_v1_authorization_commit(
    lease_harness: LeaseHarness,
) -> None:
    c = case("physical_cash", lease_harness.lease.claims.scope)
    lease_harness.state.commit_started = asyncio.Event()
    lease_harness.state.release_commit = asyncio.Event()
    kernel = EdgeRetailCashKernelV1(verifier=lease_harness.pipeline, execution=c.execution)
    task = asyncio.create_task(kernel.complete(lease=lease_harness.lease, command=c.command))
    try:
        await asyncio.wait_for(lease_harness.state.commit_started.wait(), timeout=2)
        assert not task.done() and c.execution.entered == 0
        lease_harness.state.release_commit.set()
        assert await asyncio.wait_for(task, timeout=2) == c.execution.result
        assert c.execution.committed == 1
    finally:
        lease_harness.state.release_commit.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
