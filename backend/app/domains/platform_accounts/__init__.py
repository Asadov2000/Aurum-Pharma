"""Aurum Pharma platform team account lifecycle."""

from app.domains.platform_accounts.router import activation_router, admin_router

__all__ = ["activation_router", "admin_router"]
