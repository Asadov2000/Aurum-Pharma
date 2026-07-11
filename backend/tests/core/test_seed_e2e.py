"""Safety guard for the disposable E2E database bootstrap."""

from __future__ import annotations

import pytest

from app.seed_e2e import require_e2e_seed_confirmation


def test_e2e_seed_requires_explicit_confirmation() -> None:
    with pytest.raises(SystemExit, match="E2E seed refused"):
        require_e2e_seed_confirmation(environment="development", confirmation=None)


def test_e2e_seed_is_forbidden_outside_development() -> None:
    with pytest.raises(SystemExit, match="E2E seed refused"):
        require_e2e_seed_confirmation(environment="production", confirmation="1")


def test_e2e_seed_accepts_development_with_confirmation() -> None:
    require_e2e_seed_confirmation(environment="development", confirmation="1")
