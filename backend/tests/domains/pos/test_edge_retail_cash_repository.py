"""Adapter contract tests; real native identity is proved by the CI LOGIN script."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from decimal import Decimal
from typing import cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.edge_retail_cash import (
    EdgeRetailCashCommandV1,
    EdgeRetailCashKind,
    runtime_edge_retail_cash_kernel_v1,
)
from app.domains.pos.edge_retail_cash_repository import (
    EdgeRetailCashPreparationRepository,
    preparation_payload,
)
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from app.domains.sync.offline_auth import OfflineAuthContractError
from app.domains.sync.schemas import OfflineAuthScopeV0


def command(kind: EdgeRetailCashKind = "physical_cash") -> EdgeRetailCashCommandV1:
    return EdgeRetailCashCommandV1(
        kind=kind,
        scope=OfflineAuthScopeV0(
            activation_id=uuid4(),
            tenant_id=uuid4(),
            branch_id=uuid4(),
            edge_node_id=uuid4(),
            register_id=uuid4(),
            writer_epoch=2,
            user_id=uuid4(),
            capability="cash_sale_v1",
        ),
        confirmation=CashConfirmationRequestV2(
            operation_id=uuid4(),
            sale_id=uuid4(),
            expected_revision_hash="a" * 64,
            expected_total_amount=Decimal("12.30"),
            cash_tendered=Decimal("20.00"),
        ),
    )


def response(request: EdgeRetailCashCommandV1) -> dict[str, object]:
    return {
        "schema_version": 1,
        "state": "prepared_not_executed",
        "kind": request.kind,
        "scope": request.scope.model_dump(mode="json"),
        "sale_id": str(request.confirmation.sale_id),
        "shift_id": str(uuid4()),
        "saved_confirmation": request.confirmation.model_dump(mode="json"),
    }


def session(raw: object, *, active: bool = True) -> Mock:
    result = Mock(spec=AsyncSession)
    result.in_transaction.return_value = active
    result.scalar = AsyncMock(return_value=raw)
    return result


@pytest.mark.parametrize("kind", ["physical_cash", "mixed_cash"])
async def test_preparation_binds_parameters_and_does_not_commit(kind: EdgeRetailCashKind) -> None:
    request = command(kind)
    fake = session(response(request))
    repository = EdgeRetailCashPreparationRepository(cast(AsyncSession, fake))
    result = await repository.prepare(request)
    assert result.state == "prepared_not_executed"
    assert result.scope == request.scope
    assert result.saved_confirmation == request.confirmation
    args = fake.scalar.call_args.args
    sql, params = str(args[0]), args[1]
    assert "public.dispatch_edge_cash_sale_v1(" in sql
    assert str(request.scope.tenant_id) not in sql
    assert params["request_hash"] == hashlib.sha256(params["payload"].encode()).hexdigest()
    assert params["cashier_user_id"] == request.scope.user_id
    assert params["operation_id"] == request.confirmation.operation_id
    assert json.loads(params["payload"])["confirmation"]["expected_total_amount"] == "12.30"
    fake.commit.assert_not_called()
    fake.rollback.assert_not_called()


async def test_preparation_needs_existing_transaction_before_any_sql() -> None:
    fake = session(None, active=False)
    with pytest.raises(OfflineAuthContractError, match="caller-owned transaction"):
        await EdgeRetailCashPreparationRepository(cast(AsyncSession, fake)).prepare(command())
    fake.scalar.assert_not_called()


@pytest.mark.parametrize(
    "field",
    [
        "activation_id",
        "tenant_id",
        "branch_id",
        "edge_node_id",
        "register_id",
        "user_id",
    ],
)
async def test_foreign_scope_is_rejected(field: str) -> None:
    request = command()
    raw = response(request)
    scope = cast(dict[str, object], raw["scope"])
    scope[field] = str(uuid4())
    fake = session(raw)
    with pytest.raises(OfflineAuthContractError):
        await EdgeRetailCashPreparationRepository(cast(AsyncSession, fake)).prepare(request)


@pytest.mark.parametrize(
    "field,value",
    [
        ("kind", "mixed_cash"),
        ("sale_id", "00000000-0000-4000-8000-000000000099"),
        ("state", "completed"),
        ("schema_version", 2),
        ("checkout_result", {}),
        ("shift_id", "invalid"),
    ],
)
async def test_unexpected_result_cannot_be_accepted(field: str, value: object) -> None:
    request = command()
    raw = response(request)
    raw[field] = value
    fake = session(raw)
    with pytest.raises((OfflineAuthContractError, ValidationError)):
        await EdgeRetailCashPreparationRepository(cast(AsyncSession, fake)).prepare(request)


@pytest.mark.parametrize(
    "field,value",
    [
        ("operation_id", "00000000-0000-4000-8000-000000000099"),
        ("sale_id", "00000000-0000-4000-8000-000000000099"),
        ("expected_revision_hash", "b" * 64),
        ("expected_total_amount", "12.31"),
        ("cash_tendered", "20.01"),
        ("currency", "USD"),
    ],
)
async def test_changed_saved_confirmation_is_rejected(field: str, value: str) -> None:
    request = command()
    raw = response(request)
    confirmation = cast(dict[str, object], raw["saved_confirmation"])
    confirmation[field] = value
    fake = session(raw)
    with pytest.raises((OfflineAuthContractError, ValidationError)):
        await EdgeRetailCashPreparationRepository(cast(AsyncSession, fake)).prepare(request)


async def test_repeat_reads_database_again_and_propagates_denial() -> None:
    request = command()
    fake = session(response(request))
    repository = EdgeRetailCashPreparationRepository(cast(AsyncSession, fake))
    await repository.prepare(request)
    fake.scalar.side_effect = RuntimeError("synthetic native denial")
    with pytest.raises(RuntimeError, match="synthetic native denial"):
        await repository.prepare(request)
    assert fake.scalar.await_count == 2
    fake.commit.assert_not_called()


def test_canonical_payload_is_stable_and_distinct_from_legacy_request() -> None:
    request = command()
    payload = preparation_payload(request)
    assert json.loads(payload)["profile"] == "retail_cash.prepare.v1"
    assert payload == preparation_payload(deepcopy(request))
    assert set(json.loads(payload)) == {"profile", "kind", "scope", "confirmation"}


async def test_forged_command_is_revalidated_before_database_access() -> None:
    request = command().model_copy(update={"kind": "card"})
    fake = session(None)
    with pytest.raises(ValidationError):
        await EdgeRetailCashPreparationRepository(cast(AsyncSession, fake)).prepare(request)
    fake.scalar.assert_not_called()


def test_preparation_is_not_installed_as_runtime_execution() -> None:
    kernel = runtime_edge_retail_cash_kernel_v1()
    assert type(kernel.execution).__name__ == "DenyAllEdgeRetailCashExecutionV1"
    assert not hasattr(EdgeRetailCashPreparationRepository, "complete")
    assert not hasattr(EdgeRetailCashPreparationRepository, "transaction")
