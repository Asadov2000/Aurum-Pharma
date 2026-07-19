"""Pure guard and fixture tests for the showcase seeder."""

from __future__ import annotations

import pytest

from app.seed_showcase import require_showcase_confirmation
from app.showcase.catalog import showcase_catalog_rows
from app.showcase.profiles import PROFILES, get_profile
from app.showcase.seeder import _ean13


def test_realistic_profile_is_representative_without_being_stress_volume() -> None:
    realistic = get_profile("realistic")
    stress = get_profile("stress")

    assert realistic.branches == 3
    assert realistic.history_days == 365
    assert (
        25_000
        <= (realistic.branches * realistic.history_days * realistic.daily_sales_per_branch)
        <= 35_000
    )
    assert realistic.incoming_documents == 340
    assert realistic.daily_sales_per_branch < stress.daily_sales_per_branch


def test_unknown_profile_is_rejected_with_supported_names() -> None:
    with pytest.raises(ValueError, match="lite, realistic, stress"):
        get_profile("production")


def test_catalog_is_large_unique_and_database_compatible() -> None:
    rows = showcase_catalog_rows()

    assert len(rows) >= 500
    assert len({row.stable_key for row in rows}) == len(rows)
    assert len({row.category for row in rows}) >= 20
    assert {row.storage_type for row in rows} <= {"normal", "cold", "frozen"}
    assert {row.dispensing_type for row in rows} <= {
        "otc",
        "prescription",
        "special",
    }
    assert all(row.price > 0 for row in rows)


@pytest.mark.parametrize("sequence", [1, 42, 537, 999_999])
def test_generated_internal_ean13_has_valid_checksum(sequence: int) -> None:
    barcode = _ean13(sequence)
    weighted_sum = sum(
        int(digit) * (1 if index % 2 == 0 else 3) for index, digit in enumerate(barcode)
    )

    assert len(barcode) == 13
    assert barcode.startswith("299")
    assert weighted_sum % 10 == 0


def test_showcase_guard_accepts_only_exact_isolated_target() -> None:
    require_showcase_confirmation(
        environment="development",
        confirmation="1",
        database_name="aurum_demo",
        session_user="aurum_support",
    )


@pytest.mark.parametrize(
    ("environment", "confirmation", "database_name", "session_user"),
    [
        ("production", "1", "aurum_demo", "aurum_support"),
        ("development", None, "aurum_demo", "aurum_support"),
        ("development", "1", "aurum", "aurum_support"),
        ("development", "1", "aurum_demo", "aurum_app"),
    ],
)
def test_showcase_guard_fails_closed(
    environment: str,
    confirmation: str | None,
    database_name: str,
    session_user: str,
) -> None:
    with pytest.raises(SystemExit, match="Showcase seed refused"):
        require_showcase_confirmation(
            environment=environment,
            confirmation=confirmation,
            database_name=database_name,
            session_user=session_user,
        )


def test_all_profiles_are_named_consistently() -> None:
    assert all(name == profile.name for name, profile in PROFILES.items())
