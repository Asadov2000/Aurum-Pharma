"""Anchored native replay transport; synthetic responses are not DB authority."""

import hashlib
import json
from contextlib import nullcontext
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
)
from app.domains.pos.edge_retail_cash_replay import (
    COMPLETED_PROFILE,
    MAX_REPLAY_BYTES,
    EdgeRetailCashReplayRepository,
    EdgeRetailCashReplayV1,
    completed_command_request_hash,
    replay_payload,
)
from app.domains.pos.mixed_documents import MixedCheckoutResultV4
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from app.domains.pos.unit_contracts import CheckoutPaymentV2
from app.domains.sync.offline_auth import OfflineAuthContractError
from tests.domains.pos.test_edge_retail_cash_repository import command as preparation_command
from tests.domains.pos.test_mixed_documents import mixed_payload
from tests.domains.pos.test_physical_documents import result_payload


def fixture(
    kind: EdgeRetailCashKind = "physical_cash",
) -> tuple[EdgeRetailCashCommandV1, dict[str, object]]:
    scope = preparation_command(kind).scope
    original = (
        PhysicalCheckoutResultV3.model_validate(result_payload(is_test=False))
        if kind == "physical_cash"
        else MixedCheckoutResultV4.model_validate(mixed_payload(is_test=False))
    )
    command = EdgeRetailCashCommandV1(
        kind=kind,
        scope=scope,
        confirmation=CashConfirmationRequestV2(
            operation_id=uuid4(),
            sale_id=original.sale_id,
            expected_revision_hash="a" * 64,
            expected_total_amount=original.total_amount,
            cash_tendered=original.total_amount + Decimal("10.00"),
        ),
    )
    result = original.model_copy(
        update={
            "tenant_id": scope.tenant_id,
            "branch_id": scope.branch_id,
            "register_id": scope.register_id,
            "cashier_id": scope.user_id,
            "operation_id": command.confirmation.operation_id,
            "event_id": command.confirmation.operation_id,
            "stock_command_id": command.confirmation.operation_id,
            "payments": (
                CheckoutPaymentV2(
                    id=command.confirmation.operation_id,
                    method="cash",
                    amount=command.confirmation.expected_total_amount,
                ),
            ),
        }
    )
    wrapper = {
        "profile": COMPLETED_PROFILE,
        "command_type": "sale.cash.complete",
        "kind": kind,
        "scope": command.scope.model_dump(mode="json"),
        "confirmation": command.confirmation.model_dump(mode="json"),
        "result": result.model_dump(mode="json"),
        "operation_id": str(result.operation_id),
        "sale_id": str(result.sale_id),
        "receipt_number": result.receipt_number,
        "total_amount": format(result.total_amount, ".2f"),
        "currency": "TJS",
    }
    return command, wrapper


def session(raw: object, *, active: bool = True) -> Mock:
    fake = Mock(spec=AsyncSession)
    fake.in_transaction.return_value = active
    fake.new, fake.dirty, fake.deleted = set(), set(), set()
    fake.no_autoflush = nullcontext()
    fake.scalar = AsyncMock(return_value=raw)
    return fake


@pytest.mark.parametrize("kind", ["physical_cash", "mixed_cash"])
async def test_replay_restores_the_exact_result_without_committing(
    kind: EdgeRetailCashKind,
) -> None:
    command, wrapper = fixture(kind)
    fake = session(wrapper)
    repository = EdgeRetailCashReplayRepository(cast(AsyncSession, fake))
    first = await repository.replay(command)
    second = await repository.replay(command)
    assert first == second == EdgeRetailCashReplayV1.model_validate_json(json.dumps(wrapper))
    assert first is not None and first.result.operation_id == command.confirmation.operation_id
    sql, params = fake.scalar.call_args.args
    assert "public.dispatch_edge_cash_sale_v1(" in str(sql)
    assert str(command.scope.tenant_id) not in str(sql)
    assert params["operation_id"] == command.confirmation.operation_id
    assert params["activation_id"] == command.scope.activation_id
    assert params["writer_epoch"] == command.scope.writer_epoch
    assert params["cashier_user_id"] == command.scope.user_id
    assert params["request_hash"] == hashlib.sha256(params["payload"].encode()).hexdigest()
    fake.commit.assert_not_called()
    fake.rollback.assert_not_called()
    fake.flush.assert_not_called()


async def test_no_anchor_remains_absent_without_creating_or_backfilling_anything() -> None:
    command, _ = fixture()
    fake = session(None)
    assert await EdgeRetailCashReplayRepository(cast(AsyncSession, fake)).replay(command) is None
    fake.add.assert_not_called()
    fake.execute.assert_not_called()
    fake.commit.assert_not_called()


@pytest.mark.parametrize("dirty", ["new", "dirty", "deleted", "no_transaction"])
async def test_unsafe_session_is_rejected_before_sql(dirty: str) -> None:
    command, wrapper = fixture()
    fake = session(wrapper, active=dirty != "no_transaction")
    if dirty != "no_transaction":
        setattr(fake, dirty, {object()})
    with pytest.raises(OfflineAuthContractError):
        await EdgeRetailCashReplayRepository(cast(AsyncSession, fake)).replay(command)
    fake.scalar.assert_not_called()


@pytest.mark.parametrize(
    "path",
    [
        ("operation_id",),
        ("sale_id",),
        ("result", "tenant_id"),
        ("result", "branch_id"),
        ("result", "register_id"),
        ("result", "cashier_id"),
        ("result", "sale_id"),
        ("result", "operation_id"),
        ("result", "event_id"),
        ("result", "stock_command_id"),
        ("result", "payments", 0, "id"),
    ],
)
async def test_nested_identity_substitution_is_rejected(path: tuple[str | int, ...]) -> None:
    command, wrapper = fixture()
    target = wrapper
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = str(uuid4())
    fake = session(wrapper)
    with pytest.raises(ValidationError):
        await EdgeRetailCashReplayRepository(cast(AsyncSession, fake)).replay(command)
    fake.commit.assert_not_called()


@pytest.mark.parametrize("kind", ["physical_cash", "mixed_cash"])
@pytest.mark.parametrize("mutation", ["test", "electronic", "receipt", "total", "kind"])
def test_non_native_cash_or_mismatched_headers_are_refused(
    kind: EdgeRetailCashKind, mutation: str
) -> None:
    _, wrapper = fixture(kind)
    if mutation == "test":
        wrapper["result"]["is_test"] = True
    elif mutation == "electronic":
        wrapper["result"]["payments"][0]["method"] = "qr"
    elif mutation == "receipt":
        wrapper["receipt_number"] = "another-receipt"
    elif mutation == "total":
        wrapper["total_amount"] = "999.00"
    else:
        wrapper["kind"] = "mixed_cash" if kind == "physical_cash" else "physical_cash"
    with pytest.raises(ValidationError):
        EdgeRetailCashReplayV1.model_validate_json(json.dumps(wrapper))


@pytest.mark.parametrize("field", ["activation_id", "edge_node_id", "writer_epoch"])
async def test_even_internally_consistent_wrapper_must_match_requested_native_scope(
    field: str,
) -> None:
    command, wrapper = fixture()
    wrapper["scope"][field] = (
        command.scope.writer_epoch + 1 if field == "writer_epoch" else str(uuid4())
    )
    with pytest.raises(OfflineAuthContractError, match="requested command"):
        await EdgeRetailCashReplayRepository(cast(AsyncSession, session(wrapper))).replay(command)


async def test_changed_cash_tender_is_not_the_same_confirmation() -> None:
    command, wrapper = fixture()
    wrapper["confirmation"]["cash_tendered"] = "999.00"
    with pytest.raises(OfflineAuthContractError, match="requested command"):
        await EdgeRetailCashReplayRepository(cast(AsyncSession, session(wrapper))).replay(command)


@pytest.mark.parametrize("raw", [[], False, "{}", 1])
async def test_non_object_responses_are_refused(raw: object) -> None:
    command, _ = fixture()
    with pytest.raises(OfflineAuthContractError, match="JSON object"):
        await EdgeRetailCashReplayRepository(cast(AsyncSession, session(raw))).replay(command)


async def test_response_limit_counts_utf8_bytes_before_parsing() -> None:
    command, wrapper = fixture()
    wrapper["untrusted"] = "я" * (MAX_REPLAY_BYTES // 2)
    with pytest.raises(OfflineAuthContractError, match="response limit"):
        await EdgeRetailCashReplayRepository(cast(AsyncSession, session(wrapper))).replay(command)


def test_request_hash_is_for_completion_not_the_replay_transport() -> None:
    command, _ = fixture()
    wire = replay_payload(command)
    payload = json.loads(wire)
    assert payload["profile"] == "retail_cash.replay.v1"
    payload["profile"] = "retail_cash.complete.v1"
    expected = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert completed_command_request_hash(command) == hashlib.sha256(expected.encode()).hexdigest()
    assert completed_command_request_hash(command) != hashlib.sha256(wire.encode()).hexdigest()


async def test_forged_nested_command_and_unknown_extra_wrapper_fields_are_refused() -> None:
    command, wrapper = fixture()
    altered = command.model_copy(
        update={
            "confirmation": command.confirmation.model_copy(update={"cash_tendered": Decimal(0)})
        }
    )
    fake = session(wrapper)
    with pytest.raises(ValidationError):
        await EdgeRetailCashReplayRepository(cast(AsyncSession, fake)).replay(altered)
    fake.scalar.assert_not_called()
    extra = deepcopy(wrapper)
    extra["unknown"] = True
    with pytest.raises(ValidationError):
        EdgeRetailCashReplayV1.model_validate_json(json.dumps(extra))
