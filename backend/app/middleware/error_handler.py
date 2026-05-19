"""Translate domain exceptions and unhandled errors into a uniform JSON envelope."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.errors import AurumError
from app.core.logging import get_logger

logger = get_logger("error_handler")


async def aurum_error_handler(request: Request, exc: AurumError) -> JSONResponse:
    logger.warning(
        "domain_error",
        code=exc.code,
        message=exc.message,
        details=exc.details,
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
