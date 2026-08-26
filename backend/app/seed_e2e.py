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

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import SupportSessionLocal
from app.core.security import derive_mfa_encryption_key, hash_password
from app.core.time import utc_now
from app.domains.auth.models import AppUser
from app.domains.foundation.models import Tenant
from app.domains.foundation.repository import FoundationRepository
from app.domains.foundation.service import FoundationService
from app.domains.roles.repository import RolesRepository
from app.domains.roles.service import RolesService

CONFIRMATION_ENV = "AURUM_E2E_SEED"
DEV_TOTP_SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"
ADMIN_TOTP_SECRET = "KRUGS4ZANFZSAYJAMNXW2L3ON5XCA5DF"


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
            await session.execute(text("SELECT set_config('app.support_session', 'true', true)"))
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
            developer = await roles_repo.insert_user(
                email="dev@aurum.tj",
                full_name="Aurum Developer",
                password_hash=hash_password("Devdev1234"),
                is_developer=True,
                is_administrator=False,
                status="active",
                activated_at=now,
            )
            administrator = await roles_repo.insert_user(
                email="admin@aurum.tj",
                full_name="Aurum Administrator",
                password_hash=hash_password("Admin1234"),
                is_developer=False,
                is_administrator=False,
                status="active",
                activated_at=now,
            )
            await session.execute(
                text("SELECT set_config('app.user_id', :user_id, true)"),
                {"user_id": str(developer.id)},
            )
            administrator_grant_id = await session.scalar(
                text("""
                    INSERT INTO public.platform_access_grant (
                      user_id,
                      access_kind,
                      status,
                      requested_by,
                      request_reason_code,
                      request_reason,
                      requires_approval
                    ) VALUES (
                      :administrator_id,
                      'administrator',
                      'active',
                      :developer_id,
                      'other',
                      'Disposable development seed account',
                      false
                    )
                    RETURNING id
                    """),
                {
                    "administrator_id": administrator.id,
                    "developer_id": developer.id,
                },
            )
            await session.execute(
                text("""
                    INSERT INTO public.platform_access_grant_permission (
                      grant_id,
                      permission_code,
                      created_by
                    )
                    SELECT :grant_id, permission.code, :developer_id
                    FROM public.permission AS permission
                    WHERE permission.is_active
                      AND permission.target_role_type = 'platform'
                      AND permission.scope_type = 'PLATFORM'
                      AND permission.developer_delegable
                      AND permission.administrator_grantable
                    ORDER BY permission.code
                    """),
                {
                    "grant_id": administrator_grant_id,
                    "developer_id": developer.id,
                },
            )
            await session.execute(text("SELECT set_config('app.user_id', '', true)"))
            await session.refresh(administrator)
            await session.execute(
                text("""
                    INSERT INTO public.support_mfa (
                      user_id,
                      active_secret_ciphertext,
                      active_key_version,
                      status,
                      active_generation,
                      confirmed_at
                    ) VALUES (
                      :user_id,
                      public.pgp_sym_encrypt(
                        :secret,
                        :encryption_key,
                        'cipher-algo=aes256, compress-algo=0'
                      ),
                      :key_version,
                      'active',
                      1,
                      :confirmed_at
                    )
                    """),
                [
                    {
                        "user_id": developer.id,
                        "secret": DEV_TOTP_SECRET,
                        "encryption_key": derive_mfa_encryption_key(),
                        "key_version": settings.MFA_ENCRYPTION_KEY_VERSION,
                        "confirmed_at": now,
                    },
                    {
                        "user_id": administrator.id,
                        "secret": ADMIN_TOTP_SECRET,
                        "encryption_key": derive_mfa_encryption_key(),
                        "key_version": settings.MFA_ENCRYPTION_KEY_VERSION,
                        "confirmed_at": now,
                    },
                ],
            )

            owner, _membership, _ownership, _role = await RolesService(roles_repo).provision_owner(
                tenant_id=tenant.id,
                email="owner@aurum.tj",
                full_name="Demo Owner",
            )
            owner.password_hash = hash_password("Owner1234")
            owner.activated_at = now

            cashier, _cashier_membership = await RolesService(roles_repo).create_tenant_account(
                tenant_id=tenant.id,
                email="cashier@aurum.tj",
                full_name="Demo Cashier",
                actor_id=developer.id,
            )
            cashier.password_hash = hash_password("Cashier1234")
            await session.flush()

    print("E2E base seed completed on the disposable database.")


if __name__ == "__main__":
    asyncio.run(main())
