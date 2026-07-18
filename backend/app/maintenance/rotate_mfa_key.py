"""Re-encrypt support MFA secrets from one configured key version to another."""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import SupportSessionLocal
from app.core.security import (
    derive_mfa_encryption_key,
    mfa_encryption_keyring_json,
)


async def rotate(*, from_version: int) -> int:
    settings = get_settings()
    to_version = settings.MFA_ENCRYPTION_KEY_VERSION
    if from_version == to_version:
        raise ValueError("Source and current MFA key versions must differ")
    if from_version not in settings.MFA_ENCRYPTION_PREVIOUS_KEYS:
        raise ValueError(f"MFA_ENCRYPTION_PREVIOUS_KEYS does not contain version {from_version}")

    async with SupportSessionLocal() as session:
        async with session.begin():
            result = await session.execute(
                text(
                    "SELECT public.rotate_support_mfa_encryption("
                    ":from_version, :to_version, :to_key, "
                    "CAST(:keyring AS JSONB))"
                ),
                {
                    "from_version": from_version,
                    "to_version": to_version,
                    "to_key": derive_mfa_encryption_key(),
                    "keyring": mfa_encryption_keyring_json(),
                },
            )
            return int(result.scalar_one())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Rotate encrypted support MFA secrets without printing keys."
    )
    parser.add_argument("--from-version", type=int, required=True)
    args = parser.parse_args()
    count = asyncio.run(rotate(from_version=args.from_version))
    print(f"Rotated MFA encryption for {count} support account(s).")


if __name__ == "__main__":
    main()
