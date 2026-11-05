"""Public mixed cash contracts; prices and ownership remain server-owned."""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import UUID4, Field

from app.domains.pos.mixed_documents import MixedCheckoutResultV4
from app.domains.pos.unit_cash_intent_service import SavedCashIntentV2
from app.domains.pos.unit_contracts import FrozenContract


class MixedCashOperationNotFound(FrozenContract):
    state: Literal["not_found"] = "not_found"
    operation_id: UUID4
    sale_id: UUID


class MixedCashOperationSaved(FrozenContract):
    state: Literal["saved_draft"] = "saved_draft"
    operation_id: UUID4
    sale_id: UUID
    saved_intent: SavedCashIntentV2


class MixedCashOperationCompleted(FrozenContract):
    state: Literal["completed"] = "completed"
    operation_id: UUID4
    sale_id: UUID
    saved_intent: SavedCashIntentV2
    result: MixedCheckoutResultV4


MixedCashOperationRead = Annotated[
    MixedCashOperationNotFound | MixedCashOperationSaved | MixedCashOperationCompleted,
    Field(discriminator="state"),
]
