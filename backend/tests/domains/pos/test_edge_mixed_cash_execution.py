"""Mixed native wire keeps both stock kinds bound to one confirmed command."""

import hashlib
import json
from dataclasses import replace
from typing import cast
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domains.pos import edge_mixed_cash_execution as execution_module
from app.domains.pos.edge_mixed_cash_execution import (
    NativeEdgeMixedCashExecutionV1,
    mixed_completion_payload,
)
from app.domains.pos.edge_mixed_cash_plan import build_edge_mixed_cash_plan
from app.domains.pos.edge_retail_cash import EdgeRetailCashCommandV1
from app.domains.pos.edge_retail_cash_replay import completed_command_request_hash
from app.domains.sync.offline_auth import OfflineAuthContractError
from tests.domains.pos.test_edge_mixed_cash_plan import mixed_draft
from tests.domains.pos.test_edge_retail_cash import case


def test_mixed_wire_keeps_whole_quote_and_separate_canonical_command_hash() -> None:
    draft = mixed_draft()
    prepared = draft.inputs.preparation
    command = EdgeRetailCashCommandV1(
        kind="mixed_cash", scope=prepared.scope, confirmation=prepared.saved_confirmation
    )
    plan = build_edge_mixed_cash_plan(draft)
    encoded = mixed_completion_payload(command, plan)
    wire = json.loads(encoded)
    assert set(wire) == {"profile", "kind", "scope", "confirmation", "plan"}
    assert wire.pop("plan") == plan.model_dump(mode="json")
    assert {line.inventory_kind for line in plan.quote.lines} == {"legacy_pack", "physical_unit"}
    assert str(plan.quote.total_amount) == "2.52"
    assert [movement.kind for movement in plan.movements] == ["pack_open", "unit_take"]
    assert sum(movement.units_taken for movement in plan.movements) == 2
    canonical = json.dumps(wire, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert completed_command_request_hash(command) == hashlib.sha256(canonical.encode()).hexdigest()
    assert hashlib.sha256(encoded.encode()).hexdigest() != completed_command_request_hash(command)


@pytest.mark.parametrize(
    "field", ["tenant_id", "branch_id", "sale_id", "register_id", "cashier_id"]
)
def test_foreign_mixed_quote_is_refused_before_a_database_call(field: str) -> None:
    draft = mixed_draft()
    prepared = draft.inputs.preparation
    command = EdgeRetailCashCommandV1(
        kind="mixed_cash", scope=prepared.scope, confirmation=prepared.saved_confirmation
    )
    plan = build_edge_mixed_cash_plan(draft)
    forged = plan.model_copy(update={"quote": plan.quote.model_copy(update={field: uuid4()})})
    with pytest.raises(OfflineAuthContractError, match="plan differs"):
        mixed_completion_payload(command, forged)


def test_mixed_complete_wire_limit_includes_command_and_plan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = mixed_draft()
    prepared = draft.inputs.preparation
    command = EdgeRetailCashCommandV1(
        kind="mixed_cash", scope=prepared.scope, confirmation=prepared.saved_confirmation
    )
    plan = build_edge_mixed_cash_plan(draft)
    exact_size = len(mixed_completion_payload(command, plan).encode("utf-8"))
    monkeypatch.setattr(execution_module, "MAX_COMPLETE_BYTES", exact_size - 1)
    with pytest.raises(OfflineAuthContractError, match="complete wire bound"):
        mixed_completion_payload(command, plan)


@pytest.mark.parametrize("fault", ["scope", "physical", "permission"])
async def test_unbound_mixed_principal_never_opens_a_session(fault: str) -> None:
    c = case("mixed_cash")
    command, principal = c.command, c.verifier.principal
    if fault == "scope":
        principal = replace(
            principal, scope=principal.scope.model_copy(update={"user_id": uuid4()})
        )
    elif fault == "physical":
        command = command.model_copy(update={"kind": "physical_cash"})
    else:
        principal = replace(principal, command="operation.result.read")
    factory = MagicMock()
    adapter = NativeEdgeMixedCashExecutionV1(cast(async_sessionmaker[AsyncSession], factory))
    with pytest.raises(OfflineAuthContractError, match="differs from its principal"):
        async with adapter.transaction(principal=principal, command=command):
            raise AssertionError("Unbound mixed native transaction entered")
    factory.assert_not_called()
