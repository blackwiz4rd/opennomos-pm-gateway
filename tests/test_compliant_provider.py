from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest
from fastapi import HTTPException

from pm_gateway import Operation, PMGateway, Provider
from pm_gateway.adapters.compliant import CompliantProvider
from pm_gateway.adapters.factory import validate_provider_context
from pm_gateway.app import app
from pm_gateway.generated.models.transition_request import TransitionRequest
from pm_gateway.generated.models.work_item_patch import WorkItemPatch
from pm_gateway.impl import provider_api
from pm_gateway.impl.provider_api import _dispatch_compliant

_PROVIDER = {"key": "compliant", "productName": "Remote"}


def _fake_open_provider(adapter: CompliantProvider):
    @asynccontextmanager
    async def open_provider(provider, credential, base_url):
        assert provider == "compliant"
        yield adapter

    return open_provider


def _workspace_page() -> dict[str, Any]:
    return {
        "items": [{"id": "ws-1", "name": "Workspace 1", "provider": _PROVIDER}],
        "nextCursor": None,
    }


def _work_item(work_item_id: str = "wi-1") -> dict[str, Any]:
    return {
        "id": work_item_id,
        "title": "Fix login",
        "status": {
            "id": "state-1",
            "name": "In Progress",
            "category": "started",
            "mapping": {"quality": "mapped"},
        },
        "source": {"provider": "compliant", "resourceType": "work-item"},
    }


def _work_item_page() -> dict[str, Any]:
    return {"items": [_work_item()], "nextCursor": None}


def test_compliant_base_url_is_required_and_normalized() -> None:
    with pytest.raises(ValueError, match="required for compliant providers"):
        validate_provider_context("compliant", "secret", None)

    assert CompliantProvider("secret", "https://remote.example")._base_url == (
        "https://remote.example/pm/v1"
    )
    assert CompliantProvider("secret", "https://remote.example/pm/v1")._base_url == (
        "https://remote.example/pm/v1"
    )


def test_unknown_provider_alias_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported PM provider"):
        validate_provider_context("standard", "secret", "https://remote.example")


@pytest.mark.asyncio
async def test_compliant_request_uses_bearer_auth_and_drops_empty_query_values() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = request.url
        seen["authorization"] = request.headers.get("Authorization")
        return httpx.Response(200, json=_workspace_page())

    adapter = CompliantProvider(
        "ephemeral-secret",
        "https://remote.example",
        transport=httpx.MockTransport(handler),
    )

    payload = await adapter.request(
        "GET",
        "/workspaces",
        params={"cursor": None, "limit": 10},
    )

    assert payload == _workspace_page()
    assert seen["url"].path == "/pm/v1/workspaces"
    assert dict(seen["url"].params) == {"limit": "10"}
    assert seen["authorization"] == "Bearer ephemeral-secret"


@pytest.mark.asyncio
async def test_compliant_request_propagates_upstream_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PM_GATEWAY_EXPOSE_UPSTREAM_ERRORS", "1")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "workspace not found"})

    adapter = CompliantProvider(
        "secret",
        "https://remote.example",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(HTTPException) as error:
        await adapter.request("GET", "/workspaces/missing")

    assert error.value.status_code == 404
    assert error.value.detail == "The PM provider returned HTTP 404: workspace not found"


@pytest.mark.asyncio
async def test_compliant_list_work_items_forwards_filters_and_preserves_payload() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = request.url.params
        return httpx.Response(200, json=_work_item_page())

    adapter = CompliantProvider(
        "secret",
        "https://remote.example",
        transport=httpx.MockTransport(handler),
    )
    changed_since = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)

    result = await _dispatch_compliant(
        "list_work_items",
        {
            "workspaceId": "ws-1",
            "containerId": "project-1",
            "cursor": "next",
            "limit": 25,
            "changed_since": changed_since,
            "state_id": "state-1",
            "assignee_id": "user-1",
            "archived": False,
            "deleted": True,
            "query": "login",
            "sort": "-updatedAt",
            "board_id": "board-1",
            "iteration_ids": ["sprint-1", "sprint-2"],
        },
        adapter,
    )

    assert seen["path"] == "/pm/v1/workspaces/ws-1/containers/project-1/work-items"
    assert seen["params"]["changedSince"] == "2026-01-02T03:04:05+00:00"
    assert seen["params"]["stateId"] == "state-1"
    assert seen["params"]["assigneeId"] == "user-1"
    assert seen["params"]["archived"] == "false"
    assert seen["params"]["deleted"] == "true"
    assert seen["params"]["query"] == "login"
    assert seen["params"]["sort"] == "-updatedAt"
    assert seen["params"]["boardId"] == "board-1"
    assert seen["params"].get_list("iterationIds") == ["sprint-1", "sprint-2"]

    assert result.items[0].id == "wi-1"
    assert result.items[0].title == "Fix login"
    assert result.items[0].status.name == "In Progress"


@pytest.mark.asyncio
async def test_compliant_workflow_states_forwards_board_id() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": "state-1",
                        "name": "Todo",
                        "category": "unstarted",
                        "mapping": {"quality": "mapped"},
                    }
                ]
            },
        )

    adapter = CompliantProvider(
        "secret",
        "https://remote.example",
        transport=httpx.MockTransport(handler),
    )

    result = await _dispatch_compliant(
        "list_workflow_states",
        {"workspaceId": "ws-1", "containerId": "project-1", "board_id": "board-1"},
        adapter,
    )

    assert seen["params"] == {"boardId": "board-1"}
    assert result.items[0].name == "Todo"


@pytest.mark.asyncio
async def test_sdk_compliant_provider_returns_remote_standard_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/pm/v1/workspaces"
        return httpx.Response(200, json=_workspace_page())

    adapter = CompliantProvider(
        "ephemeral-secret",
        "https://remote.example",
        transport=httpx.MockTransport(handler),
    )

    monkeypatch.setattr(provider_api, "open_provider", _fake_open_provider(adapter))

    result = await PMGateway().execute(
        Operation.LIST_WORKSPACES,
        provider=Provider.COMPLIANT,
        credential="ephemeral-secret",
        base_url="https://remote.example",
    )

    assert [workspace.id for workspace in result.items] == ["ws-1"]


@pytest.mark.asyncio
async def test_http_compliant_provider_returns_remote_standard_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/pm/v1/workspaces/ws-1/containers/c-1/work-items"
        return httpx.Response(200, json=_work_item_page())

    adapter = CompliantProvider(
        "ephemeral-secret",
        "https://remote.example",
        transport=httpx.MockTransport(handler),
    )

    monkeypatch.setenv("PM_GATEWAY_API_TOKEN", "gateway-secret")
    monkeypatch.setattr(provider_api, "open_provider", _fake_open_provider(adapter))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/pm/v1/workspaces/ws-1/containers/c-1/work-items",
            headers={
                "Authorization": "Bearer gateway-secret",
                "X-PM-Provider": "compliant",
                "X-PM-Provider-Credential": "ephemeral-secret",
                "X-PM-Provider-Base-Url": "https://remote.example",
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["id"] == "wi-1"
    assert payload["items"][0]["title"] == "Fix login"
    assert "additional_properties" not in response.text


@pytest.mark.asyncio
async def test_compliant_update_work_item_forwards_merge_patch_if_match_and_etag() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["body"] = json.loads(request.content)
        seen["if_match"] = request.headers.get("If-Match")
        return httpx.Response(200, json=_work_item(), headers={"ETag": '"rev-2"'})

    adapter = CompliantProvider(
        "secret",
        "https://remote.example",
        transport=httpx.MockTransport(handler),
    )

    # WorkItemPatch.from_dict is the generator's documented constructor; fields
    # the caller omitted must stay absent from the forwarded merge-patch body
    # (RFC 7386: an omitted field is unchanged, an explicit null clears it).
    patch = WorkItemPatch.from_dict({"title": "Renamed", "description": None})

    result = await _dispatch_compliant(
        "update_work_item",
        {
            "workspaceId": "ws-1",
            "workItemId": "wi-1",
            "work_item_patch": patch,
            "if_match": '"rev-1"',
        },
        adapter,
    )

    assert seen["method"] == "PATCH"
    assert seen["body"] == {"title": "Renamed", "description": None}
    assert seen["if_match"] == '"rev-1"'
    assert result.payload.id == "wi-1"
    assert result.headers == {"ETag": '"rev-2"'}


@pytest.mark.asyncio
async def test_http_compliant_update_work_item_propagates_etag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        assert json.loads(request.content) == {"title": "Renamed"}
        return httpx.Response(200, json=_work_item(), headers={"ETag": '"rev-2"'})

    adapter = CompliantProvider(
        "ephemeral-secret",
        "https://remote.example",
        transport=httpx.MockTransport(handler),
    )

    monkeypatch.setenv("PM_GATEWAY_API_TOKEN", "gateway-secret")
    monkeypatch.setattr(provider_api, "open_provider", _fake_open_provider(adapter))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.patch(
            "/pm/v1/workspaces/ws-1/work-items/wi-1",
            headers={
                "Authorization": "Bearer gateway-secret",
                "X-PM-Provider": "compliant",
                "X-PM-Provider-Credential": "ephemeral-secret",
                "X-PM-Provider-Base-Url": "https://remote.example",
            },
            json={"title": "Renamed"},
        )

    assert response.status_code == 200
    assert response.headers["etag"] == '"rev-2"'
    assert response.json()["id"] == "wi-1"


@pytest.mark.asyncio
async def test_compliant_transition_work_item_forwards_post_body() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_work_item())

    adapter = CompliantProvider(
        "secret",
        "https://remote.example",
        transport=httpx.MockTransport(handler),
    )

    await _dispatch_compliant(
        "transition_work_item",
        {
            "workspaceId": "ws-1",
            "workItemId": "wi-1",
            "transition_request": TransitionRequest.from_dict({"targetStateId": "state-2"}),
        },
        adapter,
    )

    assert seen["method"] == "POST"
    assert seen["path"] == "/pm/v1/workspaces/ws-1/work-items/wi-1/transitions"
    # The unused arm of the targetStateId/targetStateName oneOf must not leak
    # into the body as a null a strict remote would reject.
    assert seen["body"] == {"targetStateId": "state-2"}


@pytest.mark.asyncio
async def test_compliant_list_workspaces_forwards_workspace_slugs_header() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["slugs"] = request.headers.get("X-PM-Workspace-Slugs")
        return httpx.Response(200, json=_workspace_page())

    adapter = CompliantProvider(
        "secret",
        "https://remote.example",
        transport=httpx.MockTransport(handler),
    )

    await _dispatch_compliant(
        "list_workspaces",
        {"x_pm_workspace_slugs": "alpha,beta", "cursor": None, "limit": None},
        adapter,
    )

    assert seen["slugs"] == "alpha,beta"


@pytest.mark.asyncio
async def test_compliant_paths_percent_encode_ids() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        # raw_path is the encoded form that actually goes over the wire.
        seen["path"] = request.url.raw_path
        return httpx.Response(200, json={"items": []})

    adapter = CompliantProvider(
        "secret",
        "https://remote.example",
        transport=httpx.MockTransport(handler),
    )

    await _dispatch_compliant(
        "list_containers",
        {"workspaceId": "../../admin"},
        adapter,
    )

    # A client-supplied ID must never escape the /pm/v1 route template on the
    # remote host.
    assert seen["path"] == b"/pm/v1/workspaces/..%2F..%2Fadmin/containers"


@pytest.mark.asyncio
async def test_compliant_upstream_rate_limit_forwards_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PM_GATEWAY_EXPOSE_UPSTREAM_ERRORS", "1")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"code": "rate_limited", "message": "slow down"},
            headers={"Retry-After": "30"},
        )

    adapter = CompliantProvider(
        "secret",
        "https://remote.example",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(HTTPException) as error:
        await adapter.request("GET", "/workspaces")

    assert error.value.status_code == 429
    assert error.value.detail == "The PM provider returned HTTP 429: slow down"
    assert error.value.headers == {"Retry-After": "30"}


@pytest.mark.asyncio
async def test_http_compliant_rate_limit_forwards_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"message": "slow down"},
            headers={"Retry-After": "7"},
        )

    adapter = CompliantProvider(
        "ephemeral-secret",
        "https://remote.example",
        transport=httpx.MockTransport(handler),
    )

    monkeypatch.setenv("PM_GATEWAY_API_TOKEN", "gateway-secret")
    monkeypatch.setattr(provider_api, "open_provider", _fake_open_provider(adapter))

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/pm/v1/workspaces",
            headers={
                "Authorization": "Bearer gateway-secret",
                "X-PM-Provider": "compliant",
                "X-PM-Provider-Credential": "ephemeral-secret",
                "X-PM-Provider-Base-Url": "https://remote.example",
            },
        )

    assert response.status_code == 429
    assert response.json()["code"] == "rate_limited"
    assert response.headers["retry-after"] == "7"


@pytest.mark.asyncio
async def test_compliant_upstream_error_body_without_message_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PM_GATEWAY_EXPOSE_UPSTREAM_ERRORS", "1")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json=["boom"])

    adapter = CompliantProvider(
        "secret",
        "https://remote.example",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(HTTPException) as error:
        await adapter.request("GET", "/workspaces")

    assert error.value.status_code == 502
    assert "boom" in error.value.detail


@pytest.mark.asyncio
async def test_compliant_upstream_error_body_is_hidden_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PM_GATEWAY_EXPOSE_UPSTREAM_ERRORS", raising=False)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "internal-only-detail"})

    adapter = CompliantProvider(
        "secret",
        "https://remote.example",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(HTTPException) as error:
        await adapter.request("GET", "/workspaces")

    assert error.value.status_code == 400
    assert error.value.detail == "The PM provider returned HTTP 400."
    assert "internal-only-detail" not in error.value.detail
