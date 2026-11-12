from __future__ import annotations

import json

import httpx
import pytest

from pm_gateway.adapters.asana import AsanaAPIError, AsanaProvider
from pm_gateway.adapters.factory import validate_provider_context
from pm_gateway.adapters.serialization import as_work_item
from pm_gateway.impl.provider_api import PMGateway
from pm_gateway.sdk import Provider

CREDENTIAL = "super-secret-asana-token"

PROJECT = {
    "gid": "101",
    "name": "Roadmap",
    "permalink_url": "https://app.asana.com/1/77/project/101",
    "resource_subtype": "default_project",
}

SECTION_TODO = {"gid": "s-todo", "name": "To do"}
SECTION_DOING = {"gid": "s-doing", "name": "Doing"}
SECTION_DONE = {"gid": "s-done", "name": "Done"}

TASK = {
    "gid": "501",
    "name": "Ship the Asana adapter",
    "notes": "Ships the integration.",
    "completed": False,
    "completed_at": None,
    "created_at": "2026-10-01T10:00:00.000Z",
    "modified_at": "2026-10-02T11:30:00.000Z",
    "due_on": "2026-11-01",
    "start_on": "2026-10-01",
    "assignee": {"gid": "123", "name": "Ada Lovelace"},
    "created_by": {"gid": "9", "name": "Ada"},
    "parent": None,
    "memberships": [
        {"project": {"gid": "101", "name": "Roadmap"}, "section": SECTION_DOING},
        {"project": {"gid": "202", "name": "Platform"}, "section": SECTION_TODO},
    ],
    "tags": [{"gid": "t1", "name": "backend"}, {"gid": "t2", "name": "urgent"}],
    "custom_fields": [
        {
            "gid": "cf-points",
            "name": "Story Points",
            "display_value": "5",
            "number_value": 5,
        },
        {"gid": "cf-notes", "name": "Notes", "display_value": "Keep it small"},
    ],
    "permalink_url": "https://app.asana.com/1/77/task/501",
    "resource_subtype": "default_task",
    "num_subtasks": 2,
}


class FakeAsana:
    """A tiny Asana REST double driven by request path."""

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
        raise AssertionError(f"Unexpected Asana request: {request.method} {path}")

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def provider(self, base_url: str | None = None) -> AsanaProvider:
        return AsanaProvider(CREDENTIAL, base_url, transport=self.transport)


def _page(*items: dict, offset: str | None = None) -> dict:
    payload: dict = {"data": list(items)}
    if offset:
        payload["next_page"] = {"offset": offset, "path": "/tasks", "uri": "https://x"}
    return payload


def test_default_endpoint_and_authorization_header() -> None:
    fake = FakeAsana()
    fake.responses["/projects"] = _page(PROJECT)

    provider = fake.provider()
    projects = _run(provider.list_projects("77"))

    assert projects == [
        {
            "id": "101",
            "name": "Roadmap",
            "identifier": None,
            "url": "https://app.asana.com/1/77/project/101",
        }
    ]
    request = fake.requests[0]
    assert request["method"] == "GET"
    assert request["url"].startswith("https://app.asana.com/api/1.0/workspaces/77/projects")
    assert request["headers"]["authorization"] == f"Bearer {CREDENTIAL}"
    assert request["params"]["opt_fields"] == "gid,name,permalink_url,resource_subtype"

def test_base_url_is_normalized() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json=_page())

    for base in (
        "https://asana-proxy.example/api/1.0",
        "https://asana-proxy.example/api/1.0/",
        "https://asana-proxy.example",
    ):
        provider = AsanaProvider(CREDENTIAL, base, transport=httpx.MockTransport(handler))
        _run(provider.list_projects("77"))
        assert seen["url"].startswith("https://asana-proxy.example/api/1.0/workspaces/77/projects")


def test_projects_are_paginated_through_the_offset() -> None:
    fake = FakeAsana()
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        fake.requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "params": dict(request.url.params),
                "headers": dict(request.headers),
                "url": str(request.url),
            }
        )
        if calls["count"] == 1:
            return httpx.Response(200, json=_page(PROJECT, offset="offset-1"))
        return httpx.Response(200, json=_page({**PROJECT, "gid": "102", "name": "Last"}))

    provider = AsanaProvider(CREDENTIAL, transport=httpx.MockTransport(handler))
    projects = _run(provider.list_projects("77"))

    assert [project["id"] for project in projects] == ["101", "102"]
    assert fake.requests[0]["params"]["offset"] if "offset" in fake.requests[0]["params"] else True
    assert fake.requests[1]["params"]["offset"] == "offset-1"


def test_item_cap_truncates_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PM_GATEWAY_ASANA_MAX_ITEMS", "1")
    fake = FakeAsana()
    fake.responses["/tasks"] = _page(TASK, {**TASK, "gid": "502"}, offset="more")

    items = _run(fake.provider().list_work_items("77", "101"))

    assert [item["id"] for item in items] == ["501"]


def test_work_item_fields_are_normalized() -> None:
    fake = FakeAsana()
    fake.responses["/tasks"] = _page(TASK)

    normalized = _run(fake.provider().list_work_items("77", "101"))[0]

    assert normalized["id"] == "501"
    assert normalized["identifier"] == "501"
    assert normalized["title"] == "Ship the Asana adapter"
    assert normalized["description"] == "Ships the integration."
    assert normalized["description_content_type"] == "text/plain"
    assert normalized["state"] == "Doing"
    assert normalized["state_id"] == "s-doing"
    assert normalized["state_category"] == "started"
    assert normalized["assignee"] == "Ada Lovelace"
    assert normalized["assignee_id"] == "123"
    assert normalized["creator_id"] == "9"
    assert normalized["start_date"] == "2026-10-01"
    assert normalized["due_date"] == "2026-11-01"
    assert normalized["created_at"] == "2026-10-01T10:00:00+00:00"
    assert normalized["updated_at"] == "2026-10-02T11:30:00+00:00"
    assert normalized["labels"] == ["backend", "urgent"]
    assert normalized["estimate"] == 5.0
    assert normalized["custom_fields"] == {
        "Story Points": "5",
        "Notes": "Keep it small",
    }
    assert normalized["project_id"] == "101"
    assert normalized["project_name"] == "Roadmap"
    assert normalized["item_type"] == "task"
    assert normalized["url"] == "https://app.asana.com/1/77/task/501"
    assert normalized["provider"] == "asana"
    assert normalized["memberships"] == [
        {
            "project_id": "101",
            "project_name": "Roadmap",
            "section_id": "s-doing",
            "section_name": "Doing",
        },
        {
            "project_id": "202",
            "project_name": "Platform",
            "section_id": "s-todo",
            "section_name": "To do",
        },
    ]
    assert "raw" in normalized


def test_primary_membership_follows_the_requested_project() -> None:
    normalized = AsanaProvider(CREDENTIAL)._task_to_dict(TASK, "202")

    assert normalized["project_id"] == "202"
    assert normalized["project_name"] == "Platform"
    assert normalized["state"] == "To do"
    assert normalized["state_category"] == "unstarted"


def test_completed_task_with_unknown_section_is_completed() -> None:
    normalized = AsanaProvider(CREDENTIAL)._task_to_dict(
        {
            "gid": "1",
            "name": "Done thing",
            "completed": True,
            "memberships": [
                {"project": {"gid": "101"}, "section": {"gid": "s", "name": "Shipped"}}
            ],
        },
        "101",
    )

    assert normalized["state"] == "Shipped"
    assert normalized["state_category"] == "completed"


def test_subtasks_are_flagged() -> None:
    normalized = AsanaProvider(CREDENTIAL)._task_to_dict(
        {**TASK, "gid": "777", "parent": {"gid": "501"}}, "101"
    )

    assert normalized["parent_id"] == "501"
    assert normalized["item_type"] == "subtask"


def test_work_item_projection_follows_the_contract() -> None:
    fake = FakeAsana()
    fake.responses["/tasks"] = _page(TASK)
    normalized = _run(fake.provider().list_work_items("77", "101"))[0]

    item = as_work_item(
        normalized,
        provider="asana",
        workspace_id="77",
        container_id="101",
        base_url=None,
    )
    payload = item.to_dict()

    assert payload["id"] == "501"
    assert payload["status"]["id"] == "s-doing"
    assert payload["status"]["category"] == "started"
    assert payload["assignees"][0]["displayName"] == "Ada Lovelace"
    assert str(payload["dates"]["dueDate"]) == "2026-11-01"
    assert payload["memberships"][0]["id"] == "101"
    assert payload["source"]["provider"] == "asana"
    assert payload["source"]["resourceType"] == "task"
    assert payload["url"] == "https://app.asana.com/1/77/task/501"
    # Multi-project tasks keep every membership in the namespaced extensions.
    assert payload["extensions"]["asana:native"]["memberships"][1]["project_id"] == "202"
    assert "raw" not in payload
    assert "additional_properties" not in repr(payload)
    assert CREDENTIAL not in json.dumps(payload, default=str)


def test_get_work_item_uses_the_task_endpoint() -> None:
    fake = FakeAsana()
    fake.responses["/tasks/501"] = {"data": TASK}

    normalized = _run(fake.provider().get_work_item("77", "101", "501"))

    assert normalized["id"] == "501"
    assert normalized["project_id"] == "101"
    assert fake.requests[0]["path"] == "/api/1.0/tasks/501"


def test_missing_work_item_is_a_not_found_error() -> None:
    fake = FakeAsana()
    fake.responses["/tasks/404"] = {"data": {}}

    with pytest.raises(AsanaAPIError) as excinfo:
        _run(fake.provider().get_work_item("77", "101", "404"))

    assert excinfo.value.status_code == 404


def test_workflow_states_are_project_sections() -> None:
    fake = FakeAsana()
    fake.responses["/sections"] = _page(SECTION_TODO, SECTION_DOING, SECTION_DONE)

    states = _run(fake.provider().list_states("77", "101"))

    assert states == [
        {"id": "s-todo", "name": "To do", "group": "To do"},
        {"id": "s-doing", "name": "Doing", "group": "Doing"},
        {"id": "s-done", "name": "Done", "group": "Done"},
    ]


def test_transition_moves_the_task_by_section_id_or_name() -> None:
    fake = FakeAsana()
    fake.responses["/sections"] = _page(SECTION_TODO, SECTION_DOING, SECTION_DONE)
    fake.responses["/addTask"] = {"data": {}}
    fake.responses["/tasks/501"] = {"data": TASK}
    provider = fake.provider()

    _run(provider.update_work_item("77", "101", "501", {"state": "s-done"}))
    _run(provider.update_work_item("77", "101", "501", {"state": "done"}))
    _run(provider.update_work_item("77", "101", "501", {"state": "DONE"}))

    mutations = [request for request in fake.requests if request["path"].endswith("/addTask")]
    assert [m["path"] for m in mutations] == ["/api/1.0/sections/s-done/addTask"] * 3
    assert all(m["body"] == {"data": {"task": "501"}} for m in mutations)


def test_unknown_transition_target_is_a_client_error() -> None:
    fake = FakeAsana()
    fake.responses["/sections"] = _page(SECTION_TODO, SECTION_DOING)

    with pytest.raises(ValueError, match="No Asana section"):
        _run(fake.provider().update_work_item("77", "101", "501", {"state": "Nope"}))


def test_missing_transition_target_is_a_client_error() -> None:
    provider = AsanaProvider(CREDENTIAL, transport=httpx.MockTransport(lambda _: None))

    with pytest.raises(ValueError, match="target Asana section"):
        _run(provider.update_work_item("77", "101", "501", {}))


def test_api_errors_are_sanitized() -> None:
    fake = FakeAsana()
    fake.status_code = 401
    fake.responses["/projects"] = {
        "errors": [{"message": f"Invalid token {CREDENTIAL}", "code": "unauthorized"}]
    }

    with pytest.raises(AsanaAPIError) as excinfo:
        _run(fake.provider().list_projects("77"))

    message = str(excinfo.value)
    assert excinfo.value.status_code == 401
    assert excinfo.value.code == "unauthorized"
    assert CREDENTIAL not in message
    assert "[redacted]" in message


def test_http_errors_expose_the_upstream_status() -> None:
    fake = FakeAsana()
    fake.status_code = 429
    fake.responses["/projects"] = {"data": []}

    with pytest.raises(AsanaAPIError) as excinfo:
        _run(fake.provider().list_projects("77"))

    assert excinfo.value.status_code == 429


def test_invalid_json_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    provider = AsanaProvider(CREDENTIAL, transport=httpx.MockTransport(handler))

    with pytest.raises(AsanaAPIError, match="invalid response"):
        _run(provider.list_projects("77"))


def test_non_numeric_workspace_ids_are_rejected() -> None:
    provider = AsanaProvider(CREDENTIAL, transport=httpx.MockTransport(lambda _: None))

    with pytest.raises(ValueError, match="numeric"):
        _run(provider.list_projects("acme"))


def test_board_filters_are_rejected() -> None:
    provider = AsanaProvider(CREDENTIAL, transport=httpx.MockTransport(lambda _: None))

    with pytest.raises(ValueError, match="board or iteration"):
        _run(provider.list_work_items("77", "101", board_id="1"))


def test_boards_iterations_and_scope_are_absent() -> None:
    provider = AsanaProvider(CREDENTIAL, transport=httpx.MockTransport(lambda _: None))

    assert _run(provider.list_boards("77", "101")) == []
    assert _run(provider.list_sprints("77", "101")) == []
    assert _run(provider.get_board_scope("77", "101")) == {
        "board_id": None,
        "scope": "all",
        "sprint": None,
        "sprints": [],
    }


def test_validate_slug_reports_invalid_workspaces() -> None:
    fake = FakeAsana()
    fake.responses["/projects"] = _page(PROJECT)
    provider = fake.provider()

    assert _run(provider.validate_slug("77")) == {
        "slug": "77",
        "valid": True,
        "projects_count": 1,
    }
    assert _run(provider.validate_slug("not-a-number")) is None


def test_asana_is_a_supported_provider() -> None:
    validate_provider_context("asana", CREDENTIAL, None)
    validate_provider_context("asana", CREDENTIAL, "https://app.asana.com/api/1.0")
    with pytest.raises(ValueError, match="not allowlisted"):
        validate_provider_context("asana", CREDENTIAL, "https://attacker.example")
    with pytest.raises(ValueError, match="must not be empty"):
        validate_provider_context("asana", "", None)
    assert Provider.ASANA.value == "asana"


def test_capabilities_describe_asana_limits() -> None:
    capabilities = _run(
        PMGateway().execute(
            "get_capabilities",
            provider="asana",
            credential=CREDENTIAL,
        )
    )

    assert capabilities.provider.key == "asana"
    assert capabilities.provider.product_name == "Asana"
    features = {feature.name: feature.supported for feature in capabilities.features}
    assert features["work-items.read"] is True
    assert features["work-items.transition"] is True
    assert features["workflow-states.list"] is True
    assert features["boards.list"] is False
    assert features["iterations.list"] is False
    assert features["comments.read"] is False


def test_asana_items_flow_through_the_standard_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import asynccontextmanager

    from pm_gateway.app import app
    from pm_gateway.impl import provider_api

    normalized = AsanaProvider(CREDENTIAL)._task_to_dict(TASK, "101")

    class FakeAdapter:
        async def list_work_items(self, workspace, container, **kwargs):
            assert (workspace, container) == ("77", "101")
            return [normalized]

    @asynccontextmanager
    async def fake_open_provider(provider, credential, base_url, *, mode=None):
        assert provider == "asana"
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
                "/pm/v1/workspaces/77/containers/101/work-items",
                headers={
                    "Authorization": "Bearer gateway-secret",
                    "X-PM-Provider": "asana",
                    "X-PM-Provider-Credential": CREDENTIAL,
                },
            )

    response = _run(request())

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["status"]["category"] == "started"
    assert payload["items"][0]["source"]["provider"] == "asana"
    assert payload["items"][0]["source"]["resourceType"] == "task"
    assert CREDENTIAL not in response.text


def test_invalidate_closes_the_credential_bearing_client() -> None:
    fake = FakeAsana()
    fake.responses["/projects"] = _page()
    provider = fake.provider()

    _run(provider.list_projects("77"))
    assert provider._client is not None
    _run(provider.invalidate())
    assert provider._client is None


def _run(awaitable):
    import asyncio

    return asyncio.run(awaitable)
