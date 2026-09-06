"""The demo login exception must never escape an explicit local demo setup."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from tests.core.test_config_guard import _build


def _demo_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "ENVIRONMENT": "development",
        "AUTH_LOCAL_TESTING_MODE": True,
        "AUTH_LOCAL_DEMO_OWNER_LOGIN": True,
        "DATABASE_URL_APP": "postgresql+asyncpg://app@postgres/aurum_demo",
        "DATABASE_URL_SUPPORT": "postgresql+asyncpg://support@postgres/aurum_demo",
        "CORS_ORIGINS": ["http://localhost:5173", "http://127.0.0.1:5173"],
    }
    values.update(overrides)
    return _build(**values)


def test_demo_owner_login_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AUTH_LOCAL_DEMO_OWNER_LOGIN", raising=False)
    assert _build(ENVIRONMENT="development").AUTH_LOCAL_DEMO_OWNER_LOGIN is False


def test_explicit_local_demo_configuration_is_accepted() -> None:
    assert _demo_settings().AUTH_LOCAL_DEMO_OWNER_LOGIN is True


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_demo_owner_login_rejects_non_development(environment: str) -> None:
    with pytest.raises(ValidationError, match="Local demo owner login"):
        _demo_settings(ENVIRONMENT=environment, AUTH_LOCAL_TESTING_MODE=False)


def test_demo_owner_login_requires_explicit_local_testing_mode() -> None:
    with pytest.raises(ValidationError, match="Local demo owner login"):
        _demo_settings(AUTH_LOCAL_TESTING_MODE=False)


@pytest.mark.parametrize("field", ["DATABASE_URL_APP", "DATABASE_URL_SUPPORT"])
@pytest.mark.parametrize("database", ["aurum", "aurum_test", "aurum_demo_copy", ""])
def test_demo_owner_login_rejects_either_non_demo_database(field: str, database: str) -> None:
    with pytest.raises(ValidationError, match="Local demo owner login"):
        _demo_settings(**{field: f"postgresql+asyncpg://app@postgres/{database}"})


@pytest.mark.parametrize(
    "origins",
    [
        [],
        ["https://pharmacy.example.com"],
        ["http://localhost:5173", "https://pharmacy.example.com"],
        ["http://localhost.example.com:5173"],
        ["http://127.0.0.1.example.com:5173"],
        ["http://192.168.1.10:5173"],
    ],
)
def test_demo_owner_login_requires_only_loopback_origins(origins: list[str]) -> None:
    with pytest.raises(ValidationError, match="Local demo owner login"):
        _demo_settings(CORS_ORIGINS=origins)
