from __future__ import annotations

from httpx import AsyncClient


async def test_healthz(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.headers["x-request-id"]


async def test_readyz_reports_database(client: AsyncClient) -> None:
    response = await client.get("/readyz")
    assert response.status_code == 200
    body = response.json()
    assert body["checks"]["postgres"] == "ok"
    assert body["checks"]["chroma"] == "ok"
