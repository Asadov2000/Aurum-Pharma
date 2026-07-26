"""Run the single database-owner hardening revision without exposing its URL."""

from __future__ import annotations

import os

from sqlalchemy import URL

from app.migrate import run_alembic

TARGET_REVISION = "0032"


def main() -> None:
    owner_url = URL.create(
        "postgresql+asyncpg",
        username=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.environ["MIGRATION_DB_HOST"],
        port=5432,
        database=os.environ["MIGRATION_DB_NAME"],
    )
    run_alembic(
        "upgrade",
        owner_url.render_as_string(hide_password=False),
        TARGET_REVISION,
    )


if __name__ == "__main__":
    main()
