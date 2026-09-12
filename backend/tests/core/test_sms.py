"""SMS integration never sends network traffic in tests."""

from __future__ import annotations

import json
from typing import Literal

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.core.config import Settings
from app.core.errors import ServiceUnavailableError
from app.core.sms import send_sms_code


def sms_settings(
    *,
    provider: Literal["disabled", "development", "payom"] = "payom",
    endpoint: str = "https://sms.example.test/api/message",
    token: str = "test-only-provider-key",
    environment: Literal["development", "staging", "production"] = "development",
) -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_URL_APP="postgresql+asyncpg://app:test@localhost/aurum_test",
        DATABASE_URL_SUPPORT="postgresql+asyncpg://support:test@localhost/aurum_test",
        JWT_SECRET="test-only-jwt-key",
        ENVIRONMENT=environment,
        SMS_PROVIDER=provider,
        PAYOM_API_URL=endpoint,
        PAYOM_API_TOKEN=SecretStr(token),
    )


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://HOST/api/message",
        "http://sms.example.test/api/message",
        "https://key@sms.example.test/api/message",
        "https://sms.example.test/api/message?key=secret",
        "https://sms.example.test/api/message#fragment",
        "https://sms.example.test:8443/api/message",
        "https://sms.example.test:bad/api/message",
        "https://sms.example.test/another-endpoint",
    ],
)
def test_payom_requires_explicit_https_message_endpoint(endpoint: str) -> None:
    with pytest.raises(ValidationError, match="PAYOM_API_URL"):
        sms_settings(endpoint=endpoint)


def test_payom_requires_key_and_redacts_settings() -> None:
    with pytest.raises(ValidationError, match="PAYOM_API_TOKEN"):
        sms_settings(token=" ")
    configured = sms_settings()
    assert "test-only-provider-key" not in repr(configured)
    assert configured.PAYOM_API_URL not in repr(configured)


@pytest.mark.parametrize("token", ["key\r\ninjected", "key with spaces", "ключ"])
def test_payom_key_cannot_inject_headers(token: str) -> None:
    with pytest.raises(ValidationError, match="PAYOM_API_TOKEN"):
        sms_settings(token=token)


@pytest.mark.parametrize("environment", ["staging", "production"])
def test_development_sms_cannot_start_outside_development(
    environment: Literal["staging", "production"],
) -> None:
    with pytest.raises(ValidationError, match="Development SMS"):
        sms_settings(provider="development", environment=environment)


@pytest.mark.asyncio
async def test_payom_request_matches_documented_contract() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "POST"
        assert request.url.path == "/api/message"
        assert request.url.query == b""
        assert request.headers["Authorization"] == "Bearer test-only-provider-key"
        body = json.loads(request.content)
        assert body == {
            "telephone": "+992900000001",
            "text": "Aurum Pharma: код 123456. Никому не сообщайте.",
            "senderName": "AurumPharma",
            "type": "SMS",
        }
        assert len(body["text"]) <= 70
        return httpx.Response(202)

    await send_sms_code(
        "+992900000001", "123456", config=sms_settings(), transport=httpx.MockTransport(handler)
    )
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 307, 400, 401, 429, 500])
async def test_provider_failures_are_sanitized_and_never_retried(status: int) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            status,
            headers={"Location": "https://other.example.test/api/message"},
            text="sensitive provider response",
        )

    with pytest.raises(ServiceUnavailableError) as failure:
        await send_sms_code(
            "+992900000001", "123456", config=sms_settings(), transport=httpx.MockTransport(handler)
        )
    assert "sensitive" not in str(failure.value)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_uncertain_timeout_does_not_resend_or_expose_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        raise httpx.ReadTimeout("sensitive upstream error", request=request)

    with pytest.raises(ServiceUnavailableError) as failure:
        await send_sms_code(
            "+992900000001", "123456", config=sms_settings(), transport=httpx.MockTransport(handler)
        )
    assert "sensitive" not in str(failure.value)
    assert failure.value.__cause__ is None
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_disabled_and_development_modes_make_no_network_requests() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("Local SMS modes must never call a provider")

    transport = httpx.MockTransport(handler)
    with pytest.raises(ServiceUnavailableError):
        await send_sms_code(
            "+992900000001", "123456", config=sms_settings(provider="disabled"), transport=transport
        )
    await send_sms_code(
        "+992900000001", "123456", config=sms_settings(provider="development"), transport=transport
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("phone,code", [("+79990000000", "123456"), ("+992900000001", "abcdef")])
async def test_rejects_invalid_destination_or_code(phone: str, code: str) -> None:
    with pytest.raises(ValueError, match="Invalid SMS"):
        await send_sms_code(phone, code, config=sms_settings(provider="development"))
