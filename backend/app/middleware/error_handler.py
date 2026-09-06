"""Translate domain exceptions and unhandled errors into a uniform JSON envelope."""

from __future__ import annotations

from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.db import AppSessionLocal
from app.core.errors import AurumError, AuthorizationPolicyDeniedError
from app.core.logging import get_logger
from app.domains.audit.repository import AuditRepository
from app.domains.audit.service import AuditService

logger = get_logger("error_handler")


def _request_state_uuid(request: Request, name: str) -> UUID | None:
    raw = getattr(request.state, name, None)
    if isinstance(raw, UUID):
        return raw
    if not isinstance(raw, str):
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


def _safe_permission_codes(exc: AuthorizationPolicyDeniedError) -> list[str]:
    raw = exc.details.get("permissions")
    if not isinstance(raw, list):
        return []
    return sorted({value for value in raw if isinstance(value, str) and 0 < len(value) <= 200})


async def record_authorization_denial(
    request: Request,
    exc: AuthorizationPolicyDeniedError,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
) -> None:
    """Persist a rejected policy change outside the rolled-back request transaction."""

    tenant_id = _request_state_uuid(request, "tenant_id")
    user_id = _request_state_uuid(request, "user_id")
    if tenant_id is None or user_id is None:
        return

    route = request.scope.get("route")
    route_path = getattr(route, "path", None)
    reason = exc.details.get("reason")
    metadata: dict[str, object] = {
        "result": "denied",
        "reason": reason if isinstance(reason, str) else "authorization_policy_denied",
        "method": request.method,
        "path": route_path if isinstance(route_path, str) else "unresolved",
    }
    request_id = getattr(request.state, "request_id", None)
    if isinstance(request_id, str) and request_id:
        metadata["request_id"] = request_id[:200]
    permissions = _safe_permission_codes(exc)
    if permissions:
        metadata["permissions"] = permissions

    factory = session_factory or AppSessionLocal
    async with factory() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.user_id', :value, true)"),
                {"value": str(user_id)},
            )
            await session.execute(
                text("SELECT set_config('app.tenant_id', :value, true)"),
                {"value": str(tenant_id)},
            )
            if "request_id" in metadata:
                await session.execute(
                    text("SELECT set_config('app.request_id', :value, true)"),
                    {"value": metadata["request_id"]},
                )
            await AuditService(AuditRepository(session)).log_authorization_denied(
                tenant_id=tenant_id,
                user_id=user_id,
                metadata=metadata,
            )


async def aurum_error_handler(request: Request, exc: AurumError) -> JSONResponse:
    logger.warning(
        "domain_error",
        code=exc.code,
        message=exc.message,
        details=exc.details,
        path=request.url.path,
    )
    if isinstance(exc, AuthorizationPolicyDeniedError):
        try:
            await record_authorization_denial(request, exc)
        except Exception:
            logger.exception(
                "authorization_denial_audit_failed",
                path=request.url.path,
            )
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            },
        },
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception(
        "unhandled_error",
        path=request.url.path,
        exc_type=type(exc).__name__,
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "Internal server error",
                "details": {},
            },
        },
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AurumError, aurum_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, unhandled_error_handler)
