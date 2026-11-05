"""Real confirmed physical intake fixtures shared by disposition regressions."""

from typing import Literal
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.domains.customer_returns.physical_disposition import PhysicalDispositionRequest
from app.domains.customer_returns.physical_repository import PhysicalCustomerReturnsRepository
from app.domains.customer_returns.physical_schemas import PhysicalCustomerReturnRead
from app.domains.customer_returns.physical_service import PhysicalCustomerReturnsService
from app.domains.pos.physical_cash_refund_review import physical_refund_revision
from app.domains.pos.physical_return_allocation import PhysicalReturnSelection
from app.domains.pos.physical_zero_return_repository import PhysicalZeroReturnRepository
from app.domains.pos.physical_zero_return_service import PhysicalZeroReturnService
from tests.domains.customer_returns.test_physical_queue import cash_source
from tests.domains.pos.test_physical_cash_refund import refund
from tests.domains.pos.test_physical_zero_return import all_facts
from tests.domains.pos.test_physical_zero_return import source as zero_source
from tests.domains.pos.test_unit_return import runtime


async def confirmed(
    session: AsyncSession,
    *,
    intake_kind: Literal["cash_refund", "zero_return"] = "cash_refund",
    two_returns: bool = False,
) -> list[PhysicalCustomerReturnRead]:
    if intake_kind == "cash_refund":
        if two_returns:
            original, _ = await cash_source(session, quantity=2, returned=1)
            await refund(session, original.sale_id, original.lines[0].id, original.shift_id)
        else:
            await cash_source(session)
    else:
        original = await zero_source(session)
        selections = (PhysicalReturnSelection(original.lines[0].id, "unit", 1),)
        await PhysicalZeroReturnService(PhysicalZeroReturnRepository(session)).receive(
            tenant_id=original.tenant_id,
            actor_id=original.cashier_id,
            parent_sale_id=original.sale_id,
            operation_id=uuid4(),
            selections=selections,
            expected_revision_hash=physical_refund_revision(original, {}, selections),
        )
    result = await PhysicalCustomerReturnsService(
        PhysicalCustomerReturnsRepository(session)
    ).list_returns(
        tenant_id=runtime(session).stock.tenant_id,
        branch_id=None,
        search=None,
        allowed_branch_ids=None,
        page=1,
        page_size=200,
    )
    assert len(result.items) == (2 if two_returns else 1)
    return result.items


def request(row: PhysicalCustomerReturnRead, **updates: object) -> PhysicalDispositionRequest:
    return PhysicalDispositionRequest.model_validate(
        {
            "decision_operation_id": uuid4(),
            "target": {
                "inventory_kind": "physical_unit",
                "intake_operation_id": row.operation_id,
                "line_id": row.line_id,
                "lot_id": row.lot_id,
            },
            "disposition_type": "disposed",
            "reason_code": "damaged",
            "comment": None,
            **updates,
        }
    )


async def unchanged_facts(session: AsyncSession) -> dict[str, list[object]]:
    return await all_facts(session, runtime(session).stock.tenant_id)
