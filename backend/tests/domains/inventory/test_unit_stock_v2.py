"""Physical availability previews; scope checks here do not establish database RLS."""

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.domains.inventory.unit_stock import MAX_BASE_UNITS, UnitPackaging
from app.domains.inventory.unit_stock_v2 import (
    OpenedBlisterV2,
    OpenedPackV2,
    UnitPackagingV2,
    UnitStockAvailabilityV2,
    UnitStockBatchV2,
    stock_availability_v2,
    to_base_units_v2,
)

TODAY = date(2026, 10, 6)
NOW = datetime(2026, 10, 6, tzinfo=UTC)
TENANT, BRANCH, CATALOG = (UUID(int=i) for i in (101, 102, 103))


def packaging(*, blister: int | None = 10, step: int = 1) -> UnitPackagingV2:
    return UnitPackagingV2(
        unit="tablet", units_per_pack=30, units_per_blister=blister, dispense_step=step
    )


def opened(
    number: int,
    *,
    intact: tuple[int, ...] = (),
    pieces: tuple[OpenedBlisterV2, ...] = (),
    loose: int = 0,
) -> OpenedPackV2:
    return OpenedPackV2(
        id=UUID(int=number),
        opened_at=NOW,
        intact_blisters=intact,
        opened_blisters=pieces,
        loose_units=loose,
    )


def batch(
    number: int = 1,
    *,
    closed: int = 1,
    packs: tuple[OpenedPackV2, ...] = (),
    config: UnitPackagingV2 | None = None,
    expiry: int = 30,
    blocked: bool = False,
) -> UnitStockBatchV2:
    return UnitStockBatchV2(
        id=UUID(int=number),
        tenant_id=TENANT,
        branch_id=BRANCH,
        catalog_id=CATALOG,
        expires_at=TODAY + timedelta(days=expiry),
        created_at=NOW,
        packaging=config or packaging(),
        closed_packs=closed,
        opened_packs=packs,
        is_blocked=blocked,
    )


def availability(
    batches: tuple[UnitStockBatchV2, ...], *, config: UnitPackagingV2 | None = None
) -> UnitStockAvailabilityV2:
    return stock_availability_v2(
        batches=batches,
        tenant_id=TENANT,
        branch_id=BRANCH,
        catalog_id=CATALOG,
        today=TODAY,
        packaging=config or packaging(),
    )


@pytest.mark.parametrize("value", [True, False, "2", 1, 3, 2.0, None])
def test_composition_version_requires_exact_v2_integer(value: object) -> None:
    with pytest.raises(ValidationError):
        UnitPackagingV2.model_validate(packaging().model_dump() | {"version": value})


@pytest.mark.parametrize("field", ["units_per_pack", "units_per_blister", "dispense_step"])
@pytest.mark.parametrize("value", [True, "10", 2.5, 0, -1, 10_001])
def test_composition_counts_are_strict_bounded_integers(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        UnitPackagingV2.model_validate(packaging().model_dump() | {field: value})


def test_composition_requires_explicit_blister_and_divisible_factors() -> None:
    data = packaging().model_dump()
    del data["units_per_blister"]
    with pytest.raises(ValidationError):
        UnitPackagingV2.model_validate(data)
    for field, value in (("units_per_blister", 7), ("dispense_step", 7)):
        with pytest.raises(ValidationError):
            UnitPackagingV2.model_validate(packaging().model_dump() | {field: value})
    assert packaging(blister=None).units_per_blister is None


def test_formats_use_explicit_factors_and_respect_legacy_step() -> None:
    config = packaging()
    assert to_base_units_v2(config, quantity=2, sale_format="pack") == 60
    assert to_base_units_v2(config, quantity=2, sale_format="blister") == 20
    assert to_base_units_v2(config, quantity=2, sale_format="unit") == 2
    stepped = packaging(step=6)
    with pytest.raises(ValueError):
        to_base_units_v2(stepped, quantity=1, sale_format="blister")
    assert to_base_units_v2(stepped, quantity=3, sale_format="blister") == 30
    with pytest.raises(ValueError):
        to_base_units_v2(stepped, quantity=5, sale_format="unit")
    assert to_base_units_v2(stepped, quantity=6, sale_format="unit") == 6
    with pytest.raises(ValueError):
        to_base_units_v2(packaging(blister=None), quantity=1, sale_format="blister")


@pytest.mark.parametrize("quantity", [True, False, "2", 2.0, 0, -1, MAX_BASE_UNITS + 1])
def test_conversion_rejects_invalid_quantity(quantity: object) -> None:
    # Runtime API deliberately receives invalid types without relaxing its annotations.
    with pytest.raises(ValueError):
        to_base_units_v2(packaging(), **{"quantity": quantity, "sale_format": "unit"})


def test_conversion_enforces_base_limit_after_multiplication() -> None:
    config = packaging()
    for sale_format, factor in (("pack", 30), ("blister", 10)):
        largest = MAX_BASE_UNITS // factor
        assert (
            to_base_units_v2(config, quantity=largest, sale_format=sale_format) == largest * factor
        )
        with pytest.raises(ValueError):
            to_base_units_v2(config, quantity=largest + 1, sale_format=sale_format)
    assert to_base_units_v2(config, quantity=MAX_BASE_UNITS, sale_format="unit") == MAX_BASE_UNITS
    with pytest.raises(ValueError):
        to_base_units_v2(config, quantity=1, sale_format="unknown")


def test_fragments_never_become_sealed_packs_or_intact_blisters() -> None:
    packs = tuple(
        opened(number, pieces=(OpenedBlisterV2(number=1, remaining_units=5),))
        for number in range(11, 17)
    )
    result = availability((batch(closed=0, packs=packs),))
    assert result.base_units == 30
    assert result.closed_packs == 0
    assert result.intact_blisters == 0


def test_opened_outer_pack_can_retain_all_intact_blisters_without_becoming_sealed() -> None:
    result = availability((batch(closed=0, packs=(opened(11, intact=(1, 2, 3)),)),))
    assert (result.base_units, result.closed_packs, result.intact_blisters) == (30, 0, 3)


def test_availability_counts_physical_remainders_without_rounding_to_step() -> None:
    config = packaging(step=6)
    pack = opened(11, intact=(2,), pieces=(OpenedBlisterV2(number=1, remaining_units=3),))
    result = availability((batch(closed=2, packs=(pack,), config=config),), config=config)
    assert (result.base_units, result.closed_packs, result.intact_blisters) == (73, 2, 7)
    assert result.lock_batch_ids == (UUID(int=1),)
    assert result.lock_opened_pack_ids == (UUID(int=11),)


def test_unknown_blister_composition_counts_only_loose_units() -> None:
    config = packaging(blister=None)
    result = availability((batch(packs=(opened(11, loose=7),), config=config),), config=config)
    assert (result.base_units, result.closed_packs, result.intact_blisters) == (37, 1, None)


@pytest.mark.parametrize(
    "pack",
    [
        opened(11),
        opened(11, intact=(1, 1)),
        opened(11, intact=(4,)),
        opened(11, intact=(1,), pieces=(OpenedBlisterV2(number=1, remaining_units=5),)),
        opened(11, pieces=(OpenedBlisterV2(number=1, remaining_units=10),)),
        opened(11, pieces=(OpenedBlisterV2(number=1, remaining_units=3),) * 2),
        opened(11, intact=(1,), loose=1),
    ],
)
def test_batch_rejects_impossible_blister_snapshots(pack: OpenedPackV2) -> None:
    with pytest.raises(ValidationError):
        batch(packs=(pack,))


def test_batch_rejects_blisters_without_composition_and_repeated_pack_ids() -> None:
    for pack in (opened(11, intact=(1,)), opened(11, loose=31)):
        with pytest.raises(ValidationError):
            batch(config=packaging(blister=None), packs=(pack,))
    with pytest.raises(ValidationError):
        batch(packs=(opened(11, intact=(1,)), opened(11, intact=(2,))))


@pytest.mark.parametrize("number", [True, "1", 1.5, 0, -1])
def test_blister_ordinals_are_strict_positive_integers(number: object) -> None:
    with pytest.raises(ValidationError):
        OpenedPackV2.model_validate(opened(11).model_dump() | {"intact_blisters": (number,)})
    with pytest.raises(ValidationError):
        OpenedBlisterV2.model_validate({"number": number, "remaining_units": 1})


@pytest.mark.parametrize("remaining", [True, "1", 1.5, 0, -1])
def test_opened_blister_remainder_is_strict_positive_integer(remaining: object) -> None:
    with pytest.raises(ValidationError):
        OpenedBlisterV2.model_validate({"number": 1, "remaining_units": remaining})


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "bad"),
        ("opened_at", datetime(2026, 10, 6)),
        ("loose_units", True),
        ("loose_units", -1),
    ],
)
def test_opened_pack_rejects_invalid_identity_time_and_loose_count(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        OpenedPackV2.model_validate(opened(11).model_dump() | {field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("closed_packs", True),
        ("closed_packs", -1),
        ("is_blocked", 1),
        ("is_blocked", "false"),
        ("created_at", datetime(2026, 10, 6)),
    ],
)
def test_batch_rejects_invalid_counts_flags_and_naive_time(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        UnitStockBatchV2.model_validate(batch().model_dump() | {field: value})


def test_batch_and_total_availability_enforce_base_limit() -> None:
    largest = MAX_BASE_UNITS // 30
    with pytest.raises(ValidationError):
        batch(closed=largest + 1)
    with pytest.raises(ValueError):
        availability((batch(closed=largest), batch(2)))


def test_excludes_expired_blocked_and_different_historical_composition() -> None:
    snapshots = (
        batch(1),
        batch(2, expiry=0),
        batch(3, expiry=-1),
        batch(4, blocked=True),
        batch(5, config=packaging(blister=None)),
    )
    result = availability(snapshots)
    assert (result.base_units, result.closed_packs, result.intact_blisters) == (30, 1, 3)
    assert result.lock_batch_ids == tuple(UUID(int=i) for i in range(1, 6))
    assert result.lock_opened_pack_ids == ()


@pytest.mark.parametrize("scope", ["tenant_id", "branch_id", "catalog_id"])
@pytest.mark.parametrize("skip", ["blocked", "expired", "composition"])
def test_scope_is_checked_before_excluding_ineligible_batches(scope: str, skip: str) -> None:
    snapshot = batch(
        blocked=skip == "blocked",
        expiry=0 if skip == "expired" else 30,
        config=packaging(blister=None) if skip == "composition" else None,
    )
    foreign = UnitStockBatchV2.model_validate(snapshot.model_dump() | {scope: UUID(int=999)})
    with pytest.raises(ValueError):
        availability((foreign,))


def test_global_duplicates_are_checked_before_filtering() -> None:
    with pytest.raises(ValueError):
        availability((batch(1), batch(1, blocked=True)))
    first = batch(1, packs=(opened(11, intact=(1,)),))
    second = batch(2, packs=(opened(11, intact=(2,)),), expiry=0)
    with pytest.raises(ValueError):
        availability((first, second))


def test_snapshots_and_result_are_immutable_and_preview_preserves_input() -> None:
    config = packaging()
    pack = opened(11, intact=(2,), pieces=(OpenedBlisterV2(number=1, remaining_units=3),))
    snapshot = batch(packs=(pack,), config=config)
    before = snapshot.model_dump(mode="json")
    result = availability((snapshot,))
    assert snapshot.model_dump(mode="json") == before
    for model, field, value in (
        (config, "units_per_pack", 60),
        (pack, "loose_units", 5),
        (snapshot, "closed_packs", 5),
    ):
        with pytest.raises(ValidationError):
            setattr(model, field, value)
    with pytest.raises(FrozenInstanceError):
        result.base_units = 0
    assert availability((snapshot,)).base_units == result.base_units


def test_v1_composition_dump_remains_exact_without_new_blister_fields() -> None:
    legacy = UnitPackaging(unit="tablet", units_per_pack=30, dispense_step=1)
    assert (
        legacy.model_dump_json()
        == '{"version":1,"unit":"tablet","units_per_pack":30,"dispense_step":1}'
    )


def test_v2_entrypoints_reject_legacy_composition_instead_of_guessing_blister_metadata() -> None:
    legacy = UnitPackaging(unit="tablet", units_per_pack=30, dispense_step=1)
    with pytest.raises(ValueError):
        to_base_units_v2(legacy, quantity=1, sale_format="pack")
    with pytest.raises(ValueError):
        availability((), config=legacy)


@pytest.mark.parametrize("snapshots", [[batch()], (batch().model_dump(),)])
def test_availability_requires_a_tuple_of_validated_snapshots(snapshots: object) -> None:
    with pytest.raises(ValueError):
        availability(snapshots)


def test_availability_rejects_datetime_instead_of_exact_date() -> None:
    with pytest.raises(ValueError):
        stock_availability_v2(
            batches=(),
            tenant_id=TENANT,
            branch_id=BRANCH,
            catalog_id=CATALOG,
            today=NOW,
            packaging=packaging(),
        )
