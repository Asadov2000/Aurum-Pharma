"""Use only the resolve-authorized DB writer; the caller owns the transaction."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.customer_returns.mixed_disposition import (
    MixedDispositionRequest,
    MixedLegacyDispositionTarget,
    MixedPhysicalDispositionTarget,
)
from app.domains.customer_returns.mixed_disposition_schemas import ResolvedMixedDisposition


class MixedDispositionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(
        self, *, tenant_id: UUID, request: MixedDispositionRequest
    ) -> ResolvedMixedDisposition:
        target = request.target
        batch_id = target.batch_id if isinstance(target, MixedLegacyDispositionTarget) else None
        lot_id = target.lot_id if isinstance(target, MixedPhysicalDispositionTarget) else None
        try:
            payload = await self.session.scalar(
                text(
                    "SELECT public.record_mixed_customer_return_disposition("
                    ":tenant,:decision,:intake,:line,:kind,:batch,:lot,"
                    ":disposition,:reason,:comment)"
                ),
                {
                    "tenant": tenant_id,
                    "decision": request.decision_operation_id,
                    "intake": target.intake_operation_id,
                    "line": target.line_id,
                    "kind": target.inventory_kind,
                    "batch": batch_id,
                    "lot": lot_id,
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
                    "Mixed quarantine resolution permission denied"
                ) from None
            if sqlstate == "P0002":
                raise NotFoundError("Confirmed mixed quarantine target not found") from None
            if sqlstate == "23505":
                raise ConflictError(
                    "Mixed quarantine decision conflicts with existing history"
                ) from None
            if sqlstate == "23514":
                raise BusinessRuleError("Mixed quarantine decision is invalid") from None
            raise
        return ResolvedMixedDisposition.model_validate(payload)
