"""Native checks use current cart facts, not a copied saved quote or payment flag."""

from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from datetime import timedelta
from decimal import Decimal
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, ConflictError
from app.domains.catalog.unit_sales import UnitSalePreparation
from app.domains.pos.edge_retail_cash import EdgeRetailCashCommandV1, EdgeRetailCashKind
from app.domains.pos.edge_retail_cash_draft import (
    EdgeRetailCashDraftRepository,
    EdgeRetailCashDraftV1,
    EdgeRetailCurrentLegacyItemV1,
    EdgeRetailCurrentUnitItemV1,
    EdgeRetailPrescriptionReferenceV1,
    draft_payload,
)
from app.domains.pos.edge_retail_cash_inputs import (
    EdgeRetailCashInputsV1,
    EdgeRetailLegacyStockInputV1,
)
from app.domains.pos.edge_retail_cash_repricing import check_edge_retail_cash_draft
from app.domains.pos.unit_cart_quote import MixedCartQuoteV2, PhysicalUnitDraftQuoteLineV2
from app.domains.pos.unit_contracts import LegacyResultLineV2
from app.domains.sync.offline_auth import OfflineAuthContractError
from tests.domains.pos.test_edge_retail_cash_inputs import example


def draft_example(
    kind: EdgeRetailCashKind = "physical_cash",
) -> tuple[EdgeRetailCashCommandV1, EdgeRetailCashDraftV1]:
    command, raw = example(kind)
    inputs = EdgeRetailCashInputsV1.model_validate_json(json.dumps(raw))
    unit = next(
        line for line in inputs.saved_quote.lines if isinstance(line, PhysicalUnitDraftQuoteLineV2)
    )
    preparation = UnitSalePreparation(
        unit="tablet",
        units_per_pack=30,
        units_per_blister=10,
        dispense_step=1,
        unit_price=Decimal("1.25"),
    )
    catalogs = tuple(
        (
            catalog.model_copy(update={"unit_sale_preparation": preparation})
            if catalog.id == unit.catalog_id
            else catalog
        )
        for catalog in inputs.catalogs
    )
    legacy = tuple(
        EdgeRetailCurrentLegacyItemV1(
            id=line.id,
            tenant_id=command.scope.tenant_id,
            sale_id=inputs.saved_quote.sale_id,
            catalog_id=line.catalog_id,
            batch_id=line.batch_id,
            position=line.position,
            quantity=line.quantity,
            unit_price=line.original_retail_unit_price,
            discount_amount=line.discount_amount,
            total_price=line.net_amount,
            currency="TJS",
            parent_sale_item_id=None,
        )
        for line in inputs.saved_quote.lines
        if isinstance(line, LegacyResultLineV2)
    )
    batches = tuple(
        EdgeRetailLegacyStockInputV1(
            id=item.batch_id,
            tenant_id=command.scope.tenant_id,
            branch_id=command.scope.branch_id,
            catalog_id=item.catalog_id,
            expires_at=inputs.stock_date + timedelta(days=100),
            created_at=inputs.physical_stock[0].created_at,
            qty_remaining=Decimal("1.000"),
            sale_price=Decimal("99.00"),
            is_blocked=False,
        )
        for item in legacy
    )
    return command, EdgeRetailCashDraftV1(
        state="current_draft_not_executed",
        inputs=inputs.model_copy(update={"catalogs": catalogs, "legacy_stock": batches}),
        unit_items=(
            EdgeRetailCurrentUnitItemV1(
                id=unit.id,
                tenant_id=command.scope.tenant_id,
                branch_id=command.scope.branch_id,
                sale_id=inputs.saved_quote.sale_id,
                catalog_id=unit.catalog_id,
                position=1,
                sale_format=unit.sale_format,
                physical_packaging=unit.packaging,
                quantity=unit.quantity,
                discount_amount=unit.discount_amount,
            ),
        ),
        legacy_items=legacy,
        rx_refs=(),
    )


def _replace_quote(draft: EdgeRetailCashDraftV1, quote: MixedCartQuoteV2) -> EdgeRetailCashDraftV1:
    prepared = draft.inputs.preparation
    request = prepared.saved_confirmation.model_copy(
        update={
            "expected_revision_hash": quote.revision_hash,
            "expected_total_amount": quote.total_amount,
        }
    )
    inputs = draft.inputs.model_copy(
        update={
            "saved_quote": quote,
            "preparation": prepared.model_copy(update={"saved_confirmation": request}),
        }
    )
    return draft.model_copy(update={"inputs": inputs})


@pytest.mark.parametrize("kind", ["physical_cash", "mixed_cash"])
def test_current_quote_and_real_stock_are_checked_without_changing_facts(
    kind: EdgeRetailCashKind,
) -> None:
    _, draft = draft_example(kind)
    before = draft.model_dump(mode="json")
    result = check_edge_retail_cash_draft(draft)
    assert result.state == "checked_current_draft_not_executed"
    assert result.quote == draft.inputs.saved_quote
    assert result.physical_stock_plan.lines[0].base_units == 2
    assert result.stock_date == draft.inputs.stock_date
    assert len(result.legacy_allocations) == (1 if kind == "mixed_cash" else 0)
    assert draft.model_dump(mode="json") == before


@pytest.mark.parametrize(
    "change",
    [
        {"quantity": 3},
        {"discount_amount": Decimal("0.25")},
        {"id": uuid4()},
    ],
)
def test_changed_current_quantity_discount_or_identity_invalidates_confirmation(
    change: dict[str, object],
) -> None:
    _, draft = draft_example()
    current = draft.unit_items[0].model_copy(update=change)
    with pytest.raises(ConflictError, match="current native cart"):
        check_edge_retail_cash_draft(draft.model_copy(update={"unit_items": (current,)}))


@pytest.mark.parametrize("field", ["is_active", "currency", "dispensing_type"])
def test_current_unavailable_catalog_is_rejected(field: str) -> None:
    _, draft = draft_example()
    change: dict[str, object] = {
        "is_active": False,
        "currency": "USD",
        "dispensing_type": "special",
    }
    catalog = draft.inputs.catalogs[0].model_copy(update={field: change[field]})
    inputs = draft.inputs.model_copy(update={"catalogs": (catalog,)})
    with pytest.raises(BusinessRuleError, match="unavailable"):
        check_edge_retail_cash_draft(draft.model_copy(update={"inputs": inputs}))


def test_current_physical_price_change_is_repriced_then_rejected() -> None:
    _, draft = draft_example()
    catalog = draft.inputs.catalogs[0]
    assert catalog.unit_sale_preparation is not None
    changed = catalog.model_copy(
        update={
            "unit_sale_preparation": catalog.unit_sale_preparation.model_copy(
                update={"unit_price": Decimal("1.50")}
            )
        }
    )
    inputs = draft.inputs.model_copy(update={"catalogs": (changed,)})
    with pytest.raises(ConflictError, match="current native cart"):
        check_edge_retail_cash_draft(draft.model_copy(update={"inputs": inputs}))


def test_pack_price_change_does_not_reprice_a_unit_format() -> None:
    _, draft = draft_example()
    catalog = draft.inputs.catalogs[0].model_copy(update={"base_price": Decimal("99.00")})
    inputs = draft.inputs.model_copy(update={"catalogs": (catalog,)})
    assert check_edge_retail_cash_draft(draft.model_copy(update={"inputs": inputs})).quote == (
        draft.inputs.saved_quote
    )


@pytest.mark.parametrize("change", [{"physical_packaging": None}, {"catalog_id": uuid4()}])
def test_current_cart_requires_matching_catalogs_and_physical_packaging(
    change: dict[str, object],
) -> None:
    _, draft = draft_example()
    item = draft.unit_items[0].model_copy(update=change)
    with pytest.raises((BusinessRuleError, ConflictError)):
        check_edge_retail_cash_draft(draft.model_copy(update={"unit_items": (item,)}))


@pytest.mark.parametrize("kind", ["physical_cash", "mixed_cash"])
@pytest.mark.parametrize("coverage", ["missing", "global", "individual"])
def test_rx_checks_current_requirements_for_both_inventory_kinds(
    kind: EdgeRetailCashKind, coverage: str
) -> None:
    _, draft = draft_example(kind)
    catalogs = tuple(
        row.model_copy(update={"dispensing_type": "prescription"}) for row in draft.inputs.catalogs
    )
    refs = ()
    if coverage == "global":
        refs = (EdgeRetailPrescriptionReferenceV1(sale_item_id=None, unit_cart_item_id=None),)
    elif coverage == "individual":
        refs = tuple(
            EdgeRetailPrescriptionReferenceV1(sale_item_id=None, unit_cart_item_id=item.id)
            for item in draft.unit_items
        ) + tuple(
            EdgeRetailPrescriptionReferenceV1(sale_item_id=item.id, unit_cart_item_id=None)
            for item in draft.legacy_items
        )
    current = draft.model_copy(
        update={"inputs": draft.inputs.model_copy(update={"catalogs": catalogs}), "rx_refs": refs}
    )
    if coverage == "missing":
        with pytest.raises(BusinessRuleError, match="Prescription"):
            check_edge_retail_cash_draft(current)
    else:
        assert check_edge_retail_cash_draft(current).quote == draft.inputs.saved_quote


@pytest.mark.parametrize("covered", ["unit", "legacy", "foreign"])
def test_partial_or_unrelated_rx_links_cannot_cover_the_mixed_cart(covered: str) -> None:
    _, draft = draft_example("mixed_cash")
    catalogs = tuple(
        row.model_copy(update={"dispensing_type": "prescription"}) for row in draft.inputs.catalogs
    )
    reference = EdgeRetailPrescriptionReferenceV1(
        sale_item_id=draft.legacy_items[0].id if covered == "legacy" else None,
        unit_cart_item_id=draft.unit_items[0].id if covered == "unit" else uuid4(),
    )
    current = draft.model_copy(
        update={
            "inputs": draft.inputs.model_copy(update={"catalogs": catalogs}),
            "rx_refs": (reference,),
        }
    )
    with pytest.raises(BusinessRuleError, match="Prescription"):
        check_edge_retail_cash_draft(current)


@pytest.mark.parametrize("failure", ["blocked", "expired", "short"])
def test_physical_stock_is_rechecked_after_quote_confirmation(failure: str) -> None:
    _, draft = draft_example()
    lot = draft.inputs.physical_stock[0]
    change = {
        "blocked": {"is_blocked": True},
        "expired": {"expires_at": draft.inputs.stock_date},
        "short": {"closed_packs": 0},
    }[failure]
    inputs = draft.inputs.model_copy(update={"physical_stock": (lot.model_copy(update=change),)})
    with pytest.raises(BusinessRuleError):
        check_edge_retail_cash_draft(draft.model_copy(update={"inputs": inputs}))


@pytest.mark.parametrize("failure", ["blocked", "expired", "short", "stale_fefo"])
def test_legacy_stock_is_rechecked_with_exact_current_fefo(failure: str) -> None:
    _, draft = draft_example("mixed_cash")
    batch = draft.inputs.legacy_stock[0]
    changes = {
        "blocked": {"is_blocked": True},
        "expired": {"expires_at": draft.inputs.stock_date},
        "short": {"qty_remaining": Decimal("0.500")},
    }
    batches = (
        (batch.model_copy(update=changes[failure]),)
        if failure in changes
        else (
            batch,
            batch.model_copy(
                update={"id": uuid4(), "expires_at": batch.expires_at - timedelta(days=1)}
            ),
        )
    )
    inputs = draft.inputs.model_copy(
        update={"legacy_stock": tuple(sorted(batches, key=lambda b: str(b.id)))}
    )
    with pytest.raises(BusinessRuleError):
        check_edge_retail_cash_draft(draft.model_copy(update={"inputs": inputs}))


def test_legacy_rows_preserve_saved_price_and_normalized_position_semantics() -> None:
    _, draft = draft_example("mixed_cash")
    legacy = draft.legacy_items[0].model_copy(update={"position": 10000})
    unit = draft.unit_items[0].model_copy(update={"position": 99})
    checked = check_edge_retail_cash_draft(
        draft.model_copy(update={"unit_items": (unit,), "legacy_items": (legacy,)})
    )
    assert checked.quote == draft.inputs.saved_quote
    assert checked.quote.lines[0].original_retail_unit_price == Decimal("4.00")
    assert draft.inputs.legacy_stock[0].sale_price == Decimal("99.00")


def test_fractional_legacy_lines_share_one_batch_and_keep_existing_half_even_cents() -> None:
    _, draft = draft_example("mixed_cash")
    original = draft.legacy_items[0]
    first = original.model_copy(
        update={
            "quantity": Decimal("0.125"),
            "unit_price": Decimal("1.00"),
            "total_price": Decimal("0.12"),
        }
    )
    second = first.model_copy(
        update={
            "id": uuid4(),
            "position": 2,
            "quantity": Decimal("0.375"),
            "discount_amount": Decimal("0.01"),
            "total_price": Decimal("0.37"),
        }
    )
    physical = draft.inputs.saved_quote.lines[-1].model_copy(update={"position": 3})
    lines = (
        LegacyResultLineV2(
            inventory_kind="legacy_pack",
            id=first.id,
            position=1,
            catalog_id=first.catalog_id,
            batch_id=first.batch_id,
            quantity=first.quantity,
            original_retail_unit_price=first.unit_price,
            gross_amount=Decimal("0.12"),
            discount_amount=Decimal("0.00"),
            net_amount=first.total_price,
        ),
        LegacyResultLineV2(
            inventory_kind="legacy_pack",
            id=second.id,
            position=2,
            catalog_id=second.catalog_id,
            batch_id=second.batch_id,
            quantity=second.quantity,
            original_retail_unit_price=second.unit_price,
            gross_amount=Decimal("0.38"),
            discount_amount=second.discount_amount,
            net_amount=second.total_price,
        ),
        physical,
    )
    quote = draft.inputs.saved_quote.model_copy(
        update={"lines": lines, "total_amount": Decimal("2.99")}
    )
    current = _replace_quote(draft.model_copy(update={"legacy_items": (first, second)}), quote)
    result = check_edge_retail_cash_draft(current)
    assert result.quote.total_amount == Decimal("2.99")
    assert len(result.legacy_allocations) == 1
    assert result.legacy_allocations[0].quantity == Decimal("0.500")
    assert set(result.legacy_allocations[0].source_item_ids) == {first.id, second.id}


def _session(raw: object) -> MagicMock:
    session = MagicMock(spec=AsyncSession)
    session.in_transaction.return_value = True
    session.new, session.dirty, session.deleted = set(), set(), set()
    session.no_autoflush = nullcontext()
    session.scalar = AsyncMock(return_value=raw)
    return session


async def test_repository_binds_current_draft_to_native_command_and_preserves_transaction() -> None:
    command, draft = draft_example()
    session = _session(draft.model_dump(mode="json"))
    result = await EdgeRetailCashDraftRepository(cast(AsyncSession, session)).lock_draft(command)
    assert result == draft
    sql, params = session.scalar.call_args.args
    assert "public.dispatch_edge_cash_sale_v1(" in str(sql)
    assert json.loads(params["payload"])["profile"] == "retail_cash.draft.v1"
    assert params["request_hash"] == hashlib.sha256(draft_payload(command).encode()).hexdigest()
    assert params["cashier_user_id"] == command.scope.user_id
    session.flush.assert_not_called()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()


@pytest.mark.parametrize("pending", ["new", "dirty", "deleted", "transaction"])
async def test_repository_refuses_pending_changes_before_any_dispatch(pending: str) -> None:
    command, draft = draft_example()
    session = _session(draft.model_dump(mode="json"))
    if pending == "transaction":
        session.in_transaction.return_value = False
    else:
        setattr(session, pending, {object()})
    with pytest.raises(OfflineAuthContractError):
        await EdgeRetailCashDraftRepository(cast(AsyncSession, session)).lock_draft(command)
    session.scalar.assert_not_called()
    session.flush.assert_not_called()


async def test_repository_rejects_response_rebound_to_a_different_command() -> None:
    command, draft = draft_example()
    session = _session(draft.model_dump(mode="json"))
    other = command.model_copy(
        update={"scope": command.scope.model_copy(update={"user_id": uuid4()})}
    )
    with pytest.raises(OfflineAuthContractError, match="requested command"):
        await EdgeRetailCashDraftRepository(cast(AsyncSession, session)).lock_draft(other)


def test_constructed_nested_current_row_cannot_skip_strict_revalidation() -> None:
    _, draft = draft_example()
    forged = draft.unit_items[0].model_copy(update={"quantity": True})
    with pytest.raises(ValidationError):
        check_edge_retail_cash_draft(draft.model_copy(update={"unit_items": (forged,)}))
