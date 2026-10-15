"""Internal closed-pack conversion; the caller owns the outer transaction."""

from uuid import UUID

from app.core.errors import PermissionDeniedError
from app.domains.inventory.unit_legacy_transition_posting_repository import (
    LegacyTransitionPostingRepository,
    LegacyTransitionPostingResult,
)


class LegacyTransitionPostingService:
    def __init__(self, repository: LegacyTransitionPostingRepository) -> None:
        self.repository = repository

    async def post(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        batch_id: UUID,
        reconciliation_id: UUID,
        command_id: UUID,
        actor_id: UUID,
    ) -> LegacyTransitionPostingResult:
        """Post by saved identities, never caller-supplied amounts or a prior plan.

        The database repeats source and access checks under locks, including on
        replay. SQL failures propagate to the internal caller; no HTTP is exposed.
        The savepoint rolls back both stock sides on failure without committing
        unrelated work in the caller's transaction.
        """
        if not all(
            isinstance(value, UUID)
            for value in (
                tenant_id,
                branch_id,
                batch_id,
                reconciliation_id,
                command_id,
                actor_id,
            )
        ):
            raise PermissionDeniedError("Authenticated transition identifiers are required")
        if not await self.repository.actor_allowed(
            tenant_id=tenant_id, branch_id=branch_id, actor_id=actor_id
        ):
            raise PermissionDeniedError("Legacy transition permission denied")
        async with self.repository.session.begin_nested():
            return await self.repository.post(
                tenant_id=tenant_id,
                branch_id=branch_id,
                batch_id=batch_id,
                reconciliation_id=reconciliation_id,
                command_id=command_id,
            )
