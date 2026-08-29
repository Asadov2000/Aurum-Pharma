"""FastAPI entry point.

Middleware order matters: Starlette wraps middleware bottom-up, so the *last*
`add_middleware` call ends up outermost. In non-development environments we want:

    request → SecurityHeaders → trusted proxy/host → RequestId
            → AuthContext → CORS → app

So inner application middleware is added first and perimeter middleware last.
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from starlette.middleware.trustedhost import TrustedHostMiddleware
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app.core.config import get_settings
from app.core.db import app_engine, support_engine
from app.core.logging import configure_logging, get_logger
from app.core.redis import redis_client
from app.domains.audit import admin_router as audit_admin_router
from app.domains.audit import router as audit_router
from app.domains.auth import router as auth_router
from app.domains.billing.router import admin_router as billing_admin_router
from app.domains.billing.router import platform_router as billing_platform_router
from app.domains.billing.router import tenant_router as billing_tenant_router
from app.domains.catalog import router as catalog_router
from app.domains.customer_returns import router as customer_returns_router
from app.domains.dashboard import router as dashboard_router
from app.domains.foundation.router import admin_router as foundation_admin_router
from app.domains.foundation.router import tenant_router as foundation_tenant_router
from app.domains.incoming import router as incoming_router
from app.domains.inventory import router as inventory_router
from app.domains.notifications import router as notifications_router
from app.domains.onboarding import router as onboarding_router
from app.domains.platform_access import router as platform_access_router
from app.domains.platform_accounts.router import (
    activation_router as platform_accounts_activation_router,
)
from app.domains.platform_accounts.router import admin_router as platform_accounts_admin_router
from app.domains.pos import router as pos_router
from app.domains.roles import router as roles_router
from app.domains.suppliers import router as suppliers_router
from app.domains.support_access import router as support_access_router
from app.domains.sync.router import admin_router as sync_admin_router
from app.domains.sync.router import router as sync_router
from app.middleware.auth_context import AuthContextMiddleware
from app.middleware.edge_read_only import EdgeReadOnlyMiddleware
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
    docs_url="/docs" if settings.ENVIRONMENT == "development" else None,
    redoc_url="/redoc" if settings.ENVIRONMENT == "development" else None,
    openapi_url="/openapi.json" if settings.ENVIRONMENT == "development" else None,
)

# CRITICAL: with allow_credentials=True, allow_methods / allow_headers MUST be
# explicit lists — wildcard "*" is rejected by browsers per the CORS spec.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Aurum-Support-Session",
        "X-Request-ID",
    ],
    expose_headers=["X-Request-ID"],
)

app.add_middleware(AuthContextMiddleware)
app.add_middleware(
    EdgeReadOnlyMiddleware,
    enabled=settings.DEPLOYMENT_PROFILE == "edge_shadow",
)
app.add_middleware(RequestIdMiddleware)
if settings.ENVIRONMENT != "development":
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.TRUSTED_HOSTS)
    app.add_middleware(
        ProxyHeadersMiddleware,
        trusted_hosts=settings.TRUSTED_PROXY_IPS,
    )
# Outermost: stamps security headers on every non-development response. Dev/test
# remain untouched; CSP ships Report-Only because the SPA enforces its own policy.
app.add_middleware(
    SecurityHeadersMiddleware,
    enabled=settings.ENVIRONMENT != "development",
)

register_error_handlers(app)

app.include_router(auth_router)
app.include_router(foundation_admin_router)
app.include_router(foundation_tenant_router)
app.include_router(support_access_router)
app.include_router(platform_access_router)
app.include_router(platform_accounts_admin_router)
app.include_router(platform_accounts_activation_router)
app.include_router(roles_router)
app.include_router(catalog_router)
app.include_router(inventory_router)
app.include_router(suppliers_router)
app.include_router(incoming_router)
app.include_router(pos_router)
app.include_router(customer_returns_router)
app.include_router(billing_tenant_router)
app.include_router(billing_admin_router)
app.include_router(billing_platform_router)
app.include_router(audit_admin_router)
app.include_router(audit_router)
app.include_router(onboarding_router)
app.include_router(notifications_router)
app.include_router(dashboard_router)
app.include_router(sync_admin_router)
app.include_router(sync_router)


@app.get("/healthz", tags=["meta"])
async def healthz() -> JSONResponse:
    db_ok = False
    redis_ok = False

    try:
        async with app_engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            db_ok = result.scalar() == 1
    except Exception as exc:
        logger.warning("healthz_db_fail", error_type=type(exc).__name__)

    try:
        redis_ok = bool(await redis_client.ping())
    except Exception as exc:
        logger.warning("healthz_redis_fail", error_type=type(exc).__name__)

    status = "ok" if db_ok and redis_ok else "degraded"
    return JSONResponse(
        status_code=200 if status == "ok" else 503,
        content={"status": status, "db": db_ok, "redis": redis_ok},
    )


@app.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    if settings.ENVIRONMENT != "development":
        authorization = request.headers.get("authorization", "")
        scheme, separator, supplied = authorization.partition(" ")
        expected = (
            settings.METRICS_TOKEN.get_secret_value() if settings.METRICS_TOKEN is not None else ""
        )
        if (
            not separator
            or scheme.lower() != "bearer"
            or not expected
            or not secrets.compare_digest(supplied, expected)
        ):
            # Do not advertise an operational endpoint to unauthenticated callers.
            raise HTTPException(status_code=404, detail="Not Found")
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
