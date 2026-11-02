"""The native wire binds a physical oracle and never acquires app credentials."""

import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from typing import cast
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domains.pos import edge_physical_cash_execution as execution_module
from app.domains.pos.edge_physical_cash_execution import (
    NativeEdgePhysicalCashExecutionV1,
    physical_completion_payload,
)
from app.domains.pos.edge_physical_cash_plan import build_edge_physical_cash_plan
from app.domains.pos.edge_retail_cash import EdgeRetailCashCommandV1
from app.domains.pos.edge_retail_cash_replay import completed_command_request_hash
from app.domains.sync.offline_auth import OfflineAuthContractError
from tests.domains.pos.test_edge_physical_cash_plan import physical_draft
from tests.domains.pos.test_edge_retail_cash import case


def test_completion_wire_separates_movement_oracle_from_the_immutable_command_hash() -> None:
    draft = physical_draft((("unit", 2, Decimal("0.00")),))
    prepared = draft.inputs.preparation
    command = EdgeRetailCashCommandV1(
        kind="physical_cash", scope=prepared.scope, confirmation=prepared.saved_confirmation
    )
    plan = build_edge_physical_cash_plan(draft)
    encoded = physical_completion_payload(command, plan)
    wire = json.loads(encoded)
    assert wire.pop("plan") == plan.model_dump(mode="json")
    canonical_command = json.dumps(wire, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert (
        completed_command_request_hash(command)
        == hashlib.sha256(canonical_command.encode("utf-8")).hexdigest()
    )
    assert hashlib.sha256(encoded.encode("utf-8")).hexdigest() != (
        completed_command_request_hash(command)
    )


@pytest.mark.parametrize(
    "field", ["tenant_id", "branch_id", "sale_id", "register_id", "cashier_id"]
)
def test_foreign_quote_is_refused_before_a_database_call(field: str) -> None:
    draft = physical_draft((("unit", 2, Decimal("0.00")),))
    prepared = draft.inputs.preparation
    command = EdgeRetailCashCommandV1(
        kind="physical_cash", scope=prepared.scope, confirmation=prepared.saved_confirmation
    )
    plan = build_edge_physical_cash_plan(draft)
    forged = plan.model_copy(update={"quote": plan.quote.model_copy(update={field: uuid4()})})
    with pytest.raises(OfflineAuthContractError, match="plan differs"):
        physical_completion_payload(command, forged)


def test_complete_wire_limit_covers_the_command_and_plan_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft = physical_draft((("unit", 2, Decimal("0.00")),))
    prepared = draft.inputs.preparation
    command = EdgeRetailCashCommandV1(
        kind="physical_cash", scope=prepared.scope, confirmation=prepared.saved_confirmation
    )
    plan = build_edge_physical_cash_plan(draft)
    exact_size = len(physical_completion_payload(command, plan).encode("utf-8"))
    monkeypatch.setattr(execution_module, "MAX_COMPLETE_BYTES", exact_size - 1)
    with pytest.raises(OfflineAuthContractError, match="complete wire bound"):
        physical_completion_payload(command, plan)


@pytest.mark.parametrize("fault", ["scope", "mixed", "permission"])
async def test_unbound_principal_or_unsupported_kind_never_opens_a_session(fault: str) -> None:
    c = case("physical_cash")
    command, principal = c.command, c.verifier.principal
    if fault == "scope":
        principal = replace(
            principal, scope=principal.scope.model_copy(update={"user_id": uuid4()})
        )
    elif fault == "mixed":
        command = command.model_copy(update={"kind": "mixed_cash"})
    else:
        principal = replace(principal, command="operation.result.read")
    factory = MagicMock()
    adapter = NativeEdgePhysicalCashExecutionV1(cast(async_sessionmaker[AsyncSession], factory))
    with pytest.raises(OfflineAuthContractError, match="differs from its principal"):
        async with adapter.transaction(principal=principal, command=command):
            raise AssertionError("Unbound native transaction entered")
    factory.assert_not_called()
