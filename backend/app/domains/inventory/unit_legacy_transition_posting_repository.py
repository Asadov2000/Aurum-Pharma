"""Database boundary for atomic closed-pack legacy conversion."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import text

from app.domains.inventory.unit_legacy_transition_repository import (
    LegacyTransitionSourceRepository,
)


class LegacyTransitionPostingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    command_id: UUID
    lot_id: UUID
    operation_id: UUID
    movement_id: UUID


class LegacyTransitionPostingRepository(LegacyTransitionSourceRepository):
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
