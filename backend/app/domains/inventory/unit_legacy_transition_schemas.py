"""Public closed-pack transition identities; all stock and prices remain server-owned."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict


class LegacyTransitionPostingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    branch_id: UUID
    reconciliation_id: UUID
    command_id: UUID


class LegacyTransitionPostingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    command_id: UUID
    lot_id: UUID
    operation_id: UUID
    movement_id: UUID
