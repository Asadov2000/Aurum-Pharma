"""Closed-pack conversion and recovery; the caller owns the outer transaction."""

from uuid import UUID

from sqlalchemy.exc import DBAPIError

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.inventory.unit_legacy_transition_posting_repository import (
    LegacyTransitionPostingRepository,
)
from app.domains.inventory.unit_legacy_transition_schemas import (
    LegacyTransitionPostingResult,
)


class LegacyTransitionPostingService:
    def __init__(self, repository: LegacyTransitionPostingRepository) -> None:
        self.repository = repository

    async def read(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        batch_id: UUID,
        command_id: UUID,
        actor_id: UUID,
    ) -> LegacyTransitionPostingResult:
        """Recover the actual actor's committed identities without posting again."""
        if not all(
            isinstance(value, UUID)
            for value in (tenant_id, branch_id, batch_id, command_id, actor_id)
        ):
            raise PermissionDeniedError("Authenticated transition identifiers are required")
        if not await self.repository.actor_allowed(
            tenant_id=tenant_id, branch_id=branch_id, actor_id=actor_id
        ):
            raise PermissionDeniedError("Legacy transition permission denied")
        result = await self.repository.read(
            tenant_id=tenant_id,
            branch_id=branch_id,
            batch_id=batch_id,
            command_id=command_id,
            actor_id=actor_id,
        )
        if result is None:
            raise NotFoundError("Legacy transition not found")
        return result

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
        replay. Expected database rejections become safe domain errors.
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
        try:
            async with self.repository.session.begin_nested():
                return await self.repository.post(
                    tenant_id=tenant_id,
                    branch_id=branch_id,
                    batch_id=batch_id,
                    reconciliation_id=reconciliation_id,
                    command_id=command_id,
                )
        except DBAPIError as error:
            # The savepoint has rolled back before mapping known SQL states.
            # Never expose driver text, source SQL or monetary values.
            sqlstate = getattr(error.orig, "sqlstate", None)
            if sqlstate == "42501":
                raise PermissionDeniedError("Legacy transition permission denied") from None
            if sqlstate == "23505":
                raise ConflictError(
                    "Legacy transition command conflicts with existing history"
                ) from None
            if sqlstate == "23514":
                raise BusinessRuleError(
                    "Legacy stock cannot be transferred in its current state"
                ) from None
            if sqlstate == "P0002":
                raise NotFoundError("Legacy transition source not found") from None
            raise
