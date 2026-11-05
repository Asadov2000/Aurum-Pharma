"""Shared opt-in native transaction; concrete adapters bind one existing cash kind."""

import hashlib
import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domains.pos.edge_physical_cash_plan import NativePhysicalMovementPlanV1
from app.domains.pos.edge_retail_cash import (
    EdgeRetailCashCommandV1,
    EdgeRetailCashExecutionStateV1,
    EdgeRetailCashExecutorV1,
    EdgeRetailCashKind,
    EdgeRetailCashReplayStateV1,
    EdgeRetailCashTransactionStateV1,
)
from app.domains.pos.edge_retail_cash_draft import (
    EdgeRetailCashDraftRepository,
    EdgeRetailCashDraftV1,
)
from app.domains.pos.edge_retail_cash_replay import (
    COMPLETE_PROFILE,
    MAX_REPLAY_BYTES,
    EdgeRetailCashReplayRepository,
    EdgeRetailCashReplayV1,
)
from app.domains.pos.mixed_documents import MixedCheckoutResultV4
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.sync.offline_auth import OfflineAuthContractError
from app.domains.sync.offline_auth_lease import VerifiedOfflineLeasePrincipalV1

MAX_NATIVE_COMPLETE_BYTES = 1_048_576
type NativeCashResult = PhysicalCheckoutResultV3 | MixedCheckoutResultV4
type NativePayloadBuilder = Callable[[EdgeRetailCashCommandV1, EdgeRetailCashDraftV1], str]


async def _native_transaction_id(session: AsyncSession) -> str:
    # A SQLAlchemy logical begin alone cannot exclude an AUTOCOMMIT engine.
    identity = await session.scalar(
        text(
            "SELECT CASE WHEN current_setting('transaction_isolation')='read committed' "
            "THEN pg_current_xact_id()::TEXT ELSE NULL END"
        )
    )
    if not isinstance(identity, str) or not session.in_transaction():
        raise OfflineAuthContractError(
            "Native execution requires a real READ COMMITTED transaction"
        )
    return identity


def native_completion_payload(
    command: EdgeRetailCashCommandV1,
    plan: NativePhysicalMovementPlanV1,
    *,
    kind: EdgeRetailCashKind,
    max_bytes: int = MAX_NATIVE_COMPLETE_BYTES,
) -> str:
    command = EdgeRetailCashCommandV1.model_validate(command.model_dump(mode="python"))
    plan = NativePhysicalMovementPlanV1.model_validate(plan.model_dump(mode="python"))
    if command.kind != kind:
        raise OfflineAuthContractError("Native execution plan differs from its command kind")
    kinds = {line.inventory_kind for line in plan.quote.lines}
    expected = {"physical_unit"} if kind == "physical_cash" else {"legacy_pack", "physical_unit"}
    if kinds != expected:
        raise OfflineAuthContractError("Native execution plan differs from its stock kinds")
    quote, scope = plan.quote, command.scope
    if (
        quote.tenant_id != scope.tenant_id
        or quote.branch_id != scope.branch_id
        or quote.register_id != scope.register_id
        or quote.cashier_id != scope.user_id
        or quote.sale_id != command.confirmation.sale_id
        or quote.revision_hash != command.confirmation.expected_revision_hash
        or quote.total_amount != command.confirmation.expected_total_amount
    ):
        raise OfflineAuthContractError("Native cash plan differs from the confirmed command")
    payload = json.dumps(
        {
            "profile": COMPLETE_PROFILE,
            "kind": command.kind,
            "scope": command.scope.model_dump(mode="json"),
            "confirmation": command.confirmation.model_dump(mode="json"),
            "plan": plan.model_dump(mode="json"),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    if len(payload.encode("utf-8")) > min(max_bytes, MAX_NATIVE_COMPLETE_BYTES):
        raise OfflineAuthContractError("Native execution exceeds the complete wire bound")
    return payload


class _NativeCashExecutor:
    def __init__(
        self,
        session: AsyncSession,
        command: EdgeRetailCashCommandV1,
        state: EdgeRetailCashTransactionStateV1,
        transaction_id: str,
        *,
        payload: str | None = None,
        saved: EdgeRetailCashReplayV1 | None = None,
    ) -> None:
        self._session = session
        self._command = command
        self._state = state
        self._transaction_id = transaction_id
        self._payload = payload
        self._saved = saved
        self.completed = False

    @property
    def state(self) -> EdgeRetailCashTransactionStateV1:
        return self._state

    async def complete(self, *, command: EdgeRetailCashCommandV1) -> NativeCashResult:
        command = EdgeRetailCashCommandV1.model_validate(command.model_dump(mode="python"))
        if self.completed or command != self._command or not self._session.in_transaction():
            raise OfflineAuthContractError("Native executor accepts its bound command once")
        if await _native_transaction_id(self._session) != self._transaction_id:
            raise OfflineAuthContractError("Native execution lost its server transaction")
        saved = self._saved
        if saved is None:
            payload = self._payload
            if payload is None or len(payload.encode("utf-8")) > MAX_NATIVE_COMPLETE_BYTES:
                raise OfflineAuthContractError("Native execution has no bounded current stock plan")
            raw = await self._session.scalar(
                text(
                    "SELECT public.dispatch_edge_cash_sale_v1("
                    ":operation_id,:activation_id,:writer_epoch,:cashier_user_id,"
                    "CAST(:payload AS jsonb),:request_hash)"
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
                raise OfflineAuthContractError("Native execution did not return a saved wrapper")
            encoded = json.dumps(raw, ensure_ascii=False, allow_nan=False)
            if len(encoded.encode("utf-8")) > MAX_REPLAY_BYTES:
                raise OfflineAuthContractError("Native execution result exceeds its saved bound")
            saved = EdgeRetailCashReplayV1.model_validate_json(encoded)
        if await _native_transaction_id(self._session) != self._transaction_id:
            raise OfflineAuthContractError("Native result escaped its server transaction")
        expected_type = (
            PhysicalCheckoutResultV3 if command.kind == "physical_cash" else MixedCheckoutResultV4
        )
        if (
            saved.scope != command.scope
            or saved.kind != command.kind
            or saved.confirmation != command.confirmation
            or type(saved.result) is not expected_type
        ):
            raise OfflineAuthContractError("Native execution result differs from its command")
        await self._session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        self.completed = True
        return saved.result


class NativeEdgeRetailCashExecutionV1:
    """Transaction mechanism; concrete adapters fix kind and trusted plan builder.

    A dedicated native factory is mandatory. There is no runtime factory, route
    or environment switch enabling this explicit composition.
    """

    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        *,
        kind: EdgeRetailCashKind,
        build_payload: NativePayloadBuilder,
    ) -> None:
        self._factory, self._kind, self._build_payload = factory, kind, build_payload

    def transaction(
        self,
        *,
        principal: VerifiedOfflineLeasePrincipalV1,
        command: EdgeRetailCashCommandV1,
    ) -> AbstractAsyncContextManager[EdgeRetailCashExecutorV1]:
        return self._transaction(principal=principal, command=command)

    @asynccontextmanager
    async def _transaction(
        self,
        *,
        principal: VerifiedOfflineLeasePrincipalV1,
        command: EdgeRetailCashCommandV1,
    ) -> AsyncIterator[EdgeRetailCashExecutorV1]:
        command = EdgeRetailCashCommandV1.model_validate(command.model_dump(mode="python"))
        if (
            command.kind != self._kind
            or principal.scope != command.scope
            or principal.command != "sale.cash.complete"
        ):
            raise OfflineAuthContractError("Native cash transaction differs from its principal")
        async with self._factory() as session, session.begin():
            transaction_id = await _native_transaction_id(session)
            saved = await EdgeRetailCashReplayRepository(session).replay(command)
            if await _native_transaction_id(session) != transaction_id:
                raise OfflineAuthContractError("Native preparation lost its server transaction")
            if saved is not None:
                state: EdgeRetailCashTransactionStateV1 = EdgeRetailCashReplayStateV1(
                    scope=saved.scope,
                    kind=saved.kind,
                    sale_id=saved.sale_id,
                    shift_id=saved.result.shift_id,
                    saved_confirmation=saved.confirmation,
                )
                executor = _NativeCashExecutor(session, command, state, transaction_id, saved=saved)
            else:
                draft = await EdgeRetailCashDraftRepository(session).lock_draft(command)
                payload = self._build_payload(command, draft)
                prepared = draft.inputs.preparation
                state = EdgeRetailCashExecutionStateV1(
                    scope=prepared.scope,
                    writer_mode="edge",
                    node_status="active",
                    supported_kinds=(self._kind,),
                    sale_id=prepared.sale_id,
                    shift_id=prepared.shift_id,
                    saved_confirmation=prepared.saved_confirmation,
                )
                executor = _NativeCashExecutor(
                    session, command, state, transaction_id, payload=payload
                )
            yield executor
            if not executor.completed:
                raise OfflineAuthContractError(
                    "Native transaction ended without a validated result"
                )
