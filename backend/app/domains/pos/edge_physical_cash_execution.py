"""Explicit native composition only; production factories remain DenyAll.

The dedicated native session owns one transaction. PostgreSQL establishes the
actual node identity; no app role, caller GUC or second connection substitutes.
"""

import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domains.pos.edge_physical_cash_plan import (
    NativePhysicalCashPlanV1,
    build_edge_physical_cash_plan,
)
from app.domains.pos.edge_retail_cash import (
    EdgeRetailCashCommandV1,
    EdgeRetailCashExecutionStateV1,
    EdgeRetailCashExecutorV1,
    EdgeRetailCashReplayStateV1,
    EdgeRetailCashTransactionStateV1,
)
from app.domains.pos.edge_retail_cash_draft import EdgeRetailCashDraftRepository
from app.domains.pos.edge_retail_cash_replay import (
    COMPLETE_PROFILE,
    MAX_REPLAY_BYTES,
    EdgeRetailCashReplayRepository,
    EdgeRetailCashReplayV1,
)
from app.domains.pos.physical_documents import PhysicalCheckoutResultV3
from app.domains.sync.offline_auth import OfflineAuthContractError
from app.domains.sync.offline_auth_lease import VerifiedOfflineLeasePrincipalV1

MAX_COMPLETE_BYTES = 1_048_576


async def _native_transaction_id(session: AsyncSession) -> str:
    # A SQLAlchemy logical begin alone cannot exclude an AUTOCOMMIT engine.
    # Compare real server transaction IDs before any financial dispatch.
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


def physical_completion_payload(
    command: EdgeRetailCashCommandV1, plan: NativePhysicalCashPlanV1
) -> str:
    command = EdgeRetailCashCommandV1.model_validate(command.model_dump(mode="python"))
    plan = NativePhysicalCashPlanV1.model_validate(plan.model_dump(mode="python"))
    if command.kind != "physical_cash":
        raise OfflineAuthContractError("Native physical execution does not accept mixed cash")
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
        raise OfflineAuthContractError("Native physical plan differs from the confirmed command")
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
    if len(payload.encode("utf-8")) > MAX_COMPLETE_BYTES:
        raise OfflineAuthContractError("Native physical execution exceeds the complete wire bound")
    return payload


class _NativePhysicalCashExecutor:
    def __init__(
        self,
        session: AsyncSession,
        command: EdgeRetailCashCommandV1,
        state: EdgeRetailCashTransactionStateV1,
        transaction_id: str,
        *,
        plan: NativePhysicalCashPlanV1 | None = None,
        saved: EdgeRetailCashReplayV1 | None = None,
    ) -> None:
        self._session = session
        self._command = command
        self._state = state
        self._transaction_id = transaction_id
        self._plan = plan
        self._saved = saved
        self.completed = False

    @property
    def state(self) -> EdgeRetailCashTransactionStateV1:
        return self._state

    async def complete(self, *, command: EdgeRetailCashCommandV1) -> PhysicalCheckoutResultV3:
        command = EdgeRetailCashCommandV1.model_validate(command.model_dump(mode="python"))
        if self.completed or command != self._command or not self._session.in_transaction():
            raise OfflineAuthContractError("Native executor accepts its bound command once")
        if await _native_transaction_id(self._session) != self._transaction_id:
            raise OfflineAuthContractError("Native execution lost its server transaction")
        saved = self._saved
        if saved is None:
            if self._plan is None:
                raise OfflineAuthContractError("Native execution has no current stock plan")
            payload = physical_completion_payload(command, self._plan)
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
        if (
            saved.scope != command.scope
            or saved.kind != command.kind
            or saved.confirmation != command.confirmation
            or type(saved.result) is not PhysicalCheckoutResultV3
        ):
            raise OfflineAuthContractError("Native execution result differs from its command")
        # The SQL writer drains its guards while still running as its private
        # owner. Catch any accidentally deferred work before kernel reauthorization.
        await self._session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        self.completed = True
        return saved.result


class NativeEdgePhysicalCashExecutionV1:
    """Opt-in trusted adapter; callers must supply a dedicated native factory."""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

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
            command.kind != "physical_cash"
            or principal.scope != command.scope
            or principal.command != "sale.cash.complete"
        ):
            raise OfflineAuthContractError("Native physical transaction differs from its principal")
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
                executor = _NativePhysicalCashExecutor(
                    session, command, state, transaction_id, saved=saved
                )
            else:
                draft = await EdgeRetailCashDraftRepository(session).lock_draft(command)
                plan = build_edge_physical_cash_plan(draft)
                prepared = draft.inputs.preparation
                # These facts were enforced by the native SQL draft profile
                # under retained locks, rather than asserted by the caller.
                state = EdgeRetailCashExecutionStateV1(
                    scope=prepared.scope,
                    writer_mode="edge",
                    node_status="active",
                    supported_kinds=("physical_cash",),
                    sale_id=prepared.sale_id,
                    shift_id=prepared.shift_id,
                    saved_confirmation=prepared.saved_confirmation,
                )
                executor = _NativePhysicalCashExecutor(
                    session, command, state, transaction_id, plan=plan
                )
            yield executor
            if not executor.completed:
                raise OfflineAuthContractError(
                    "Native transaction ended without a validated result"
                )
