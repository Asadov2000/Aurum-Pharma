"""Native preparation only; neither a checkout executor nor a runtime adapter.

The caller owns the transaction and its locks. Returned evidence describes a
saved confirmation, not a fresh price/stock quote, a payment or a durable sale.
Only PostgreSQL can establish the actual per-node SESSION_USER binding.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.pos.edge_retail_cash import EdgeRetailCashCommandV1, EdgeRetailCashKind
from app.domains.pos.unit_cash_confirmation import CashConfirmationRequestV2
from app.domains.pos.unit_contracts import FrozenContract
from app.domains.sync.offline_auth import OfflineAuthContractError
from app.domains.sync.schemas import OfflineAuthScopeV0

PROFILE = "retail_cash.prepare.v1"


class EdgeRetailCashPreparationV1(FrozenContract):
    schema_version: Literal[1] = 1
    state: Literal["prepared_not_executed"]
    scope: OfflineAuthScopeV0
    kind: EdgeRetailCashKind
    sale_id: UUID
    shift_id: UUID
    saved_confirmation: CashConfirmationRequestV2


def preparation_payload(command: EdgeRetailCashCommandV1) -> str:
    """Canonical ASCII-only contract; matches edge_canonical_jsonb in PostgreSQL."""
    command = EdgeRetailCashCommandV1.model_validate(command.model_dump(mode="python"))
    return json.dumps(
        {
            "profile": PROFILE,
            "kind": command.kind,
            "scope": command.scope.model_dump(mode="json"),
            "confirmation": command.confirmation.model_dump(mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


class EdgeRetailCashPreparationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def prepare(self, command: EdgeRetailCashCommandV1) -> EdgeRetailCashPreparationV1:
        command = EdgeRetailCashCommandV1.model_validate(command.model_dump(mode="python"))
        if not self.session.in_transaction():
            raise OfflineAuthContractError("Native preparation requires a caller-owned transaction")
        payload = preparation_payload(command)
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
        if not isinstance(raw, dict):
            raise OfflineAuthContractError("Native preparation did not return a JSON object")
        # asyncpg decodes JSONB into Python strings; strict UUID contracts must
        # use JSON validation rather than accepting arbitrary Python coercion.
        result = EdgeRetailCashPreparationV1.model_validate_json(json.dumps(raw, allow_nan=False))
        if (
            result.scope != command.scope
            or result.kind != command.kind
            or result.sale_id != command.confirmation.sale_id
            or result.saved_confirmation != command.confirmation
        ):
            raise OfflineAuthContractError("Native preparation differs from the requested scope")
        return result
