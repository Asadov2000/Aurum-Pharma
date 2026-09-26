"""Persist verified policy deliveries without granting any offline authority."""

from __future__ import annotations

import json
from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.sync.models import OfflinePolicyInbox
from app.domains.sync.offline_auth import (
    OFFLINE_AUTH_UNAVAILABLE_MESSAGE,
    OfflineAuthUnavailableError,
)
from app.domains.sync.offline_policy_repository import OfflinePolicyInboxRepository
from app.domains.sync.offline_policy_signature import Ed25519OfflinePolicyDeliveryVerifier
from app.domains.sync.schemas import (
    OfflineAuthDeviceBindingV0,
    OfflinePolicyDeliveryScopeV1,
    OfflinePolicyInboxReceiptV1,
    SignedOfflinePolicyDeliveryV1,
)


class OfflinePolicyInboxService:
    """The trusted operator supplies the expected target and independent keyrings.

    SQL sequence ordering protects concurrent deliveries, not database restores.
    Pending rows cannot serve as enrollment, policy floor or authorization state.
    """

    def __init__(
        self,
        *,
        session_factory: Callable[[], AsyncSession],
        expected_scope: OfflinePolicyDeliveryScopeV1,
        expected_device: OfflineAuthDeviceBindingV0,
        signature_verifier: Ed25519OfflinePolicyDeliveryVerifier,
    ) -> None:
        self._scope = OfflinePolicyDeliveryScopeV1.model_validate(
            expected_scope.model_dump(mode="python", warnings="error")
        )
        self._device = OfflineAuthDeviceBindingV0.model_validate(
            expected_device.model_dump(mode="python", warnings="error")
        )
        self._sessions = session_factory
        self._signature = signature_verifier

    async def _set_context(self, db: AsyncSession) -> None:
        # Trusted composition decides the tenant before any query; input does not.
        await db.execute(
            text(
                "SELECT set_config('app.tenant_id', :tenant, true), "
                "set_config('app.user_id', '', true), "
                "set_config('app.branch_id', :branch, true), "
                "set_config('app.edge_node_id', :node, true)"
            ),
            {
                "tenant": str(self._scope.tenant_id),
                "branch": str(self._scope.branch_id),
                "node": str(self._scope.edge_node_id),
            },
        )

    async def _validate(self, delivery: SignedOfflinePolicyDeliveryV1) -> None:
        await self._signature.verify(delivery)
        if delivery.claims.scope != self._scope or delivery.claims.device_binding != self._device:
            raise ValueError("Policy delivery target mismatch")

    async def _stored(self, row: OfflinePolicyInbox) -> SignedOfflinePolicyDeliveryV1:
        delivery = SignedOfflinePolicyDeliveryV1.model_validate_json(json.dumps(row.payload))
        await self._validate(delivery)
        claims = delivery.claims
        if (
            row.id != claims.delivery_id
            or row.delivery_sequence != claims.delivery_sequence
            or row.tenant_access_revision != claims.policy.version
            or row.issued_at != claims.issued_at
            or row.claims_hash != delivery.claims_hash
            or row.status != "pending_protected_acceptance"
            or any(
                getattr(row, field) != getattr(claims.scope, field)
                for field in type(claims.scope).model_fields
            )
        ):
            raise ValueError("Stored policy delivery is inconsistent")
        return delivery

    async def stage(self, delivery: SignedOfflinePolicyDeliveryV1) -> OfflinePolicyInboxReceiptV1:
        """Store a valid delivery once; only return after durable commit and close."""
        try:
            validated = SignedOfflinePolicyDeliveryV1.model_validate(
                delivery.model_dump(mode="python", warnings="error")
            )
            await self._validate(validated)
            duplicate = False
            receipt: OfflinePolicyInboxReceiptV1 | None = None
            async with self._sessions() as db, db.begin():
                await self._set_context(db)
                repo = OfflinePolicyInboxRepository(db)
                await repo.lock_target(self._scope)
                await repo.assert_enrollment(self._scope)
                latest_row = await repo.get_latest(self._scope)
                latest = None if latest_row is None else await self._stored(latest_row)
                previous = await repo.get_by_id(self._scope, validated.claims.delivery_id)
                if previous is not None:
                    if await self._stored(previous) != validated:
                        raise ValueError("Policy delivery identifier conflict")
                    duplicate = True
                else:
                    if latest is not None and (
                        validated.claims.delivery_sequence <= latest.claims.delivery_sequence
                        or validated.claims.policy.version < latest.claims.policy.version
                        or validated.claims.issued_at < latest.claims.issued_at
                    ):
                        raise ValueError("Policy delivery order conflict")
                    await repo.insert(validated)
                receipt = OfflinePolicyInboxReceiptV1(
                    delivery_id=validated.claims.delivery_id,
                    delivery_sequence=validated.claims.delivery_sequence,
                    tenant_access_revision=validated.claims.policy.version,
                    claims_hash=validated.claims_hash,
                    duplicate=duplicate,
                )
            if receipt is None:
                raise ValueError("Policy delivery was not committed")
            return receipt
        except Exception:
            raise OfflineAuthUnavailableError(OFFLINE_AUTH_UNAVAILABLE_MESSAGE) from None

    async def read_latest(self) -> SignedOfflinePolicyDeliveryV1 | None:
        """Revalidate stored signature and indexed columns; never activate a row."""
        try:
            result: SignedOfflinePolicyDeliveryV1 | None = None
            loaded = False
            async with self._sessions() as db, db.begin():
                await self._set_context(db)
                repo = OfflinePolicyInboxRepository(db)
                await repo.assert_enrollment(self._scope)
                row = await repo.get_latest(self._scope)
                if row is not None:
                    result = await self._stored(row)
                loaded = True
            if not loaded:
                raise ValueError("Policy inbox read did not finish")
            return result
        except Exception:
            raise OfflineAuthUnavailableError(OFFLINE_AUTH_UNAVAILABLE_MESSAGE) from None
