"""Run Alembic with role separation and test-only downgrade guards."""

from __future__ import annotations

import argparse
import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError

LEGACY_HEAD_REVISION = "0066"
ROLE_SEPARATION_REVISION = "0067"
SCHEMA_OWNER_ROLE = "aurum_schema_owner"
RevisionDirection = Literal["upgrade", "downgrade"]

EMPTY_DATABASE_CHECK_SQL = """
SELECT EXISTS (
    SELECT 1
    FROM pg_catalog.pg_namespace AS schemas
    WHERE schemas.nspname NOT IN ('public', 'information_schema')
      AND schemas.nspname !~ '^pg_'

    UNION ALL

    SELECT 1
    FROM pg_catalog.pg_class AS relations
    JOIN pg_catalog.pg_namespace AS schemas
      ON schemas.oid = relations.relnamespace
    WHERE schemas.nspname NOT IN ('pg_catalog', 'information_schema')
      AND schemas.nspname !~ '^pg_toast'
      AND relations.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')
      AND NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_depend AS dependencies
          WHERE dependencies.classid = 'pg_class'::REGCLASS
            AND dependencies.objid = relations.oid
            AND dependencies.deptype = 'e'
      )

    UNION ALL

    SELECT 1
    FROM pg_catalog.pg_proc AS routines
    JOIN pg_catalog.pg_namespace AS schemas
      ON schemas.oid = routines.pronamespace
    WHERE schemas.nspname NOT IN ('pg_catalog', 'information_schema')
      AND schemas.nspname !~ '^pg_'
      AND NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_depend AS dependencies
          WHERE dependencies.classid = 'pg_proc'::REGCLASS
            AND dependencies.objid = routines.oid
            AND dependencies.deptype = 'e'
      )

    UNION ALL

    SELECT 1
    FROM pg_catalog.pg_type AS types
    JOIN pg_catalog.pg_namespace AS schemas
      ON schemas.oid = types.typnamespace
    WHERE schemas.nspname NOT IN ('pg_catalog', 'information_schema')
      AND schemas.nspname !~ '^pg_'
      AND types.typtype IN ('d', 'e')
      AND NOT EXISTS (
          SELECT 1
          FROM pg_catalog.pg_depend AS dependencies
          WHERE dependencies.classid = 'pg_type'::REGCLASS
            AND dependencies.objid = types.oid
            AND dependencies.deptype = 'e'
      )

    UNION ALL

    SELECT 1
    FROM pg_catalog.pg_extension AS extensions
    WHERE extensions.extname NOT IN ('plpgsql', 'pgcrypto', 'pg_trgm', 'unaccent')
)
"""


@dataclass(frozen=True)
class MigrationURLs:
    support: str
    migrator: str

    @classmethod
    def load(cls) -> MigrationURLs:
        urls = cls(
            support=_read_secret("DATABASE_URL_SUPPORT"),
            migrator=_read_secret("DATABASE_URL_MIGRATION"),
        )
        urls.validate()
        return urls

    def validate(self) -> None:
        support = _validated_url(self.support, "aurum_support", "DATABASE_URL_SUPPORT")
        migrator = _validated_url(
            self.migrator,
            "aurum_migrator",
            "DATABASE_URL_MIGRATION",
        )
        support_endpoint = (support.host, support.port or 5432, support.database)
        migrator_endpoint = (migrator.host, migrator.port or 5432, migrator.database)
        if support_endpoint != migrator_endpoint:
            raise ValueError("Support and migration URLs must target the same database")
        if support.password == migrator.password:
            raise ValueError("Support and migration roles must use independent passwords")

    @property
    def database(self) -> str:
        database = make_url(self.migrator).database
        if database is None:
            raise ValueError("DATABASE_URL_MIGRATION must include a database name")
        return database


def _read_secret(name: str) -> str:
    environment_value = os.environ.get(name)
    secrets_dir = os.environ.get("AURUM_SECRETS_DIR")
    secret_path = Path(secrets_dir, name) if secrets_dir else None
    file_exists = secret_path is not None and secret_path.is_file()

    if environment_value is not None and file_exists:
        raise ValueError(f"{name} must come from either environment or secrets directory")
    if environment_value is not None:
        value = environment_value
    elif secret_path is not None and file_exists:
        value = secret_path.read_text(encoding="utf-8")
    else:
        raise ValueError(f"{name} is required for database migrations")

    value = value.strip()
    if not value:
        raise ValueError(f"{name} must not be empty")
    return value


def _validated_url(value: str, username: str, name: str) -> URL:
    try:
        url = make_url(value)
    except Exception as exc:
        raise ValueError(f"{name} must be a valid SQLAlchemy URL") from exc
    if url.drivername != "postgresql+asyncpg":
        raise ValueError(f"{name} must use postgresql+asyncpg")
    if url.username != username:
        raise ValueError(f"{name} must use the {username} role")
    if not url.password or not url.host or not url.database:
        raise ValueError(f"{name} must include password, host, and database")
    return url


def _backend_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _alembic_config(database_url: str, role: str | None) -> Config:
    root = _backend_root()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.attributes["aurum_database_url"] = database_url
    config.attributes["aurum_migration_role"] = role
    return config


def run_alembic(
    direction: RevisionDirection,
    database_url: str,
    target: str,
    *,
    role: str | None = None,
) -> None:
    config = _alembic_config(database_url, role)
    if direction == "upgrade":
        command.upgrade(config, target)
    else:
        command.downgrade(config, target)


async def _read_current_revision(database_url: str) -> str | None:
    engine = create_async_engine(database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            table_exists = await connection.scalar(
                text("SELECT pg_catalog.to_regclass('public.alembic_version')")
            )
            if table_exists is None:
                has_user_objects = await connection.scalar(text(EMPTY_DATABASE_CHECK_SQL))
                if has_user_objects:
                    raise RuntimeError(
                        "Alembic revision ledger is missing from a non-empty database"
                    )
                return None
            result = await connection.execute(
                text("SELECT version_num FROM public.alembic_version")
            )
            return _single_revision(list(result.scalars()))
    finally:
        await engine.dispose()


def _single_revision(rows: list[object]) -> str:
    if len(rows) != 1 or rows[0] is None:
        raise RuntimeError("Alembic revision ledger must contain exactly one row")
    revision = str(rows[0])
    _revision_number(revision)
    return revision


def _revision_number(revision: str | None) -> int:
    if revision is None:
        return 0
    if len(revision) != 4 or not revision.isdecimal():
        raise RuntimeError(f"Unsupported non-linear Alembic revision: {revision}")
    return int(revision)


def _resolve_target(target: str) -> str:
    placeholder = "postgresql+asyncpg://unused:unused@localhost/unused"
    config = _alembic_config(placeholder, None)
    scripts = ScriptDirectory.from_config(config)
    try:
        if target == "head":
            revision = scripts.get_current_head()
        else:
            selected = scripts.get_revision(target)
            if selected is None:
                raise ValueError(f"Unknown Alembic target: {target}")
            revision = selected.revision
    except CommandError as exc:
        raise ValueError(f"Unknown Alembic target: {target}") from exc
    if revision is None:
        raise ValueError(f"Unknown Alembic target: {target}")
    _revision_number(revision)
    return revision


def _current_revision(urls: MigrationURLs) -> str | None:
    revision = asyncio.run(_read_current_revision(urls.migrator))
    if revision is None:
        return None
    try:
        known_revision = _resolve_target(revision)
    except ValueError as exc:
        raise RuntimeError(f"Unknown current Alembic revision: {revision}") from exc
    if known_revision != revision:
        raise RuntimeError(f"Ambiguous current Alembic revision: {revision}")
    return revision


def upgrade(urls: MigrationURLs, target: str) -> None:
    target_revision = _resolve_target(target)
    target_number = _revision_number(target_revision)
    current_number = _revision_number(_current_revision(urls))
    if target_number < current_number:
        raise ValueError("Upgrade target is older than the current revision")

    legacy_number = _revision_number(LEGACY_HEAD_REVISION)
    separation_number = _revision_number(ROLE_SEPARATION_REVISION)

    if current_number < min(target_number, legacy_number):
        legacy_target = target_revision if target_number <= legacy_number else LEGACY_HEAD_REVISION
        run_alembic("upgrade", urls.support, legacy_target)
        current_number = _revision_number(_current_revision(urls))

    if target_number >= separation_number and current_number < separation_number:
        if current_number != legacy_number:
            raise RuntimeError("Role separation must start from revision 0066")
        run_alembic("upgrade", urls.migrator, ROLE_SEPARATION_REVISION)
        current_number = _revision_number(_current_revision(urls))

    if target_number > separation_number and current_number < target_number:
        run_alembic(
            "upgrade",
            urls.migrator,
            target_revision,
            role=SCHEMA_OWNER_ROLE,
        )


def downgrade(urls: MigrationURLs, target: str, *, explicitly_allowed: bool) -> None:
    if not explicitly_allowed:
        raise ValueError("Downgrade requires --allow-test-downgrade")
    if not urls.database.endswith("_test"):
        raise ValueError("Downgrade is allowed only on a disposable *_test database")

    target_revision = _resolve_target(target)
    target_number = _revision_number(target_revision)
    current_number = _revision_number(_current_revision(urls))
    if target_number > current_number:
        raise ValueError("Downgrade target is newer than the current revision")

    legacy_number = _revision_number(LEGACY_HEAD_REVISION)
    separation_number = _revision_number(ROLE_SEPARATION_REVISION)

    if current_number > separation_number:
        owner_target = (
            target_revision if target_number >= separation_number else ROLE_SEPARATION_REVISION
        )
        run_alembic(
            "downgrade",
            urls.migrator,
            owner_target,
            role=SCHEMA_OWNER_ROLE,
        )
        current_number = _revision_number(_current_revision(urls))

    if current_number == separation_number and target_number < separation_number:
        run_alembic("downgrade", urls.migrator, LEGACY_HEAD_REVISION)
        current_number = _revision_number(_current_revision(urls))

    if current_number <= legacy_number and target_number < current_number:
        run_alembic("downgrade", urls.support, target_revision)


def legacy_upgrade(target: str) -> None:
    target_revision = _resolve_target(target)
    if _revision_number(target_revision) > _revision_number(LEGACY_HEAD_REVISION):
        raise ValueError("Legacy support migration cannot run beyond revision 0066")
    support_url = _read_secret("DATABASE_URL_SUPPORT")
    _validated_url(support_url, "aurum_support", "DATABASE_URL_SUPPORT")
    run_alembic("upgrade", support_url, target_revision)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Aurum Pharma database migration runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    upgrade_parser = subparsers.add_parser("upgrade")
    upgrade_parser.add_argument("target", nargs="?", default="head")

    downgrade_parser = subparsers.add_parser("downgrade")
    downgrade_parser.add_argument("target")
    downgrade_parser.add_argument("--allow-test-downgrade", action="store_true")

    legacy_parser = subparsers.add_parser("legacy-upgrade")
    legacy_parser.add_argument("target")
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "legacy-upgrade":
        legacy_upgrade(args.target)
        return

    urls = MigrationURLs.load()
    if args.command == "upgrade":
        upgrade(urls, args.target)
    else:
        downgrade(
            urls,
            args.target,
            explicitly_allowed=args.allow_test_downgrade,
        )


if __name__ == "__main__":
    main()
