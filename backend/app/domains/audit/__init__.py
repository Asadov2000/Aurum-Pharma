"""Audit domain — immutable change log + read endpoints + PII filtering."""

from app.domains.audit.router import router

__all__ = ["router"]
