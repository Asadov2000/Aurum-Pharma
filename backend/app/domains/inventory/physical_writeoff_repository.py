"""Scoped warehouse posting functions; transaction ownership stays with the caller."""

from typing import NoReturn
from uuid import UUID

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BusinessRuleError, ConflictError, NotFoundError, PermissionDeniedError
from app.domains.inventory.physical_writeoff_schemas import (
    PhysicalWriteOffRead,
    PhysicalWriteOffRequest,
    PhysicalWriteOffStatus,
)


def _raise_writeoff_error(error: DBAPIError) -> NoReturn:
    # Bound selection, comments and private source facts must not reach clients.
    sqlstate = getattr(error.orig, "sqlstate", None)
    if sqlstate == "42501":
        raise PermissionDeniedError("Physical write-off permission denied") from None
    if sqlstate == "P0002":
        raise NotFoundError("Physical write-off stock or operation not found") from None
    if sqlstate == "23505":
        raise ConflictError("Physical write-off operation conflicts with saved history") from None
    if sqlstate in {"23514", "22P02", "22003", "22023"}:
        raise BusinessRuleError("Physical write-off quantity or stock state is invalid") from None
    raise error


def _saved_result(payload: object) -> PhysicalWriteOffStatus:
    try:
        return TypeAdapter(PhysicalWriteOffStatus).validate_python(payload)
    except ValidationError:
        raise BusinessRuleError("Saved physical write-off result is invalid") from None


class PhysicalWriteOffRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def post(
        self, *, tenant_id: UUID, actor_id: UUID, request: PhysicalWriteOffRequest
    ) -> PhysicalWriteOffRead:
        try:
            payload = await self.session.scalar(
                text(
                    "SELECT public.record_physical_writeoff("
                    ":tenant,:branch,:operation,:actor,:lot,:reason,:comment,"
                    "CAST(:selection AS JSONB))"
                ),
                {
                    "tenant": tenant_id,
                    "branch": request.branch_id,
                    "operation": request.operation_id,
                    "actor": actor_id,
                    "lot": request.lot_id,
                    "reason": request.reason,
                    "comment": request.comment,
                    "selection": request.selection.model_dump_json(),
                },
            )
        except DBAPIError as error:
            _raise_writeoff_error(error)
        result = _saved_result(payload)
        if not isinstance(result, PhysicalWriteOffRead):
            raise BusinessRuleError("Saved physical write-off result is invalid")
        return result

    async def cancel(
        self, *, tenant_id: UUID, actor_id: UUID, request: PhysicalWriteOffRequest
    ) -> PhysicalWriteOffStatus:
        try:
            payload = await self.session.scalar(
                text(
                    "SELECT public.cancel_physical_writeoff("
                    ":tenant,:branch,:operation,:actor,:lot,:reason,:comment,"
                    "CAST(:selection AS JSONB))"
                ),
                {
                    "tenant": tenant_id,
                    "branch": request.branch_id,
                    "operation": request.operation_id,
                    "actor": actor_id,
                    "lot": request.lot_id,
                    "reason": request.reason,
                    "comment": request.comment,
                    "selection": request.selection.model_dump_json(),
                },
            )
        except DBAPIError as error:
            _raise_writeoff_error(error)
        return _saved_result(payload)

    async def read(
        self, *, tenant_id: UUID, branch_id: UUID, operation_id: UUID
    ) -> PhysicalWriteOffStatus:
        try:
            payload = await self.session.scalar(
                text("SELECT public.physical_writeoff_status(:tenant,:branch,:operation)"),
                {"tenant": tenant_id, "branch": branch_id, "operation": operation_id},
            )
        except DBAPIError as error:
            _raise_writeoff_error(error)
        if payload is None:
            raise NotFoundError("Physical write-off operation not found")
        return _saved_result(payload)
