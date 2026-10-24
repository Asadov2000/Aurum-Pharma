"""Recovery of the saved activation is not permission to repeat an external action."""

from typing import Literal
from uuid import UUID

from pydantic import UUID4, AwareDatetime

from app.domains.pos.mixed_electronic_refund_intent_schemas import (
    SavedMixedElectronicRefundIntent,
)
from app.domains.pos.unit_contracts import FrozenContract


class ActivateMixedElectronicRefundRequest(FrozenContract):
    shift_id: UUID


class SavedMixedElectronicRefundActivation(FrozenContract):
    state: Literal["activation_saved"] = "activation_saved"
    operation_id: UUID4
    parent_sale_id: UUID
    branch_id: UUID
    actor_id: UUID
    register_id: UUID
    shift_id: UUID
    activated_at: AwareDatetime
    review: SavedMixedElectronicRefundIntent
