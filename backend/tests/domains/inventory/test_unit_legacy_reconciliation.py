"""Legacy comparisons preserve recorded fractions and observed physical identities."""

from datetime import UTC, datetime
from decimal import Decimal, Inexact, Rounded, localcontext
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domains.catalog.unit_sales import MAX_BASE_UNITS
from app.domains.inventory.unit_legacy_reconciliation import (
    LegacyReconciliationObservation,
    LegacyReconciliationSource,
    preview_legacy_reconciliation,
)
from app.domains.inventory.unit_stock_v2 import OpenedBlisterV2, OpenedPackV2, UnitPackagingV2

TENANT, BRANCH, CATALOG, BATCH = (UUID(int=i) for i in (101, 102, 103, 104))
IDENTITY = {"tenant_id": TENANT, "branch_id": BRANCH, "catalog_id": CATALOG, "batch_id": BATCH}


def source(quantity: object = "1") -> LegacyReconciliationSource:
    return LegacyReconciliationSource.model_validate({**IDENTITY, "legacy_quantity": quantity})


def observation(
    *, closed: int = 1, packs: tuple[OpenedPackV2, ...] = (), size: int = 30
) -> LegacyReconciliationObservation:
    return LegacyReconciliationObservation.model_validate(
        {
            **IDENTITY,
            "packaging": UnitPackagingV2(
                unit="tablet", units_per_pack=size, units_per_blister=None, dispense_step=1
            ),
            "closed_packs": closed,
            "opened_packs": packs,
        }
    )


def opened(units: int) -> OpenedPackV2:
    return OpenedPackV2(
        id=UUID(int=201), opened_at=datetime(2026, 10, 15, tzinfo=UTC), loose_units=units
    )


@pytest.mark.parametrize(
    "quantity,closed,recorded,difference,comparison",
    [
        ("2", 2, "60", "0", "matched"),
        ("2", 1, "60", "-30", "shortage"),
        ("1", 2, "30", "30", "surplus"),
        ("0", 0, "0", "0", "matched"),
        ("0.999", 1, "29.970", "0.030", "surplus"),
    ],
)
def test_exact_comparison_never_rounds_legacy_quantity(
    quantity: str, closed: int, recorded: str, difference: str, comparison: str
) -> None:
    result = preview_legacy_reconciliation(
        source(quantity), observation(closed=closed), tenant_id=TENANT, branch_id=BRANCH
    )
    assert result.recorded_base_units == Decimal(recorded)
    assert result.observed_base_units == closed * 30
    assert result.discrepancy_base_units == Decimal(difference)
    assert result.comparison == comparison
    assert result.recorded_quantity_representable is (quantity != "0.999")


def test_third_of_pack_is_not_invented_into_one_tablet() -> None:
    result = preview_legacy_reconciliation(
        source("0.333"), observation(closed=0, size=3), tenant_id=TENANT, branch_id=BRANCH
    )
    assert result.recorded_base_units == Decimal("0.999")
    assert result.fractional_base_units == Decimal("0.999")
    assert result.discrepancy_base_units == Decimal("-0.999")
    assert result.recorded_quantity_representable is False
    assert result.source.legacy_quantity == Decimal("0.333")


def test_full_opened_pack_stays_opened_and_inputs_are_immutable() -> None:
    original_source, original_observation = source(), observation(closed=0, packs=(opened(30),))
    before = original_observation.model_dump()
    result = preview_legacy_reconciliation(
        original_source, original_observation, tenant_id=TENANT, branch_id=BRANCH
    )
    assert result.comparison == "matched"
    assert result.observation.closed_packs == 0
    assert result.observation.opened_packs == (opened(30),)
    assert original_observation.model_dump() == before
    with pytest.raises(ValidationError, match="frozen"):
        result.source.legacy_quantity = Decimal("2")
    with pytest.raises(ValidationError, match="frozen"):
        result.observation.opened_packs[0].loose_units = 29


@pytest.mark.parametrize("field", ["tenant_id", "branch_id", "catalog_id", "batch_id"])
def test_mismatched_observation_identity_is_rejected(field: str) -> None:
    other = observation().model_copy(update={field: UUID(int=999)})
    with pytest.raises(ValueError):
        preview_legacy_reconciliation(source(), other, tenant_id=TENANT, branch_id=BRANCH)


@pytest.mark.parametrize("field", ["tenant_id", "branch_id"])
def test_source_must_match_trusted_scope(field: str) -> None:
    other = source().model_copy(update={field: UUID(int=999)})
    counted = observation().model_copy(update={field: UUID(int=999)})
    with pytest.raises(ValueError):
        preview_legacy_reconciliation(other, counted, tenant_id=TENANT, branch_id=BRANCH)


@pytest.mark.parametrize(
    "quantity",
    [True, False, 0.333, "NaN", "Infinity", "-Infinity", "-0.001", "0.0001", "100000000000"],
)
def test_legacy_input_requires_exact_finite_nonnegative_numeric_14_3(quantity: object) -> None:
    with pytest.raises(ValidationError):
        source(quantity)


@pytest.mark.parametrize("quantity", [1, "1.000", Decimal("1.000")])
def test_exact_decimal_input_forms_are_accepted(quantity: object) -> None:
    assert source(quantity).legacy_quantity == Decimal("1")


def test_decimal_context_cannot_round_or_trap_comparison() -> None:
    with localcontext() as context:
        context.prec = 2
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        result = preview_legacy_reconciliation(
            source("0.999"), observation(), tenant_id=TENANT, branch_id=BRANCH
        )
    assert result.recorded_base_units == Decimal("29.970")
    assert result.fractional_base_units == Decimal("0.970")
    assert result.discrepancy_base_units == Decimal("0.030")


def test_recorded_overflow_is_reported_without_losing_historical_quantity() -> None:
    result = preview_legacy_reconciliation(
        source("99999999999.999"), observation(closed=0), tenant_id=TENANT, branch_id=BRANCH
    )
    assert result.recorded_base_units == Decimal("2999999999999.970")
    assert result.recorded_quantity_representable is False
    assert result.source.legacy_quantity == Decimal("99999999999.999")
    with pytest.raises(ValidationError):
        observation(closed=MAX_BASE_UNITS)


@pytest.mark.parametrize("closed", [True, 1.0, -1])
def test_observed_pack_count_is_a_nonnegative_integer(closed: object) -> None:
    values = observation().model_dump()
    values["closed_packs"] = closed
    with pytest.raises(ValidationError):
        LegacyReconciliationObservation.model_validate(values)


@pytest.mark.parametrize(
    "intact,pieces", [((1, 1), ()), ((4,), ()), ((1,), ((1, 2),)), ((), ((1, 10),))]
)
def test_observation_reuses_blister_identity_and_capacity_rules(
    intact: tuple[int, ...], pieces: tuple[tuple[int, int], ...]
) -> None:
    values = observation(closed=0).model_dump()
    values["packaging"] = UnitPackagingV2(
        unit="tablet", units_per_pack=30, units_per_blister=10, dispense_step=1
    )
    values["opened_packs"] = (
        OpenedPackV2(
            id=UUID(int=201),
            opened_at=datetime(2026, 10, 15, tzinfo=UTC),
            intact_blisters=intact,
            opened_blisters=tuple(OpenedBlisterV2(number=n, remaining_units=q) for n, q in pieces),
        ),
    )
    with pytest.raises(ValidationError):
        LegacyReconciliationObservation.model_validate(values)


def test_duplicate_pack_identity_and_unvalidated_nested_state_are_rejected() -> None:
    with pytest.raises(ValidationError):
        observation(closed=0, packs=(opened(5), opened(5)))
    forged_pack = opened(5).model_copy(update={"loose_units": True})
    forged = observation(closed=0).model_copy(update={"opened_packs": (forged_pack,)})
    with pytest.raises(ValueError):
        preview_legacy_reconciliation(source(), forged, tenant_id=TENANT, branch_id=BRANCH)


def test_known_blisters_count_intact_and_opened_remainders_separately() -> None:
    values = observation(closed=1).model_dump()
    values["packaging"] = UnitPackagingV2(
        unit="tablet", units_per_pack=30, units_per_blister=10, dispense_step=1
    )
    pack = OpenedPackV2(
        id=UUID(int=201),
        opened_at=datetime(2026, 10, 15, tzinfo=UTC),
        intact_blisters=(1,),
        opened_blisters=(OpenedBlisterV2(number=2, remaining_units=4),),
    )
    values["opened_packs"] = (pack,)
    result = preview_legacy_reconciliation(
        source("1.5"),
        LegacyReconciliationObservation.model_validate(values),
        tenant_id=TENANT,
        branch_id=BRANCH,
    )
    assert result.observed_base_units == 44
    assert result.discrepancy_base_units == Decimal("-1")
    assert result.observation.opened_packs == (pack,)
