"""Unit tests for the privileged migration runner."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app import migrate

SUPPORT_URL = "postgresql+asyncpg://aurum_support:support-test-password@postgres:5432/aurum_test"
MIGRATION_URL = (
    "postgresql+asyncpg://aurum_migrator:migrator-test-password@postgres:5432/aurum_test"
)


def test_migration_urls_require_distinct_roles_on_same_database() -> None:
    migrate.MigrationURLs(SUPPORT_URL, MIGRATION_URL).validate()

    with pytest.raises(ValueError, match="same database"):
        migrate.MigrationURLs(
            SUPPORT_URL,
            MIGRATION_URL.replace("/aurum_test", "/other_test"),
        ).validate()

    with pytest.raises(ValueError, match="independent passwords"):
        migrate.MigrationURLs(
            SUPPORT_URL,
            MIGRATION_URL.replace("migrator-test-password", "support-test-password"),
        ).validate()


def test_read_secret_rejects_environment_and_file_collision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "DATABASE_URL_MIGRATION").write_text(MIGRATION_URL, encoding="utf-8")
    monkeypatch.setenv("AURUM_SECRETS_DIR", str(tmp_path))
    monkeypatch.setenv("DATABASE_URL_MIGRATION", MIGRATION_URL)

    with pytest.raises(ValueError, match="either environment or secrets directory"):
        migrate._read_secret("DATABASE_URL_MIGRATION")


@pytest.mark.parametrize("rows", ([], [None], ["0066", "0067"]))
def test_revision_ledger_requires_exactly_one_value(rows: list[object]) -> None:
    with pytest.raises(RuntimeError, match="exactly one row"):
        migrate._single_revision(rows)


def test_current_revision_must_exist_in_migration_graph(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _unknown_revision(_database_url: str) -> str:
        return "9999"

    monkeypatch.setattr(migrate, "_read_current_revision", _unknown_revision)

    with pytest.raises(RuntimeError, match="Unknown current Alembic revision"):
        migrate._current_revision(migrate.MigrationURLs(SUPPORT_URL, MIGRATION_URL))


@pytest.mark.parametrize("has_user_objects", (False, True))
def test_missing_revision_ledger_is_allowed_only_for_an_empty_database(
    monkeypatch: pytest.MonkeyPatch,
    has_user_objects: bool,
) -> None:
    scalar_values = iter((None, has_user_objects))

    class FakeConnection:
        async def scalar(self, _statement: object) -> object:
            return next(scalar_values)

    class ConnectionContext:
        async def __aenter__(self) -> FakeConnection:
            return FakeConnection()

        async def __aexit__(self, *_args: object) -> None:
            return None

    class FakeEngine:
        def connect(self) -> ConnectionContext:
            return ConnectionContext()

        async def dispose(self) -> None:
            return None

    monkeypatch.setattr(
        migrate,
        "create_async_engine",
        lambda *_args, **_kwargs: FakeEngine(),
    )

    if has_user_objects:
        with pytest.raises(RuntimeError, match="missing from a non-empty database"):
            asyncio.run(migrate._read_current_revision(MIGRATION_URL))
    else:
        assert asyncio.run(migrate._read_current_revision(MIGRATION_URL)) is None


def test_upgrade_crosses_role_boundary_with_separate_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    urls = migrate.MigrationURLs(SUPPORT_URL, MIGRATION_URL)
    revisions = iter(("0065", "0066", "0067"))
    calls: list[tuple[str, str, str, str | None]] = []

    monkeypatch.setattr(migrate, "_current_revision", lambda _urls: next(revisions))
    monkeypatch.setattr(
        migrate,
        "run_alembic",
        lambda direction, url, target, role=None: calls.append((direction, url, target, role)),
    )

    migrate.upgrade(urls, "0067")

    assert calls == [
        ("upgrade", SUPPORT_URL, "0066", None),
        ("upgrade", MIGRATION_URL, "0067", None),
    ]


def test_downgrade_requires_explicit_disposable_database() -> None:
    test_urls = migrate.MigrationURLs(SUPPORT_URL, MIGRATION_URL)
    with pytest.raises(ValueError, match="--allow-test-downgrade"):
        migrate.downgrade(test_urls, "0066", explicitly_allowed=False)

    production_urls = migrate.MigrationURLs(
        SUPPORT_URL.replace("aurum_test", "aurum"),
        MIGRATION_URL.replace("aurum_test", "aurum"),
    )
    with pytest.raises(ValueError, match=r"\*_test"):
        migrate.downgrade(production_urls, "0066", explicitly_allowed=True)


def test_legacy_support_command_cannot_cross_security_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL_SUPPORT", SUPPORT_URL)
    with pytest.raises(ValueError, match="cannot run beyond revision 0066"):
        migrate.legacy_upgrade("0067")
