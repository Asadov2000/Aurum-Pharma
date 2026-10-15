"""Database boundary for atomic closed-pack legacy conversion."""

from uuid import UUID

from sqlalchemy import text

from app.domains.inventory.unit_legacy_transition_repository import (
    _ACTOR_SQL,
    LegacyTransitionSourceRepository,
)
from app.domains.inventory.unit_legacy_transition_schemas import (
    LegacyTransitionPostingResult,
)


class LegacyTransitionPostingRepository(LegacyTransitionSourceRepository):
    async def read(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        batch_id: UUID,
        command_id: UUID,
        actor_id: UUID,
    ) -> LegacyTransitionPostingResult | None:
        result = await self.session.execute(
            text(
                "SELECT id AS command_id,lot_id,operation_id,movement_id "
                "FROM public.unit_legacy_transition "
                "WHERE tenant_id=:tenant AND branch_id=:branch AND batch_id=:batch "
                f"AND id=:command AND created_by=:actor AND ({_ACTOR_SQL})"
            ),
            {
                "tenant": tenant_id,
                "branch": branch_id,
                "batch": batch_id,
                "command": command_id,
                "actor": actor_id,
            },
        )
        row = result.mappings().one_or_none()
        return None if row is None else LegacyTransitionPostingResult.model_validate(row)

    async def post(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID,
        batch_id: UUID,
        reconciliation_id: UUID,
        command_id: UUID,
    ) -> LegacyTransitionPostingResult:
        result = await self.session.execute(
            text("SELECT * FROM public.post_unit_legacy_transition(:t,:b,:s,:r,:c)"),
            {
                "t": tenant_id,
                "b": branch_id,
                "s": batch_id,
                "r": reconciliation_id,
                "c": command_id,
            },
        )
        row = result.mappings().one()
        return LegacyTransitionPostingResult.model_validate(row)
