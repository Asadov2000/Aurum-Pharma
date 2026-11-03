"""Mixed legacy/physical cash only; no production factory enables execution."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domains.pos.edge_mixed_cash_plan import NativeMixedCashPlanV1, build_edge_mixed_cash_plan
from app.domains.pos.edge_native_cash_execution import (
    MAX_NATIVE_COMPLETE_BYTES,
    NativeEdgeRetailCashExecutionV1,
    native_completion_payload,
)
from app.domains.pos.edge_retail_cash import EdgeRetailCashCommandV1
from app.domains.pos.edge_retail_cash_draft import EdgeRetailCashDraftV1

MAX_COMPLETE_BYTES = MAX_NATIVE_COMPLETE_BYTES


def mixed_completion_payload(command: EdgeRetailCashCommandV1, plan: NativeMixedCashPlanV1) -> str:
    plan = NativeMixedCashPlanV1.model_validate(plan.model_dump(mode="python"))
    return native_completion_payload(command, plan, kind="mixed_cash", max_bytes=MAX_COMPLETE_BYTES)


def _mixed_payload(command: EdgeRetailCashCommandV1, draft: EdgeRetailCashDraftV1) -> str:
    return mixed_completion_payload(command, build_edge_mixed_cash_plan(draft))


class NativeEdgeMixedCashExecutionV1(NativeEdgeRetailCashExecutionV1):
    """A dedicated native transaction binds the existing mixed-cash result contract."""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        super().__init__(factory, kind="mixed_cash", build_payload=_mixed_payload)
