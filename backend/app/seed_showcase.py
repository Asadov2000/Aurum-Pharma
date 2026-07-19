"""Create a realistic dataset in the isolated ``aurum_demo`` database only."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from dataclasses import asdict

from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.core.db import SupportSessionLocal
from app.domains.auth.models import AppUser
from app.domains.foundation.models import Tenant
from app.seed_e2e import main as seed_e2e_base
from app.showcase.profiles import PROFILES, get_profile
from app.showcase.seeder import (
    is_showcase_complete,
    reconcile_showcase_email_domains,
    require_clean_showcase_base,
    seed_showcase_dataset,
)
from app.showcase.validator import validate_pending_showcase

CONFIRMATION_ENV = "AURUM_SHOWCASE_SEED"
EXPECTED_DATABASE = "aurum_demo"


def require_showcase_confirmation(
    *,
    environment: str,
    confirmation: str | None,
    database_name: str,
    session_user: str,
) -> None:
    """Fail closed unless every independent local-demo guard matches."""

    if (
        environment != "development"
        or confirmation != "1"
        or database_name != EXPECTED_DATABASE
        or session_user != "aurum_support"
    ):
        raise SystemExit(
            "Showcase seed refused: requires ENVIRONMENT=development, "
            "AURUM_SHOWCASE_SEED=1, aurum_support, and database aurum_demo."
        )


async def _inspect_database() -> tuple[int, int]:
    settings = get_settings()
    async with SupportSessionLocal() as session:
        async with session.begin():
            await session.execute(text("SELECT set_config('app.support_session', 'true', true)"))
            database_name = str(await session.scalar(text("SELECT current_database()")))
            session_user = str(await session.scalar(text("SELECT session_user")))
            require_showcase_confirmation(
                environment=settings.ENVIRONMENT,
                confirmation=os.getenv(CONFIRMATION_ENV),
                database_name=database_name,
                session_user=session_user,
            )
            user_count = int(await session.scalar(select(func.count()).select_from(AppUser)) or 0)
            tenant_count = int(await session.scalar(select(func.count()).select_from(Tenant)) or 0)
            return user_count, tenant_count


async def _ensure_e2e_base() -> None:
    user_count, tenant_count = await _inspect_database()
    if user_count == 0 and tenant_count == 0:
        previous = os.environ.get("AURUM_E2E_SEED")
        os.environ["AURUM_E2E_SEED"] = "1"
        try:
            await seed_e2e_base()
        finally:
            if previous is None:
                os.environ.pop("AURUM_E2E_SEED", None)
            else:
                os.environ["AURUM_E2E_SEED"] = previous
        return
    if user_count < 3 or tenant_count < 1:
        raise SystemExit(
            "Showcase seed refused: isolated database contains an incomplete base seed."
        )


async def run(profile_name: str) -> int:
    profile = get_profile(profile_name)
    await _ensure_e2e_base()

    async with SupportSessionLocal() as session:
        async with session.begin():
            await session.execute(text("SELECT set_config('app.support_session', 'true', true)"))
            database_name = str(await session.scalar(text("SELECT current_database()")))
            session_user = str(await session.scalar(text("SELECT session_user")))
            require_showcase_confirmation(
                environment=get_settings().ENVIRONMENT,
                confirmation=os.getenv(CONFIRMATION_ENV),
                database_name=database_name,
                session_user=session_user,
            )
            await reconcile_showcase_email_domains(session)
            if await is_showcase_complete(session):
                print(
                    json.dumps(
                        {"profile": profile.name, "status": "already_seeded"},
                        ensure_ascii=True,
                        sort_keys=True,
                    )
                )
                return 0

            await require_clean_showcase_base(session)
            summary = await seed_showcase_dataset(session, profile=profile)
            report = await validate_pending_showcase(session)
            if not report.is_valid:
                raise RuntimeError(
                    "Showcase seed failed integrity validation: "
                    f"{report.total_violations} violation(s). Transaction rolled back."
                )

    payload = {"profile": profile.name, "status": "seeded", **asdict(summary)}
    print(json.dumps(payload, default=str, ensure_ascii=True, sort_keys=True))
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        default="realistic",
        help="Dataset volume profile.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    return asyncio.run(run(args.profile))


if __name__ == "__main__":
    raise SystemExit(main())
