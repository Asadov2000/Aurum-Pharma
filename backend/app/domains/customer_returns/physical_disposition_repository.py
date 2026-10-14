"""Use scoped DB functions for physical decisions; the caller owns the transaction."""

from uuid import UUID

from pydantic import TypeAdapter
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.customer_returns.physical_disposition import PhysicalDispositionRequest
from app.domains.customer_returns.physical_disposition_schemas import (
    PhysicalDispositionStatus,
    ResolvedPhysicalDisposition,
)


class PhysicalDispositionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def history_timezone(self, tenant_id: UUID) -> str | None:
        value = await self.session.scalar(
            text("SELECT report_timezone FROM public.tenant_settings WHERE tenant_id=:tenant"),
            {"tenant": tenant_id},
        )
        if value is not None and not isinstance(value, str):
            raise BusinessRuleError("Saved physical quarantine history is invalid")
        return value

    async def read_status(
        self, *, tenant_id: UUID, branch_id: UUID, decision_operation_id: UUID
    ) -> PhysicalDispositionStatus:
        try:
            payload = await self.session.scalar(
                text(
                    "SELECT public.physical_customer_return_disposition_status("
                    ":tenant,:branch,:decision)"
                ),
                {"tenant": tenant_id, "branch": branch_id, "decision": decision_operation_id},
            )
        except DBAPIError as error:
            if getattr(error.orig, "sqlstate", None) == "42501":
                raise PermissionDeniedError(
                    "Physical quarantine recovery permission denied"
                ) from None
            raise
        adapter: TypeAdapter[PhysicalDispositionStatus] = TypeAdapter(PhysicalDispositionStatus)
        return adapter.validate_python(payload)

    async def list_history(
        self,
        *,
        tenant_id: UUID,
        branch_id: UUID | None,
        allowed_branch_ids: set[UUID] | None,
        page: int,
        page_size: int,
    ) -> tuple[list[ResolvedPhysicalDisposition], int]:
        if allowed_branch_ids is not None and (
            not allowed_branch_ids
            or (branch_id is not None and branch_id not in allowed_branch_ids)
        ):
            return [], 0
        source = (
            "FROM public.physical_customer_return_disposition_history(:tenant,:branch) "
            "AS history(payload)"
        )
        parameters: dict[str, object] = {"tenant": tenant_id, "branch": branch_id}
        if allowed_branch_ids is not None:
            source += " WHERE CAST(payload->>'branch_id' AS UUID)=ANY(CAST(:allowed AS UUID[]))"
            parameters["allowed"] = sorted(allowed_branch_ids, key=str)
        try:
            total = int(
                await self.session.scalar(text(f"SELECT count(*) {source}"), parameters) or 0
            )
            result = await self.session.execute(
                text(
                    f"SELECT payload {source} "
                    "ORDER BY CAST(payload->>'resolved_at' AS TIMESTAMPTZ) DESC,"
                    "CAST(payload->>'decision_operation_id' AS UUID) LIMIT :limit OFFSET :offset"
                ),
                {**parameters, "limit": page_size, "offset": (page - 1) * page_size},
            )
        except DBAPIError as error:
            sqlstate = getattr(error.orig, "sqlstate", None)
            if sqlstate == "42501":
                raise PermissionDeniedError(
                    "Physical quarantine history permission denied"
                ) from None
            if sqlstate in {"22P02", "22007", "22008"}:
                raise BusinessRuleError("Saved physical quarantine history is invalid") from None
            raise
        items = [
            ResolvedPhysicalDisposition.model_validate(payload) for payload in result.scalars()
        ]
        return items, total

    async def record(
        self, *, tenant_id: UUID, request: PhysicalDispositionRequest
    ) -> ResolvedPhysicalDisposition:
        target = request.target
        try:
            payload = await self.session.scalar(
                text(
                    "SELECT public.record_physical_customer_return_disposition("
                    ":tenant,:decision,:intake,:line,:lot,"
                    ":disposition,:reason,:comment)"
                ),
                {
                    "tenant": tenant_id,
                    "decision": request.decision_operation_id,
                    "intake": target.intake_operation_id,
                    "line": target.line_id,
                    "lot": target.lot_id,
                    "disposition": request.disposition_type,
                    "reason": request.reason_code,
                    "comment": request.comment,
                },
            )
        except DBAPIError as error:
            # Never expose SQL, bound comments or private source details to clients.
            sqlstate = getattr(error.orig, "sqlstate", None)
            if sqlstate == "42501":
                raise PermissionDeniedError(
                    "Physical quarantine resolution permission denied"
                ) from None
            if sqlstate == "P0002":
                raise NotFoundError("Confirmed physical quarantine target not found") from None
            if sqlstate == "23505":
                raise ConflictError(
                    "Physical quarantine decision conflicts with existing history"
                ) from None
            if sqlstate == "23514":
                raise BusinessRuleError("Physical quarantine decision is invalid") from None
            raise
        return ResolvedPhysicalDisposition.model_validate(payload)
