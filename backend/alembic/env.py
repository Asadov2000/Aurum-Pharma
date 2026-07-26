"""Alembic environment driven only by the hardened migration runner."""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import cast

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

config = context.config

database_url = cast(str | None, config.attributes.get("aurum_database_url"))
migration_role = cast(str | None, config.attributes.get("aurum_migration_role"))
if not database_url:
    raise RuntimeError("Alembic must be started through python -m app.migrate")
if migration_role not in {None, "aurum_schema_owner"}:
    raise RuntimeError("Unsupported database migration role")

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Domain models register their metadata here as they are added.
# Migrations currently use explicit SQL rather than autogeneration metadata.
target_metadata = None


def run_migrations_offline() -> None:
    raise RuntimeError("Offline SQL generation is disabled for privileged migrations")


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        if migration_role is not None:
            connection.exec_driver_sql("SET LOCAL ROLE aurum_schema_owner")
        context.run_migrations()


async def run_migrations_online() -> None:
    connectable = create_async_engine(database_url, poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
