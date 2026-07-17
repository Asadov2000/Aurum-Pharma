from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from pydantic import SecretStr

import app.main as main_module


async def test_metrics_endpoint_exposes_prometheus_payload(client: AsyncClient) -> None:
    response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert b"# HELP" in response.content
    assert b"python_info" in response.content


async def test_metrics_endpoint_hides_payload_without_token_outside_development(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_module,
        "settings",
        SimpleNamespace(ENVIRONMENT="production", METRICS_TOKEN=SecretStr("m" * 40)),
    )

    response = await client.get("/metrics")

    assert response.status_code == 404
    assert b"# HELP" not in response.content


async def test_metrics_endpoint_accepts_configured_bearer_token(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "m" * 40
    monkeypatch.setattr(
        main_module,
        "settings",
        SimpleNamespace(ENVIRONMENT="production", METRICS_TOKEN=SecretStr(token)),
    )

    response = await client.get("/metrics", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert b"# HELP" in response.content
