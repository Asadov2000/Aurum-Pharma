"""Native input contracts are evidence only; real locks are exercised in CI LOGIN tests."""

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

from app.domains.inventory.unit_allocation_v2 import UnitCartLineV2, plan_unit_cart_stock_v2
from app.domains.pos.edge_retail_cash import EdgeRetailCashCommandV1, EdgeRetailCashKind
from app.domains.pos.edge_retail_cash_inputs import (
    EdgeRetailCashInputsRepository,
    EdgeRetailCashInputsV1,
    inputs_payload,
)
from app.domains.pos.unit_cart_quote import MixedCartQuoteV2, PhysicalUnitDraftQuoteLineV2
from app.domains.sync.offline_auth import OfflineAuthContractError


def example(
    kind: EdgeRetailCashKind = "physical_cash",
) -> tuple[EdgeRetailCashCommandV1, dict[str, object]]:
    scope: dict[str, object] = {
        key: str(uuid4())
        for key in (
            "activation_id",
            "tenant_id",
            "branch_id",
            "edge_node_id",
            "register_id",
            "user_id",
        )
    }
    scope.update(writer_epoch=2, capability="cash_sale_v1")
    sale_id, shift_id, catalog_id = str(uuid4()), str(uuid4()), str(uuid4())
    packaging = {
        "version": 2,
        "unit": "tablet",
        "units_per_pack": 30,
        "units_per_blister": 10,
        "dispense_step": 1,
    }
    line: dict[str, object] = {
        "inventory_kind": "physical_unit",
        "id": str(uuid4()),
        "position": 1,
        "catalog_id": catalog_id,
        "packaging": packaging,
        "sale_format": "unit",
        "quantity": 2,
        "base_units": 2,
        "original_retail_unit_price": "1.25",
        "gross_amount": "2.50",
        "discount_amount": "0.00",
        "net_amount": "2.50",
    }
    lines = [line]
    total = "2.50"
    if kind == "mixed_cash":
        legacy = {
            "inventory_kind": "legacy_pack",
            "id": str(uuid4()),
            "position": 1,
            "catalog_id": str(uuid4()),
            "batch_id": str(uuid4()),
            "quantity": "1.000",
            "original_retail_unit_price": "4.00",
            "gross_amount": "4.00",
            "discount_amount": "0.00",
            "net_amount": "4.00",
        }
        line["position"] = 2
        lines.insert(0, legacy)
        total = "6.50"
    quote = MixedCartQuoteV2.model_validate_json(
        json.dumps(
            {
                "schema_version": 2,
                "state": "draft_quote",
                "tenant_id": scope["tenant_id"],
                "branch_id": scope["branch_id"],
                "sale_id": sale_id,
                "register_id": scope["register_id"],
                "shift_id": shift_id,
                "cashier_id": scope["user_id"],
                "lines": lines,
                "total_amount": total,
                "currency": "TJS",
            }
        )
    )
    command = EdgeRetailCashCommandV1.model_validate_json(
        json.dumps(
            {
                "schema_version": 1,
                "kind": kind,
                "scope": scope,
                "confirmation": {
                    "schema_version": 2,
                    "operation_id": str(uuid4()),
                    "sale_id": sale_id,
                    "expected_revision_hash": quote.revision_hash,
                    "expected_total_amount": total,
                    "cash_tendered": "10.00",
                    "currency": "TJS",
                },
            }
        )
    )
    catalogs = [
        {
            "id": item["catalog_id"],
            "tenant_id": scope["tenant_id"],
            "base_price": "30.00",
            "unit_sale_preparation": None,
            "dispensing_type": "otc",
            "currency": "TJS",
            "is_active": True,
            "deleted_at": None,
        }
        for item in lines
    ]
    raw: dict[str, object] = {
        "schema_version": 1,
        "state": "locked_inputs_not_executed",
        "preparation": {
            "schema_version": 1,
            "state": "prepared_not_executed",
            "kind": kind,
            "scope": scope,
            "sale_id": sale_id,
            "shift_id": shift_id,
            "saved_confirmation": command.confirmation.model_dump(mode="json"),
        },
        "stock_date": "2026-10-30",
        "saved_quote": quote.model_dump(mode="json"),
        "catalogs": sorted(catalogs, key=lambda item: item["id"]),
        "physical_stock": [
            {
                "id": str(uuid4()),
                "tenant_id": scope["tenant_id"],
                "branch_id": scope["branch_id"],
                "catalog_id": catalog_id,
                "expires_at": "2027-10-30",
                "created_at": "2026-10-01T00:00:00Z",
                "packaging": packaging,
                "closed_packs": 2,
                "opened_packs": [],
                "is_blocked": False,
            }
        ],
        "legacy_stock": [],
    }
    return command, raw


def fake_session(raw: object, *, active: bool = True) -> Mock:
    session = Mock(spec=AsyncSession)
    session.in_transaction.return_value = active
    session.scalar = AsyncMock(return_value=raw)
    return session


@pytest.mark.parametrize("kind", ["physical_cash", "mixed_cash"])
async def test_complete_scoped_inputs_use_native_dispatcher_without_commit(
    kind: EdgeRetailCashKind,
) -> None:
    command, raw = example(kind)
    fake = fake_session(raw)
    result = await EdgeRetailCashInputsRepository(cast(AsyncSession, fake)).lock_inputs(command)
    assert result.state == "locked_inputs_not_executed"
    assert result.saved_quote.revision_hash == command.confirmation.expected_revision_hash
    sql, params = fake.scalar.call_args.args
    assert "public.dispatch_edge_cash_sale_v1(" in str(sql)
    assert json.loads(params["payload"])["profile"] == "retail_cash.inputs.v1"
    assert params["request_hash"] == hashlib.sha256(params["payload"].encode()).hexdigest()
    assert params["operation_id"] == command.confirmation.operation_id
    assert params["cashier_user_id"] == command.scope.user_id
    fake.commit.assert_not_called()
    fake.rollback.assert_not_called()


def test_existing_pure_physical_planner_consumes_inputs_without_mutation() -> None:
    _, raw = example()
    inputs = EdgeRetailCashInputsV1.model_validate_json(json.dumps(raw))
    before = inputs.model_dump(mode="json")
    lines = tuple(
        UnitCartLineV2(
            id=line.id,
            catalog_id=line.catalog_id,
            packaging=line.packaging,
            sale_format=line.sale_format,
            quantity=line.quantity,
        )
        for line in inputs.saved_quote.lines
        if isinstance(line, PhysicalUnitDraftQuoteLineV2)
    )
    plan = plan_unit_cart_stock_v2(
        lines=lines,
        batches=inputs.physical_stock,
        tenant_id=inputs.preparation.scope.tenant_id,
        branch_id=inputs.preparation.scope.branch_id,
        today=inputs.stock_date,
    )
    assert sum(pick.base_units for pick in plan.lines[0].picks) == 2
    assert inputs.model_dump(mode="json") == before


async def test_native_inputs_require_an_existing_transaction() -> None:
    command, raw = example()
    fake = fake_session(raw, active=False)
    with pytest.raises(OfflineAuthContractError, match="caller-owned"):
        await EdgeRetailCashInputsRepository(cast(AsyncSession, fake)).lock_inputs(command)
    fake.scalar.assert_not_called()


@pytest.mark.parametrize("field", ["tenant_id", "branch_id", "catalog_id"])
async def test_foreign_stock_is_rejected(field: str) -> None:
    command, raw = example()
    cast(list[dict[str, object]], raw["physical_stock"])[0][field] = str(uuid4())
    with pytest.raises(ValidationError, match="another scope or catalog"):
        await EdgeRetailCashInputsRepository(cast(AsyncSession, fake_session(raw))).lock_inputs(
            command
        )


@pytest.mark.parametrize("field", ["tenant_id", "branch_id", "sale_id", "shift_id", "cashier_id"])
async def test_saved_quote_scope_cannot_be_substituted(field: str) -> None:
    command, raw = example()
    cast(dict[str, object], raw["saved_quote"])[field] = str(uuid4())
    with pytest.raises(ValidationError, match="differs from preparation"):
        await EdgeRetailCashInputsRepository(cast(AsyncSession, fake_session(raw))).lock_inputs(
            command
        )


@pytest.mark.parametrize(
    "mutation", ["duplicate", "foreign_catalog", "purchase_cost", "non_utc", "oversized"]
)
async def test_incomplete_ambiguous_or_sensitive_stock_is_rejected(mutation: str) -> None:
    command, raw = example()
    stock = cast(list[dict[str, object]], raw["physical_stock"])
    if mutation == "duplicate":
        raw["physical_stock"] = stock * 2
    elif mutation == "foreign_catalog":
        cast(list[dict[str, object]], raw["catalogs"])[0]["id"] = str(uuid4())
    elif mutation == "purchase_cost":
        stock[0]["purchase_cost_per_pack"] = "1.00"
    elif mutation == "non_utc":
        stock[0]["created_at"] = "2026-10-01T03:00:00+03:00"
    else:
        raw["padding"] = "x" * 1_048_576
    with pytest.raises((ValidationError, OfflineAuthContractError)):
        await EdgeRetailCashInputsRepository(cast(AsyncSession, fake_session(raw))).lock_inputs(
            command
        )


async def test_command_confirmation_is_rebound_and_each_retry_rechecks_database() -> None:
    command, raw = example()
    fake = fake_session(raw)
    repository = EdgeRetailCashInputsRepository(cast(AsyncSession, fake))
    await repository.lock_inputs(command)
    fake.scalar.side_effect = RuntimeError("synthetic native input denial")
    with pytest.raises(RuntimeError, match="synthetic native input denial"):
        await repository.lock_inputs(command)
    assert fake.scalar.await_count == 2
    changed = command.model_copy(
        update={"confirmation": command.confirmation.model_copy(update={"operation_id": uuid4()})}
    )
    fake.scalar.side_effect = None
    with pytest.raises(OfflineAuthContractError, match="requested command"):
        await repository.lock_inputs(changed)


def test_forged_nested_command_cannot_generate_payload() -> None:
    command, _ = example()
    forged = command.model_copy(
        update={
            "confirmation": command.confirmation.model_copy(
                update={"expected_total_amount": Decimal("NaN")}
            )
        }
    )
    with pytest.raises(ValidationError):
        inputs_payload(forged)
    assert inputs_payload(command) == inputs_payload(deepcopy(command))


@pytest.mark.parametrize("quantity", ["0.000", "12.345"])
def test_mixed_legacy_stock_keeps_exact_quantities(quantity: str) -> None:
    _, raw = example("mixed_cash")
    quote = cast(dict[str, object], raw["saved_quote"])
    line = cast(list[dict[str, object]], quote["lines"])[0]
    preparation = cast(dict[str, object], raw["preparation"])
    scope = cast(dict[str, object], preparation["scope"])
    raw["legacy_stock"] = [
        {
            "id": line["batch_id"],
            "tenant_id": scope["tenant_id"],
            "branch_id": scope["branch_id"],
            "catalog_id": line["catalog_id"],
            "expires_at": "2027-10-30",
            "created_at": "2026-10-01T00:00:00Z",
            "qty_remaining": quantity,
            "sale_price": "4.00",
            "is_blocked": False,
        }
    ]
    result = EdgeRetailCashInputsV1.model_validate_json(json.dumps(raw))
    assert result.legacy_stock[0].qty_remaining == Decimal(quantity)
    for invalid in (1.25, True, "NaN", "bad", "-0.001", "1.0001"):
        cast(list[dict[str, object]], raw["legacy_stock"])[0]["qty_remaining"] = invalid
        with pytest.raises(ValidationError):
            EdgeRetailCashInputsV1.model_validate_json(json.dumps(raw))


def test_opened_pack_identity_cannot_appear_in_two_lots() -> None:
    _, raw = example()
    lots = cast(list[dict[str, object]], raw["physical_stock"])
    lots[0]["opened_packs"] = [
        {
            "id": str(uuid4()),
            "opened_at": "2026-10-01T00:00:00Z",
            "intact_blisters": [1],
            "opened_blisters": [],
            "loose_units": 0,
        }
    ]
    assert (
        EdgeRetailCashInputsV1.model_validate_json(json.dumps(raw)).physical_stock[0].opened_packs
    )
    duplicate_lot = {**lots[0], "id": str(uuid4())}
    raw["physical_stock"] = sorted([lots[0], duplicate_lot], key=lambda row: str(row["id"]))
    with pytest.raises(ValidationError, match="unique bound"):
        EdgeRetailCashInputsV1.model_validate_json(json.dumps(raw))
