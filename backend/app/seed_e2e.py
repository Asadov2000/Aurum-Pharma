"""Bootstrap fixed accounts for E2E on a clean disposable database only.

This is deliberately separate from Alembic: production migrations must never
create users with known passwords. The command refuses to run unless the app is
in development, the explicit confirmation flag is present, and the database has
no users or tenants.

Usage in isolated CI only:

    AURUM_E2E_SEED=1 python -m app.seed_e2e
"""

from __future__ import annotations

import asyncio
import os

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import SupportSessionLocal
from app.core.security import hash_password
from app.core.time import utc_now
from app.domains.auth.models import AppUser
from app.domains.foundation.models import Tenant
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.roles.repository import RolesRepository
from app.domains.roles.service import RolesService

CONFIRMATION_ENV = "AURUM_E2E_SEED"


def require_e2e_seed_confirmation(*, environment: str, confirmation: str | None) -> None:
    if environment != "development" or confirmation != "1":
        raise SystemExit("E2E seed refused: requires ENVIRONMENT=development and AURUM_E2E_SEED=1.")


async def require_empty_database(session: AsyncSession) -> None:
    user_count = await session.scalar(select(func.count()).select_from(AppUser))
    tenant_count = await session.scalar(select(func.count()).select_from(Tenant))
    if (user_count or 0) > 0 or (tenant_count or 0) > 0:
        raise SystemExit("E2E seed refused: database already contains users or tenants.")


async def main() -> None:
    settings = get_settings()
    require_e2e_seed_confirmation(
        environment=settings.ENVIRONMENT,
        confirmation=os.getenv(CONFIRMATION_ENV),
    )

    async with SupportSessionLocal() as session:
        async with session.begin():
            await require_empty_database(session)

            foundation = FoundationService(FoundationRepository(session))
            tenant = await foundation.create_tenant(
                payload={
                    "name": "Demo Pharmacy",
                    "contact_email": "owner@aurum.tj",
                    "status": "active",
                }
            )

            now = utc_now()
            roles_repo = RolesRepository(session)
            await roles_repo.insert_user(
                email="dev@aurum.tj",
                full_name="Aurum Developer",
                password_hash=hash_password("Devdev1234"),
                is_developer=True,
                is_administrator=False,
                status="active",
                activated_at=now,
            )
            await roles_repo.insert_user(
                email="admin@aurum.tj",
                full_name="Aurum Administrator",
                password_hash=hash_password("Admin1234"),
                is_developer=False,
                is_administrator=True,
                status="active",
                activated_at=now,
            )

            owner, _role = await RolesService(roles_repo).provision_owner(
                tenant_id=tenant.id,
                email="owner@aurum.tj",
                full_name="Demo Owner",
            )
            owner.password_hash = hash_password("Owner1234")
            owner.activated_at = now
            await session.flush()

    print("E2E base seed completed on the disposable database.")


if __name__ == "__main__":
    asyncio.run(main())
