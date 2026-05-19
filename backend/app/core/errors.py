"""Domain error hierarchy. Services raise these; the error handler maps them to HTTP."""

from __future__ import annotations

from typing import Any


class AurumError(Exception):
    code: str = "internal_error"
    http_status: int = 500
    message: str = "Internal server error"

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        if message is not None:
            self.message = message
        self.details: dict[str, Any] = details or {}
        super().__init__(self.message)


class NotFoundError(AurumError):
    code = "not_found"
    http_status = 404
    message = "Resource not found"


class ValidationError(AurumError):
    code = "validation_error"
    http_status = 422
    message = "Validation failed"


class BusinessRuleError(AurumError):
    code = "business_rule_violation"
    http_status = 422
    message = "Business rule violation"


class ConflictError(AurumError):
    code = "conflict"
    http_status = 409
    message = "Resource conflict"


class PermissionDeniedError(AurumError):
    code = "permission_denied"
    http_status = 403
    message = "Permission denied"


class AuthenticationError(AurumError):
    code = "authentication_required"
    http_status = 401
    message = "Authentication required"


class RateLimitError(AurumError):
    code = "rate_limited"
    http_status = 429
    message = "Too many requests"
