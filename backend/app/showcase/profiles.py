"""Supported showcase dataset sizes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ShowcaseProfile:
    """Volume controls for one deterministic showcase dataset."""

    name: str
    branches: int
    history_days: int
    daily_sales_per_branch: int
    incoming_documents: int
    extra_tenants: int
    catalog_limit: int | None = None


PROFILES: dict[str, ShowcaseProfile] = {
    "lite": ShowcaseProfile(
        name="lite",
        branches=1,
        history_days=120,
        daily_sales_per_branch=12,
        incoming_documents=80,
        extra_tenants=3,
        catalog_limit=240,
    ),
    "realistic": ShowcaseProfile(
        name="realistic",
        branches=3,
        history_days=365,
        daily_sales_per_branch=28,
        incoming_documents=340,
        extra_tenants=8,
    ),
    "stress": ShowcaseProfile(
        name="stress",
        branches=6,
        history_days=730,
        daily_sales_per_branch=55,
        incoming_documents=1_200,
        extra_tenants=20,
    ),
}


def get_profile(name: str) -> ShowcaseProfile:
    """Return a profile or raise a CLI-friendly error."""

    try:
        return PROFILES[name]
    except KeyError as exc:
        supported = ", ".join(sorted(PROFILES))
        raise ValueError(f"Unknown showcase profile {name!r}; choose one of: {supported}") from exc
