"""Trusted local-login adapters; no production positive composition exists yet."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domains.sync.local_credentials import (
    LocalCredentialContextV1,
    LocalCredentialVerifierV1,
)
from app.domains.sync.offline_auth import TrustedClockReading
from app.domains.sync.offline_auth_lease_state import (
    LocalOfflineLeaseSessionV1,
    OfflineLeaseAuthorizationStateV1,
)


@dataclass(frozen=True, slots=True)
class TrustedLocalCredentialMaterialV1:
    """Context must come from independent protected enrollment, never the verifier."""

    context: LocalCredentialContextV1
    record: LocalCredentialVerifierV1 = field(repr=False)
    pepper: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class LocalAuthGuardSnapshotV1:
    tenant_id: UUID
    branch_id: UUID
    edge_node_id: UUID
    user_id: UUID
    credential_id: UUID
    credential_revision: int
    context_digest: str
    failed_attempts: int
    attempt_sequence: int
    blocked_until: datetime | None
    last_utc: datetime
    last_counter: int
    continuity_id: str


class TrustedLocalLoginStateTransactionV1(Protocol):
    """Hold authorization/recovery serialization through the database commit.

    Guard assertions must reject missing, replaced or rolled-back state. Sealing
    must durably advance a hardware-anchored high-water before SQL commit. If SQL
    commit fails afterwards, recovery must deny the resulting forward gap.
    """

    async def read_authorization(self) -> OfflineLeaseAuthorizationStateV1 | None: ...

    async def assert_current_authorization(
        self, authorization: OfflineLeaseAuthorizationStateV1
    ) -> None:
        """Require the independently sealed current tenant policy revision.

        Delivery advances the tenant/device-bound floor even when a lease is
        denied. SQL/lease input cannot establish the seal; missing state,
        restored authorization and interrupted policy acceptance must deny.
        """
        ...

    async def read_credential(self) -> TrustedLocalCredentialMaterialV1: ...

    async def read_trusted_time(self) -> TrustedClockReading: ...

    async def assert_current_guard(self, snapshot: LocalAuthGuardSnapshotV1) -> None: ...

    async def seal_next_guard(
        self,
        snapshot: LocalAuthGuardSnapshotV1,
        *,
        replacement: LocalOfflineLeaseSessionV1 | None,
    ) -> None:
        """Atomically seal guard and, on success, the new workplace session pointer.

        A replacement seals the full validated session digest by workplace,
        overriding any previous employee's pointer. None preserves the pointer.
        Subsequent trusted session reads must compare SQL against this seal;
        a guard alone cannot detect selective restoration of an old session row.
        """
        ...


class TrustedLocalLoginStateProviderV1(Protocol):
    def serialized(self) -> AbstractAsyncContextManager[TrustedLocalLoginStateTransactionV1]: ...
