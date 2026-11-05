"""Read an anchored native cash result; never create an anchor or execute a sale.

Only the native database dispatcher establishes the original identity binding.
These response contracts and hashes are not execution or authorization grants.
The caller retains its transaction and operation lock; runtime stays DenyAll.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, model_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.edge_retail_cash import EdgeRetailCashCommandV1, EdgeRetailCashKind
from app.domains.pos.edge_retail_cash_repository import preparation_payload
from app.domains.pos.mixed_documents import MixedCheckoutResultV4
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from app.domains.pos.unit_contracts import FrozenContract, Money
from app.domains.sync.offline_auth import OfflineAuthContractError
from app.domains.sync.schemas import OfflineAuthScopeV0

REPLAY_PROFILE = "retail_cash.replay.v1"
COMPLETE_PROFILE = "retail_cash.complete.v1"
COMPLETED_PROFILE = "retail_cash.completed.v1"
MAX_REPLAY_BYTES = 131072

type CashResult = PhysicalCheckoutResultV3 | MixedCheckoutResultV4


class EdgeRetailCashReplayV1(FrozenContract):
    """Immutable native-command wrapper, not an ordinary historical app checkout."""

    profile: Literal["retail_cash.completed.v1"]
    command_type: Literal["sale.cash.complete"]
    kind: EdgeRetailCashKind
    scope: OfflineAuthScopeV0
    confirmation: CashConfirmationRequestV2
    result: CashResult
    operation_id: UUID
    sale_id: UUID
    receipt_number: str = Field(strict=True, min_length=1, max_length=100)
    total_amount: Money
    currency: Literal["TJS"]

    @model_validator(mode="after")
    def exact_saved_command(self) -> Self:
        command = EdgeRetailCashCommandV1(
            kind=self.kind, scope=self.scope, confirmation=self.confirmation
        )
        result = self.result
        physical = type(result) is PhysicalCheckoutResultV3
        payment = result.payments[0]
        if (
            physical != (command.kind == "physical_cash")
            or self.operation_id != command.confirmation.operation_id
            or self.sale_id != command.confirmation.sale_id
            or self.total_amount != command.confirmation.expected_total_amount
            or result.tenant_id != command.scope.tenant_id
            or result.branch_id != command.scope.branch_id
            or result.register_id != command.scope.register_id
            or result.cashier_id != command.scope.user_id
            or result.operation_id != self.operation_id
            or result.event_id != self.operation_id
            or result.stock_command_id != self.operation_id
            or result.sale_id != self.sale_id
            or result.receipt_number != self.receipt_number
            or result.total_amount != self.total_amount
            or result.is_test is not False
            or len(result.payments) != 1
            or payment.id != self.operation_id
            or payment.method != "cash"
            or payment.amount != self.total_amount
        ):
            raise ValueError("Saved native cash result differs from its bound command")
        return self


def _payload(command: EdgeRetailCashCommandV1, profile: str) -> str:
    payload = json.loads(preparation_payload(command))
    payload["profile"] = profile
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def replay_payload(command: EdgeRetailCashCommandV1) -> str:
    return _payload(command, REPLAY_PROFILE)


def completed_command_request_hash(command: EdgeRetailCashCommandV1) -> str:
    """Versioned complete-envelope fingerprint for the immutable native anchor."""
    return hashlib.sha256(_payload(command, COMPLETE_PROFILE).encode("utf-8")).hexdigest()


class EdgeRetailCashReplayRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def replay(self, command: EdgeRetailCashCommandV1) -> EdgeRetailCashReplayV1 | None:
        command = EdgeRetailCashCommandV1.model_validate(command.model_dump(mode="python"))
        if not self.session.in_transaction():
            raise OfflineAuthContractError("Native cash replay requires a caller-owned transaction")
        if self.session.new or self.session.dirty or self.session.deleted:
            raise OfflineAuthContractError("Persist pending changes before native cash replay")
        payload = replay_payload(command)
        with self.session.no_autoflush:
            raw = await self.session.scalar(
                text(
                    "SELECT public.dispatch_edge_cash_sale_v1("
                    ":operation_id, :activation_id, :writer_epoch, :cashier_user_id, "
                    "CAST(:payload AS jsonb), :request_hash)"
                ),
                {
                    "operation_id": command.confirmation.operation_id,
                    "activation_id": command.scope.activation_id,
                    "writer_epoch": command.scope.writer_epoch,
                    "cashier_user_id": command.scope.user_id,
                    "payload": payload,
                    "request_hash": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                },
            )
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise OfflineAuthContractError("Native cash replay did not return a JSON object")
        encoded = json.dumps(raw, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > MAX_REPLAY_BYTES:
            raise OfflineAuthContractError("Native cash replay exceeds its response limit")
        result = EdgeRetailCashReplayV1.model_validate_json(encoded)
        if (
            result.kind != command.kind
            or result.scope != command.scope
            or result.confirmation != command.confirmation
        ):
            raise OfflineAuthContractError("Native cash replay differs from the requested command")
        return result
