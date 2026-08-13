"""Billing domain — plans, subscriptions, invoices, payments."""

from app.domains.billing.router import admin_router, platform_router, tenant_router

__all__ = ["admin_router", "platform_router", "tenant_router"]
