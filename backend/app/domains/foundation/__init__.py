"""Foundation domain — tenant, settings, branches, registers."""

from app.domains.foundation.router import admin_router, tenant_router

__all__ = ["admin_router", "tenant_router"]
