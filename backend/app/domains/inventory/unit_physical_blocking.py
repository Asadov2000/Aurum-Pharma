"""Scoped physical lot restrictions; no stock adjustment or financial completion."""

from uuid import UUID

from app.core.errors import BusinessRuleError, NotFoundError, PermissionDeniedError
from app.domains.inventory.unit_dispensing_v2_repository import UnitDispensingV2Repository
from app.domains.pos.unit_contracts import FrozenContract


class PhysicalLotBlockState(FrozenContract):
    lot_id: UUID
    branch_id: UUID
    is_blocked: bool
    blocking_revision: int


class PhysicalLotBlockingService:
    def __init__(self, repository: UnitDispensingV2Repository) -> None:
        self.repository = repository

    async def set_blocked(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        lot_id: UUID,
        actor_id: UUID,
        allowed_branch_ids: set[UUID],
        is_blocked: bool,
    ) -> PhysicalLotBlockState:
        """Serialize against dispensing on the same lot; caller owns commit.

        Actual app actor and scoped batches.update are required even on a no-op.
        Existing lot read policies apply; internal purchase amounts never leave.
        """
        if type(is_blocked) is not bool:
            raise BusinessRuleError("Physical lot blocking state must be a boolean")
        if not all(isinstance(value, UUID) for value in (tenant_id, branch_id, lot_id, actor_id)):
            raise PermissionDeniedError("Authenticated physical lot identifiers are required")
        if (
            not isinstance(allowed_branch_ids, set)
            or not all(isinstance(value, UUID) for value in allowed_branch_ids)
            or branch_id not in allowed_branch_ids
        ):
            raise PermissionDeniedError("Branch access denied")
        session = self.repository.session
        if not await self.repository.block_actor_allowed(tenant_id, branch_id, actor_id):
            raise PermissionDeniedError("Physical lot blocking permission denied")
        async with session.begin_nested():
            lot = await self.repository.lock_physical_lot(tenant_id, branch_id, lot_id)
            if lot is None:
                raise NotFoundError("Physical lot not found")
            if lot.is_blocked != is_blocked:
                await self.repository.set_physical_blocked(lot, is_blocked)
            return PhysicalLotBlockState(
                lot_id=lot.id,
                branch_id=lot.branch_id,
                is_blocked=lot.is_blocked,
                blocking_revision=lot.blocking_revision,
            )
