"""Physical-only opt-in adapter; the shared transaction retains native authority."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domains.pos.edge_native_cash_execution import (
    MAX_NATIVE_COMPLETE_BYTES,
    NativeEdgeRetailCashExecutionV1,
    native_completion_payload,
)
from app.domains.pos.edge_physical_cash_plan import (
    NativePhysicalCashPlanV1,
    build_edge_physical_cash_plan,
)
from app.domains.pos.edge_retail_cash import EdgeRetailCashCommandV1
from app.domains.pos.edge_retail_cash_draft import EdgeRetailCashDraftV1

MAX_COMPLETE_BYTES = MAX_NATIVE_COMPLETE_BYTES


def physical_completion_payload(
    command: EdgeRetailCashCommandV1, plan: NativePhysicalCashPlanV1
) -> str:
    plan = NativePhysicalCashPlanV1.model_validate(plan.model_dump(mode="python"))
    return native_completion_payload(
        command, plan, kind="physical_cash", max_bytes=MAX_COMPLETE_BYTES
    )


def _physical_payload(command: EdgeRetailCashCommandV1, draft: EdgeRetailCashDraftV1) -> str:
    return physical_completion_payload(command, build_edge_physical_cash_plan(draft))


class NativeEdgePhysicalCashExecutionV1(NativeEdgeRetailCashExecutionV1):
    """Preserve the physical-only public contract and dedicated native factory."""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        super().__init__(factory, kind="physical_cash", build_payload=_physical_payload)
