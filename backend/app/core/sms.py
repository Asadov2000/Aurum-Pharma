"""Payom's documented message API; OTP lifecycle belongs to the auth domain.

The account-specific HTTPS endpoint and sender must be supplied by Payom.
HTTP acceptance is not a delivery receipt. We neither log response bodies nor
retry uncertain sends: a timed-out request may already have sent a paid SMS.
"""

from __future__ import annotations

import re

import httpx

from app.core.config import Settings, get_settings
from app.core.errors import ServiceUnavailableError


async def send_sms_code(
    phone: str,
    code: str,
    *,
    config: Settings | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Submit a short OTP without exposing contacts, codes or provider secrets."""
    if not re.fullmatch(r"\+992[0-9]{9}", phone) or not re.fullmatch(r"[0-9]{6}", code):
        raise ValueError("Invalid SMS destination or code format")
    settings = config or get_settings()
    if settings.SMS_PROVIDER == "disabled":
        raise ServiceUnavailableError("SMS sign-in is not configured")
    if settings.SMS_PROVIDER == "development":
        if settings.ENVIRONMENT != "development":
            raise ServiceUnavailableError("Development SMS is unavailable")
        return
    if settings.PAYOM_API_TOKEN is None:
        raise ServiceUnavailableError("SMS sign-in is not configured")
    # The key stays in the Authorization header, never the URL. Redirects and
    # environment proxies are disabled so they cannot forward it elsewhere.
    try:
        async with httpx.AsyncClient(
            timeout=settings.SMS_TIMEOUT_SECONDS,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            async with client.stream(
                "POST",
                settings.PAYOM_API_URL,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {settings.PAYOM_API_TOKEN.get_secret_value()}",
                },
                json={
                    "telephone": phone,
                    "text": f"Aurum Pharma: код {code}. Никому не сообщайте.",
                    "senderName": settings.PAYOM_SENDER_NAME,
                    "type": "SMS",
                },
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise ServiceUnavailableError("SMS provider did not accept the request")
    except httpx.HTTPError:
        # Provider exceptions may contain sensitive requests. Do not attach them
        # to the API exception or print their response text in application logs.
        raise ServiceUnavailableError("SMS provider is temporarily unavailable") from None
