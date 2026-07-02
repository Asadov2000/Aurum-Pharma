from __future__ import annotations

from httpx import AsyncClient


async def test_metrics_endpoint_exposes_prometheus_payload(client: AsyncClient) -> None:
    response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert b"# HELP" in response.content
    assert b"python_info" in response.content
