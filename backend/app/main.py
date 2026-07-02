"""FastAPI entry point.

Middleware order matters: Starlette wraps middleware bottom-up, so the *last*
`add_middleware` call ends up outermost. We want:

    request → RequestId → AuthContext → CORS → app

So we add CORS first, then AuthContext, then RequestId last.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import app_engine, support_engine
from app.core.logging import configure_logging, get_logger
from app.core.redis import redis_client
from app.domains.audit import router as audit_router
from app.domains.auth import router as auth_router
from app.domains.billing import admin_router as billing_admin_router
from app.domains.billing import tenant_router as billing_tenant_router
from app.domains.catalog import router as catalog_router
from app.domains.dashboard import router as dashboard_router
from app.domains.foundation import admin_router as foundation_admin_router
from app.domains.foundation import tenant_router as foundation_tenant_router
from app.domains.incoming import router as incoming_router
from app.domains.inventory import router as inventory_router
from app.domains.notifications import router as notifications_router
from app.domains.onboarding import router as onboarding_router
from app.domains.pos import router as pos_router
from app.domains.roles import router as roles_router
from app.domains.suppliers import router as suppliers_router
from app.middleware.auth_context import AuthContextMiddleware
from app.middleware.error_handler import register_error_handlers
from app.middleware.request_id import RequestIdMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware

configure_logging()
logger = get_logger("main")
settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    logger.info("startup", environment=settings.ENVIRONMENT, app_name=settings.APP_NAME)
    try:
        yield
    finally:
        await app_engine.dispose()
        await support_engine.dispose()
        await redis_client.aclose()
        logger.info("shutdown")


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    lifespan=lifespan,
)

# CRITICAL: with allow_credentials=True, allow_methods / allow_headers MUST be
# explicit lists — wildcard "*" is rejected by browsers per the CORS spec.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)

app.add_middleware(AuthContextMiddleware)
app.add_middleware(RequestIdMiddleware)
# Outermost: stamps security headers on every response. Production-only so
# dev/test/e2e are untouched; CSP ships Report-Only (see middleware docstring).
app.add_middleware(
    SecurityHeadersMiddleware,
    enabled=settings.ENVIRONMENT == "production",
)

register_error_handlers(app)

app.include_router(auth_router)
app.include_router(foundation_admin_router)
app.include_router(foundation_tenant_router)
app.include_router(roles_router)
app.include_router(catalog_router)
app.include_router(inventory_router)
app.include_router(suppliers_router)
app.include_router(incoming_router)
app.include_router(pos_router)
app.include_router(billing_tenant_router)
app.include_router(billing_admin_router)
app.include_router(audit_router)
app.include_router(onboarding_router)
app.include_router(notifications_router)
app.include_router(dashboard_router)


@app.get("/healthz", tags=["meta"])
async def healthz() -> dict[str, object]:
    db_ok = False
    redis_ok = False

    try:
        async with app_engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            db_ok = result.scalar() == 1
    except Exception as exc:
        logger.warning("healthz_db_fail", error=str(exc))

    try:
        redis_ok = bool(await redis_client.ping())
    except Exception as exc:
        logger.warning("healthz_redis_fail", error=str(exc))

    status = "ok" if db_ok and redis_ok else "degraded"
    return {"status": status, "db": db_ok, "redis": redis_ok}


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
