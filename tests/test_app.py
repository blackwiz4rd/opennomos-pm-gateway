from __future__ import annotations

import httpx
import pytest

from pm_gateway.app import app
from pm_gateway import Operation, PMGateway, Provider
from pm_gateway.generated.apis.discovery_api_base import BaseDiscoveryApi
from pm_gateway.generated.models.container_page import ContainerPage


@pytest.mark.asyncio
async def test_health_check_is_available_without_gateway_token() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_api_fails_closed_when_gateway_token_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PM_GATEWAY_API_TOKEN", raising=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/pm/v1/capabilities")

    assert response.status_code == 503
    assert response.json()["code"] == "internal_error"


@pytest.mark.asyncio
async def test_api_requires_gateway_bearer_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PM_GATEWAY_API_TOKEN", "expected-secret")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/pm/v1/capabilities",
            headers={"Authorization": "Bearer wrong-secret"},
        )

    assert response.status_code == 401
    assert response.json()["code"] == "unauthenticated"


@pytest.mark.asyncio
async def test_capabilities_are_scoped_to_the_request_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PM_GATEWAY_API_TOKEN", "expected-secret")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/pm/v1/capabilities",
            headers={
                "Authorization": "Bearer expected-secret",
                "X-PM-Provider": "plane",
                "X-PM-Provider-Credential": "ephemeral-test-key",
            },
        )

    assert response.status_code == 200
    assert response.json()["provider"]["key"] == "plane"
    assert response.json()["features"]


@pytest.mark.asyncio
async def test_sdk_and_http_api_share_capabilities_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PM_GATEWAY_API_TOKEN", "expected-secret")
    sdk_result = await PMGateway().execute(
        Operation.GET_CAPABILITIES,
        provider=Provider.PLANE,
        credential="ephemeral-test-key",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/pm/v1/capabilities",
            headers={
                "Authorization": "Bearer expected-secret",
                "X-PM-Provider": "plane",
                "X-PM-Provider-Credential": "ephemeral-test-key",
            },
        )

    assert response.status_code == 200
    assert response.json() == sdk_result.to_dict()


@pytest.mark.asyncio
async def test_list_containers_accepts_bounded_integer_query_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PM_GATEWAY_API_TOKEN", "expected-secret")
    received_limits: list[int | None] = []

    class FakeDiscoveryApi:
        async def list_containers(
            self,
            workspace_id: str,
            provider: str,
            credential: str,
            cursor: str | None,
            limit: int | None,
            base_url: str | None,
        ) -> ContainerPage:
            received_limits.append(limit)
            return ContainerPage(items=[])

    monkeypatch.setattr(BaseDiscoveryApi, "subclasses", (FakeDiscoveryApi,))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/pm/v1/workspaces/shopflow/containers?limit=200",
            headers={
                "Authorization": "Bearer expected-secret",
                "X-PM-Provider": "plane",
                "X-PM-Provider-Credential": "ephemeral-test-key",
            },
        )

    assert response.status_code == 200
    assert received_limits == [200]


@pytest.mark.asyncio
async def test_unsupported_provider_feature_returns_contract_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PM_GATEWAY_API_TOKEN", "expected-secret")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/pm/v1/workspaces/ws-1/work-items/item-1/comments",
            headers={
                "Authorization": "Bearer expected-secret",
                "X-PM-Provider": "plane",
                "X-PM-Provider-Credential": "ephemeral-test-key",
            },
        )

    assert response.status_code == 501
    assert response.json()["code"] == "not_supported"


@pytest.mark.asyncio
async def test_oversized_request_body_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PM_GATEWAY_MAX_BODY_BYTES", "10")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/healthz", content=b"x" * 100)

    assert response.status_code == 413
    assert response.json()["code"] == "invalid_request"


@pytest.mark.asyncio
async def test_responses_include_baseline_security_headers() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
