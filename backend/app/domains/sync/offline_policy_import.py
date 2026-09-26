"""Operator-only import into the inactive policy inbox; no HTTP or runtime wiring."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from pydantic import UUID4, BaseModel, ConfigDict, Field

from app.domains.sync.offline_policy_service import OfflinePolicyInboxService
from app.domains.sync.offline_policy_signature import Ed25519OfflinePolicyDeliveryVerifier
from app.domains.sync.schemas import (
    OfflineAuthDeviceBindingV0,
    OfflinePolicyDeliveryScopeV1,
    SignedOfflinePolicyDeliveryV1,
)

_MAX_FILE_BYTES = 65_536


class _TrustedPublicKey(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    key_id: UUID4
    public_key: str = Field(pattern=r"^[0-9a-f]{64}$")


class _TrustedImportConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    expected_scope: OfflinePolicyDeliveryScopeV1
    expected_device: OfflineAuthDeviceBindingV0
    policy_keys: tuple[_TrustedPublicKey, ...] = Field(max_length=16)
    lease_keys: tuple[_TrustedPublicKey, ...] = Field(max_length=16)

    def verifier(self) -> Ed25519OfflinePolicyDeliveryVerifier:
        # Check before dict construction, so duplicate IDs cannot silently overwrite.
        for keys in (self.policy_keys, self.lease_keys):
            if len({key.key_id for key in keys}) != len(keys):
                raise ValueError("Trusted key identifiers must be unique")
        return Ed25519OfflinePolicyDeliveryVerifier(
            policy_keys={key.key_id: bytes.fromhex(key.public_key) for key in self.policy_keys},
            lease_keys={key.key_id: bytes.fromhex(key.public_key) for key in self.lease_keys},
        )


def _bounded_read(path: Path) -> bytes:
    with path.open("rb") as source:
        contents = source.read(_MAX_FILE_BYTES + 1)
    if len(contents) > _MAX_FILE_BYTES:
        raise ValueError("Policy import file exceeds the size limit")
    return contents


async def _import(delivery_path: Path, configuration_path: Path) -> bool:
    configuration = _TrustedImportConfiguration.model_validate_json(
        _bounded_read(configuration_path)
    )
    delivery = SignedOfflinePolicyDeliveryV1.model_validate_json(_bounded_read(delivery_path))
    signature = configuration.verifier()
    await signature.verify(delivery)
    if (
        delivery.claims.scope != configuration.expected_scope
        or delivery.claims.device_binding != configuration.expected_device
    ):
        raise ValueError("Policy import target mismatch")
    # Database credentials remain in normal trusted process settings, never arguments.
    from app.core.db import AppSessionLocal

    service = OfflinePolicyInboxService(
        session_factory=AppSessionLocal,
        expected_scope=configuration.expected_scope,
        expected_device=configuration.expected_device,
        signature_verifier=signature,
    )
    receipt = await service.stage(delivery)
    return receipt.duplicate


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage a signed, inactive Edge policy delivery.")
    parser.add_argument("--delivery", type=Path, required=True)
    parser.add_argument("--trusted-config", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        duplicate = asyncio.run(_import(arguments.delivery, arguments.trusted_config))
    except Exception:
        print("Policy delivery import failed; no offline authority was granted.")
        return 1
    print(
        "Policy delivery already stored; pending protected acceptance."
        if duplicate
        else "Policy delivery stored; pending protected acceptance."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
