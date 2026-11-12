from __future__ import annotations

import json

import httpx
import pytest

from pm_gateway.adapters.clickup import ClickUpAPIError, ClickUpProvider
from pm_gateway.adapters.factory import validate_provider_context
from pm_gateway.adapters.serialization import as_work_item
from pm_gateway.impl.provider_api import PMGateway
from pm_gateway.sdk import Provider

CREDENTIAL = "pk_super_secret_clickup_token"

LIST = {
    "id": "901",
    "name": "Sprint Backlog",
    "task_count": 3,
}

STATUS_TODO = {"id": "s-todo", "status": "to do", "type": "open"}
STATUS_DOING = {"id": "s-doing", "status": "in progress", "type": "custom"}
STATUS_DONE = {"id": "s-done", "status": "complete", "type": "closed"}

TASK = {
    "id": "abc123",
    "custom_id": "SPR-42",
    "name": "Ship the ClickUp adapter",
    "text_content": "Ships the integration.",
    "description": "<p>Ships the integration.</p>",
    "status": STATUS_DOING,
    "priority": {"id": "2", "priority": "high", "color": "#ff0000"},
    "creator": {"id": 9, "username": "Ada"},
    "assignees": [{"id": 123, "username": "Ada Lovelace", "email": "ada@example.com"}],
    "tags": [{"name": "backend"}, {"name": "urgent"}],
    "parent": None,
    "date_created": "1759312800000",
    "date_updated": "1759404600000",
    "date_done": None,
    "start_date": "1759276800000",
    "due_date": "1761955200000",
    "points": 5.0,
    "url": "https://app.clickup.com/t/abc123",
    "list": {"id": "901", "name": "Sprint Backlog"},
    "folder": {"id": "55", "name": "Q4"},
    "space": {"id": "77", "name": "Engineering"},
    "custom_fields": [
        {"id": "cf-1", "name": "Environment", "value": "staging"},
        {"id": "cf-2", "name": "Empty", "value": None},
    ],
}


class FakeClickUp:
    """A tiny ClickUp REST double driven by request path."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.responses: dict[str, dict] = {}
        self.status_code = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(
            {
                "method": request.method,
                "url": str(request.url),
                "path": request.url.path,
                "headers": dict(request.headers),
                "params": dict(request.url.params),
                "body": json.loads(request.content) if request.content else None,
            }
        )
        path = request.url.path
        for fragment, body in self.responses.items():
            if fragment in path:
                return httpx.Response(self.status_code, json=body)
        raise AssertionError(f"Unexpected ClickUp request: {request.method} {path}")

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def provider(self, base_url: str | None = None) -> ClickUpProvider:
        return ClickUpProvider(CREDENTIAL, base_url, transport=self.transport)


def _task_page(*items: dict, last_page: bool = True) -> dict:
    return {"tasks": list(items), "last_page": last_page}


def test_default_endpoint_and_authorization_header() -> None:
    fake = FakeClickUp()
    fake.responses["/task"] = _task_page(TASK)

    provider = fake.provider()
    items = _run(provider.list_work_items("77", "901"))

    assert [item["id"] for item in items] == ["abc123"]
    request = fake.requests[0]
    assert request["method"] == "GET"
    assert request["url"].startswith("https://api.clickup.com/api/v2/list/901/task")
    # ClickUp uses the raw token, without a Bearer prefix.
    assert request["headers"]["authorization"] == CREDENTIAL


def test_base_url_is_normalized() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_task_page())

    for base in (
        "https://clickup-proxy.example/api/v2",
        "https://clickup-proxy.example/api/v2/",
        "https://clickup-proxy.example",
    ):
        provider = ClickUpProvider(CREDENTIAL, base, transport=httpx.MockTransport(handler))
        _run(provider.list_work_items("77", "901"))
        assert seen["url"].startswith("https://clickup-proxy.example/api/v2/list/901/task")


def test_tasks_are_paginated_until_last_page() -> None:
    calls = {"count": 0}
    pages: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        pages.append(dict(request.url.params))
        if calls["count"] == 1:
            return httpx.Response(
                200, json=_task_page(TASK, last_page=False)
            )
        return httpx.Response(
            200, json=_task_page({**TASK, "id": "def456"}, last_page=True)
        )

    provider = ClickUpProvider(CREDENTIAL, transport=httpx.MockTransport(handler))
    items = _run(provider.list_work_items("77", "901"))

    assert [item["id"] for item in items] == ["abc123", "def456"]
    assert pages[0]["page"] == "0"
    assert pages[1]["page"] == "1"


def test_item_cap_truncates_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PM_GATEWAY_CLICKUP_MAX_ITEMS", "1")
    fake = FakeClickUp()
    fake.responses["/task"] = _task_page(TASK, {**TASK, "id": "def456"}, last_page=False)

    items = _run(fake.provider().list_work_items("77", "901"))

    assert [item["id"] for item in items] == ["abc123"]


def test_work_item_fields_are_normalized() -> None:
    fake = FakeClickUp()
    fake.responses["/task"] = _task_page(TASK)

    normalized = _run(fake.provider().list_work_items("77", "901"))[0]

    assert normalized["id"] == "abc123"
    assert normalized["identifier"] == "SPR-42"
    assert normalized["title"] == "Ship the ClickUp adapter"
    assert normalized["description"] == "Ships the integration."
    assert normalized["description_content_type"] == "text/plain"
    assert normalized["state"] == "in progress"
    assert normalized["state_id"] == "s-doing"
    assert normalized["state_category"] == "started"
    assert normalized["priority"] == "high"
    assert normalized["priority_value"] == 2
    assert normalized["assignee"] == "Ada Lovelace"
    assert normalized["assignee_id"] == "123"
    assert normalized["creator"] == "Ada"
    assert normalized["creator_id"] == "9"
    assert normalized["start_date"] == "2025-10-01"
    assert normalized["due_date"] == "2025-11-01"
    assert normalized["created_at"] == "2025-10-01T10:00:00+00:00"
    assert normalized["updated_at"] == "2025-10-02T11:30:00+00:00"
    assert normalized["labels"] == ["backend", "urgent"]
    assert normalized["estimate"] == 5.0
    assert normalized["custom_fields"] == {"Environment": "staging"}
    assert normalized["project_id"] == "901"
    assert normalized["project_name"] == "Sprint Backlog"
    assert normalized["team_id"] == "77"
    assert normalized["team_name"] == "Engineering"
    assert normalized["item_type"] == "task"
    assert normalized["url"] == "https://app.clickup.com/t/abc123"
    assert normalized["provider"] == "clickup"
    assert normalized["memberships"] == [
        {
            "project_id": "901",
            "project_name": "Sprint Backlog",
            "folder_id": "55",
            "folder_name": "Q4",
            "space_id": "77",
            "space_name": "Engineering",
        }
    ]


def test_subtasks_are_flagged() -> None:
    normalized = ClickUpProvider(CREDENTIAL)._task_to_dict(
        {**TASK, "id": "child", "parent": "abc123"}, "901"
    )

    assert normalized["parent_id"] == "abc123"
    assert normalized["item_type"] == "subtask"


def test_done_status_maps_to_completed() -> None:
    normalized = ClickUpProvider(CREDENTIAL)._task_to_dict(
        {**TASK, "status": STATUS_DONE}, "901"
    )

    assert normalized["state_category"] == "completed"


def test_work_item_projection_follows_the_contract() -> None:
    fake = FakeClickUp()
    fake.responses["/task"] = _task_page(TASK)
    normalized = _run(fake.provider().list_work_items("77", "901"))[0]

    item = as_work_item(
        normalized,
        provider="clickup",
        workspace_id="77",
        container_id="901",
        base_url=None,
    )
    payload = item.to_dict()

    assert payload["id"] == "abc123"
    assert payload["identifier"] == "SPR-42"
    assert payload["status"]["id"] == "s-doing"
    assert payload["status"]["category"] == "started"
    assert payload["assignees"][0]["displayName"] == "Ada Lovelace"
    assert str(payload["dates"]["dueDate"]) == "2025-11-01"
    assert payload["memberships"][0]["id"] == "901"
    assert payload["source"]["provider"] == "clickup"
    assert payload["source"]["resourceType"] == "task"
    assert payload["url"] == "https://app.clickup.com/t/abc123"
    assert "raw" not in payload
    assert "additional_properties" not in repr(payload)
    assert CREDENTIAL not in json.dumps(payload, default=str)


def test_get_work_item_uses_the_task_endpoint() -> None:
    fake = FakeClickUp()
    fake.responses["/task/abc123"] = TASK

    normalized = _run(fake.provider().get_work_item("77", "901", "abc123"))

    assert normalized["id"] == "abc123"
    assert normalized["project_id"] == "901"
    assert fake.requests[0]["path"] == "/api/v2/task/abc123"


def test_missing_work_item_is_a_not_found_error() -> None:
    fake = FakeClickUp()
    fake.responses["/task/404"] = {}

    with pytest.raises(ClickUpAPIError) as excinfo:
        _run(fake.provider().get_work_item("77", "901", "404"))

    assert excinfo.value.status_code == 404


def test_workflow_states_are_list_statuses() -> None:
    fake = FakeClickUp()
    fake.responses["/list/901"] = {
        "id": "901",
        "name": "Sprint Backlog",
        "statuses": [STATUS_TODO, STATUS_DOING, STATUS_DONE],
    }

    states = _run(fake.provider().list_states("77", "901"))

    assert states == [
        {"id": "s-todo", "name": "to do", "group": "open"},
        {"id": "s-doing", "name": "in progress", "group": "custom"},
        {"id": "s-done", "name": "complete", "group": "closed"},
    ]


def test_transition_moves_the_task_by_status_id_or_name() -> None:
    fake = FakeClickUp()
    fake.responses["/list/901"] = {
        "id": "901",
        "statuses": [STATUS_TODO, STATUS_DOING, STATUS_DONE],
    }
    fake.responses["/task/abc123"] = TASK
    provider = fake.provider()

    _run(provider.update_work_item("77", "901", "abc123", {"state": "s-done"}))
    _run(provider.update_work_item("77", "901", "abc123", {"state": "complete"}))
    _run(provider.update_work_item("77", "901", "abc123", {"state": "COMPLETE"}))

    mutations = [request for request in fake.requests if request["method"] == "PUT"]
    assert [m["path"] for m in mutations] == ["/api/v2/task/abc123"] * 3
    assert all(m["body"] == {"status": "complete"} for m in mutations)


def test_unknown_transition_target_is_a_client_error() -> None:
    fake = FakeClickUp()
    fake.responses["/list/901"] = {"id": "901", "statuses": [STATUS_TODO, STATUS_DOING]}

    with pytest.raises(ValueError, match="No ClickUp status"):
        _run(fake.provider().update_work_item("77", "901", "abc123", {"state": "Nope"}))


def test_missing_transition_target_is_a_client_error() -> None:
    provider = ClickUpProvider(CREDENTIAL, transport=httpx.MockTransport(lambda _: None))

    with pytest.raises(ValueError, match="target ClickUp status"):
        _run(provider.update_work_item("77", "901", "abc123", {}))


def test_api_errors_are_sanitized() -> None:
    fake = FakeClickUp()
    fake.status_code = 401
    fake.responses["/task"] = {"err": f"Invalid token {CREDENTIAL}", "ECODE": "OAUTH_017"}

    with pytest.raises(ClickUpAPIError) as excinfo:
        _run(fake.provider().list_work_items("77", "901"))

    message = str(excinfo.value)
    assert excinfo.value.status_code == 401
    assert excinfo.value.code == "OAUTH_017"
    assert CREDENTIAL not in message
    assert "[redacted]" in message


def test_http_errors_expose_the_upstream_status() -> None:
    fake = FakeClickUp()
    fake.status_code = 429
    fake.responses["/task"] = {"tasks": []}

    with pytest.raises(ClickUpAPIError) as excinfo:
        _run(fake.provider().list_work_items("77", "901"))

    assert excinfo.value.status_code == 429


def test_invalid_json_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    provider = ClickUpProvider(CREDENTIAL, transport=httpx.MockTransport(handler))

    with pytest.raises(ClickUpAPIError, match="invalid response"):
        _run(provider.list_work_items("77", "901"))


def test_empty_workspace_ids_are_rejected() -> None:
    provider = ClickUpProvider(CREDENTIAL, transport=httpx.MockTransport(lambda _: None))

    with pytest.raises(ValueError, match="must not be empty"):
        _run(provider.list_projects("   "))


def test_board_filters_are_rejected() -> None:
    provider = ClickUpProvider(CREDENTIAL, transport=httpx.MockTransport(lambda _: None))

    with pytest.raises(ValueError, match="board or iteration"):
        _run(provider.list_work_items("77", "901", board_id="1"))


def test_boards_iterations_and_scope_are_absent() -> None:
    provider = ClickUpProvider(CREDENTIAL, transport=httpx.MockTransport(lambda _: None))

    assert _run(provider.list_boards("77", "901")) == []
    assert _run(provider.list_sprints("77", "901")) == []
    assert _run(provider.get_board_scope("77", "901")) == {
        "board_id": None,
        "scope": "all",
        "sprint": None,
        "sprints": [],
    }


def test_lists_are_discovered_across_spaces_and_folders() -> None:
    fake = FakeClickUp()
    fake.responses["/team/77/space"] = {"spaces": [{"id": "77", "name": "Engineering"}]}
    fake.responses["/space/77/list"] = {"lists": [LIST]}
    fake.responses["/space/77/folder"] = {"folders": [{"id": "55", "name": "Q4"}]}
    fake.responses["/folder/55/list"] = {"lists": [{"id": "902", "name": "Q4 Backlog"}]}

    projects = _run(fake.provider().list_projects("77"))

    assert [project["id"] for project in projects] == ["901", "902"]
    assert [project["name"] for project in projects] == ["Sprint Backlog", "Q4 Backlog"]


def test_validate_slug_reports_invalid_workspaces() -> None:
    fake = FakeClickUp()
    fake.responses["/team/77/space"] = {"spaces": []}

    provider = fake.provider()

    assert _run(provider.validate_slug("77")) == {
        "slug": "77",
        "valid": True,
        "projects_count": 0,
    }
    assert _run(provider.validate_slug("")) is None


def test_clickup_is_a_supported_provider() -> None:
    validate_provider_context("clickup", CREDENTIAL, None)
    validate_provider_context("clickup", CREDENTIAL, "https://api.clickup.com/api/v2")
    with pytest.raises(ValueError, match="not allowlisted"):
        validate_provider_context("clickup", CREDENTIAL, "https://attacker.example")
    with pytest.raises(ValueError, match="must not be empty"):
        validate_provider_context("clickup", "", None)
    assert Provider.CLICKUP.value == "clickup"


def test_capabilities_describe_clickup_limits() -> None:
    capabilities = _run(
        PMGateway().execute(
            "get_capabilities",
            provider="clickup",
            credential=CREDENTIAL,
        )
    )

    assert capabilities.provider.key == "clickup"
    assert capabilities.provider.product_name == "ClickUp"
    features = {feature.name: feature.supported for feature in capabilities.features}
    assert features["work-items.read"] is True
    assert features["work-items.transition"] is True
    assert features["workflow-states.list"] is True
    assert features["boards.list"] is False
    assert features["iterations.list"] is False
    assert features["comments.read"] is False


def test_clickup_items_flow_through_the_standard_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import asynccontextmanager

    from pm_gateway.app import app
    from pm_gateway.impl import provider_api

    normalized = ClickUpProvider(CREDENTIAL)._task_to_dict(TASK, "901")

    class FakeAdapter:
        async def list_work_items(self, workspace, container, **kwargs):
            assert (workspace, container) == ("77", "901")
            return [normalized]

    @asynccontextmanager
    async def fake_open_provider(provider, credential, base_url, *, mode=None):
        assert provider == "clickup"
        assert credential == CREDENTIAL
        yield FakeAdapter()

    monkeypatch.setenv("PM_GATEWAY_API_TOKEN", "gateway-secret")
    monkeypatch.setattr(provider_api, "open_provider", fake_open_provider)

    async def request():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            return await client.get(
                "/pm/v1/workspaces/77/containers/901/work-items",
                headers={
                    "Authorization": "Bearer gateway-secret",
                    "X-PM-Provider": "clickup",
                    "X-PM-Provider-Credential": CREDENTIAL,
                },
            )

    response = _run(request())

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["status"]["category"] == "started"
    assert payload["items"][0]["source"]["provider"] == "clickup"
    assert payload["items"][0]["source"]["resourceType"] == "task"
    assert CREDENTIAL not in response.text


def test_invalidate_closes_the_credential_bearing_client() -> None:
    fake = FakeClickUp()
    fake.responses["/task"] = _task_page()
    provider = fake.provider()

    _run(provider.list_work_items("77", "901"))
    assert provider._client is not None
    _run(provider.invalidate())
    assert provider._client is None


def _run(awaitable):
    import asyncio

    return asyncio.run(awaitable)
