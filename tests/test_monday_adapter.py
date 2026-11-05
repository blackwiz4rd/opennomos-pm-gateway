from __future__ import annotations

import json

import httpx
import pytest

from pm_gateway.adapters.factory import validate_provider_context
from pm_gateway.adapters.monday import MondayAPIError, MondayProvider
from pm_gateway.adapters.serialization import as_work_item
from pm_gateway.impl.provider_api import PMGateway
from pm_gateway.sdk import Provider

CREDENTIAL = "super-secret-monday-token"

BOARD = {
    "id": "101",
    "name": "Roadmap",
    "url": "https://acme.monday.com/boards/101",
    "workspace": {"id": "77", "name": "Product"},
    "groups": [
        {"id": "group_todo", "title": "To Do"},
        {"id": "group_progress", "title": "Working on it"},
        {"id": "group_done", "title": "Done"},
    ],
    "columns": [
        {"id": "status", "title": "Status", "type": "status"},
        {"id": "priority", "title": "Priority", "type": "status"},
        {"id": "owner", "title": "Owner", "type": "people"},
        {"id": "due", "title": "Due date", "type": "date"},
        {"id": "timeline", "title": "Timeline", "type": "timeline"},
        {"id": "labels", "title": "Labels", "type": "dropdown"},
        {"id": "points", "title": "Story Points", "type": "numbers"},
        {"id": "desc", "title": "Description", "type": "long_text"},
        {"id": "notes", "title": "Notes", "type": "text"},
    ],
}

ITEM = {
    "id": "501",
    "name": "Ship monday adapter",
    "url": "https://acme.monday.com/boards/101/pulses/501",
    "created_at": "2026-10-01T10:00:00Z",
    "updated_at": "2026-10-02T11:30:00Z",
    "creator_id": "9",
    "creator": {"id": "9", "name": "Ada"},
    "group": {"id": "group_progress", "title": "Working on it"},
    "board": {"id": "101", "name": "Roadmap", "workspace": {"id": "77", "name": "Product"}},
    "column_values": [
        {
            "id": "status",
            "text": "In Progress",
            "value": '{"index":1}',
            "column": {"id": "status", "title": "Status", "type": "status"},
        },
        {
            "id": "priority",
            "text": "Critical",
            "value": '{"index":0}',
            "column": {"id": "priority", "title": "Priority", "type": "status"},
        },
        {
            "id": "owner",
            "text": "Ada Lovelace",
            "value": '{"personsAndTeams":[{"id":123,"kind":"person"}]}',
            "column": {"id": "owner", "title": "Owner", "type": "people"},
        },
        {
            "id": "due",
            "text": "2026-11-01",
            "value": '{"date":"2026-11-01","time":null}',
            "column": {"id": "due", "title": "Due date", "type": "date"},
        },
        {
            "id": "timeline",
            "text": "Oct 1 - Oct 5",
            "value": '{"from":"2026-10-01","to":"2026-10-05"}',
            "column": {"id": "timeline", "title": "Timeline", "type": "timeline"},
        },
        {
            "id": "labels",
            "text": "backend, urgent",
            "value": '{"ids":[1,2]}',
            "column": {"id": "labels", "title": "Labels", "type": "dropdown"},
        },
        {
            "id": "points",
            "text": "5",
            "value": '"5"',
            "column": {"id": "points", "title": "Story Points", "type": "numbers"},
        },
        {
            "id": "desc",
            "text": "Ships the integration.",
            "value": None,
            "column": {"id": "desc", "title": "Description", "type": "long_text"},
        },
        {
            "id": "notes",
            "text": "Keep it small",
            "value": None,
            "column": {"id": "notes", "title": "Notes", "type": "text"},
        },
    ],
}


class FakeMonday:
    """A tiny monday.com GraphQL double driven by operation names."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.responses: dict[str, dict] = {}
        self.status_code = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        query = payload.get("query", "")
        self.requests.append(
            {
                "method": request.method,
                "url": str(request.url),
                "headers": dict(request.headers),
                "query": query,
                "variables": payload.get("variables") or {},
            }
        )
        for operation, body in self.responses.items():
            if operation in query:
                return httpx.Response(self.status_code, json=body)
        raise AssertionError(f"Unexpected monday.com query: {query}")

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def provider(self, base_url: str | None = None) -> MondayProvider:
        return MondayProvider(CREDENTIAL, base_url, transport=self.transport)


def _board_items(*items: dict, cursor: str | None = None) -> dict:
    return {
        "data": {
            "boards": [
                {
                    **BOARD,
                    "items_page": {"cursor": cursor, "items": list(items)},
                }
            ]
        }
    }


def test_default_endpoint_and_authorization_header() -> None:
    fake = FakeMonday()
    fake.responses["query Boards"] = {
        "data": {"boards": [{"id": "101", "name": "Roadmap", "url": None}]}
    }

    provider = fake.provider()
    projects = _run(provider.list_projects("77"))

    assert projects == [
        {
            "id": "101",
            "name": "Roadmap",
            "identifier": None,
            "url": None,
            "workspace_id": None,
        }
    ]
    request = fake.requests[0]
    assert request["method"] == "POST"
    assert request["url"] == "https://api.monday.com/v2"
    assert request["headers"]["authorization"] == CREDENTIAL
    assert request["variables"]["workspaceIds"] == ["77"]


def test_base_url_and_bearer_prefix_are_preserved() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["authorization"] = request.headers["authorization"]
        return httpx.Response(200, json={"data": {"boards": []}})

    provider = MondayProvider(
        "Bearer oauth-token",
        "https://monday-proxy.example/v2/",
        transport=httpx.MockTransport(handler),
    )
    _run(provider.list_projects("77"))

    assert seen["url"] == "https://monday-proxy.example/v2"
    assert seen["authorization"] == "Bearer oauth-token"


def test_api_version_header_is_optional(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeMonday()
    fake.responses["query Boards"] = {"data": {"boards": []}}
    monkeypatch.setenv("PM_GATEWAY_MONDAY_API_VERSION", "2026-04")

    _run(fake.provider().list_projects("77"))

    assert fake.requests[0]["headers"]["api-version"] == "2026-04"


def test_boards_are_paginated(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeMonday()
    first_page = [{"id": str(index), "name": f"Board {index}"} for index in range(100)]
    second_page = [{"id": "999", "name": "Last board"}]
    fake.responses["query Boards"] = {"data": {"boards": first_page}}

    calls = {"count": 0}
    original = fake.handler

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 2:
            return httpx.Response(200, json={"data": {"boards": second_page}})
        return original(request)

    provider = MondayProvider(CREDENTIAL, transport=httpx.MockTransport(handler))
    projects = _run(provider.list_projects("77"))

    assert len(projects) == 101
    assert projects[-1]["name"] == "Last board"
    assert [request["variables"]["page"] for request in fake.requests] == [1]


def test_work_item_columns_are_normalized() -> None:
    fake = FakeMonday()
    fake.responses["query BoardItems"] = _board_items(ITEM)

    normalized = _run(fake.provider().list_work_items("77", "101"))[0]

    assert normalized["id"] == "501"
    assert normalized["identifier"] == "501"
    assert normalized["title"] == "Ship monday adapter"
    assert normalized["state"] == "Working on it"
    assert normalized["state_id"] == "group_progress"
    assert normalized["state_category"] == "started"
    assert normalized["priority"] == "Critical"
    assert normalized["priority_value"] == 1
    assert normalized["assignee"] == "Ada Lovelace"
    assert normalized["assignee_id"] == "123"
    assert normalized["creator"] == "Ada"
    assert normalized["creator_id"] == "9"
    assert normalized["start_date"] == "2026-10-01"
    assert normalized["due_date"] == "2026-11-01"
    assert normalized["estimate"] == 5.0
    assert normalized["labels"] == ["backend", "urgent"]
    assert normalized["description"] == "Ships the integration."
    assert normalized["description_content_type"] == "text/plain"
    assert normalized["project_id"] == "101"
    assert normalized["project_name"] == "Roadmap"
    assert normalized["team_id"] == "77"
    assert normalized["team_name"] == "Product"
    assert normalized["item_type"] == "item"
    assert normalized["url"] == "https://acme.monday.com/boards/101/pulses/501"
    assert normalized["custom_fields"] == {
        "Status": "In Progress",
        "Notes": "Keep it small",
    }
    assert "raw" in normalized


def test_work_item_projection_follows_the_contract() -> None:
    fake = FakeMonday()
    fake.responses["query BoardItems"] = _board_items(ITEM)
    normalized = _run(fake.provider().list_work_items("77", "101"))[0]

    item = as_work_item(
        normalized,
        provider="monday",
        workspace_id="77",
        container_id="101",
        base_url=None,
    )
    payload = item.to_dict()

    assert payload["id"] == "501"
    assert payload["status"]["id"] == "group_progress"
    assert payload["status"]["category"] == "started"
    assert payload["priority"]["level"] == "highest"
    assert payload["priority"]["nativeValue"] == "Critical"
    assert payload["assignees"][0]["displayName"] == "Ada Lovelace"
    assert str(payload["dates"]["dueDate"]) == "2026-11-01"
    assert payload["memberships"][0]["id"] == "101"
    assert payload["source"]["provider"] == "monday"
    assert payload["source"]["resourceType"] == "item"
    assert payload["url"] == "https://acme.monday.com/boards/101/pulses/501"
    assert "raw" not in payload
    assert "additional_properties" not in repr(payload)
    assert CREDENTIAL not in json.dumps(payload, default=str)


def test_items_page_follows_the_cursor() -> None:
    fake = FakeMonday()
    fake.responses["query BoardItems"] = _board_items(
        {**ITEM, "id": "1"}, cursor="cursor-1"
    )
    fake.responses["query NextItemsPage"] = {
        "data": {
            "next_items_page": {
                "cursor": None,
                "items": [{**ITEM, "id": "2"}],
            }
        }
    }
    provider = fake.provider()

    items = _run(provider.list_work_items("77", "101"))

    assert [item["id"] for item in items] == ["1", "2"]
    next_request = next(r for r in fake.requests if "NextItemsPage" in r["query"])
    assert next_request["variables"] == {"cursor": "cursor-1", "limit": 500}


def test_item_cap_truncates_results(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PM_GATEWAY_MONDAY_MAX_ITEMS", "1")
    fake = FakeMonday()
    fake.responses["query BoardItems"] = _board_items(
        {**ITEM, "id": "1"}, {**ITEM, "id": "2"}
    )

    items = _run(fake.provider().list_work_items("77", "101"))

    assert [item["id"] for item in items] == ["1"]


def test_get_work_item_uses_the_items_query_and_resolves_the_board() -> None:
    fake = FakeMonday()
    fake.responses["query ItemsById"] = {"data": {"items": [ITEM]}}

    normalized = _run(fake.provider().get_work_item("77", "", "501"))

    assert normalized["project_id"] == "101"
    assert fake.requests[0]["variables"] == {"ids": ["501"]}


def test_missing_work_item_is_a_not_found_error() -> None:
    fake = FakeMonday()
    fake.responses["query ItemsById"] = {"data": {"items": []}}

    with pytest.raises(MondayAPIError) as excinfo:
        _run(fake.provider().get_work_item("77", "101", "404"))

    assert excinfo.value.status_code == 404


def test_missing_board_is_a_not_found_error() -> None:
    fake = FakeMonday()
    fake.responses["query BoardItems"] = {"data": {"boards": []}}

    with pytest.raises(MondayAPIError) as excinfo:
        _run(fake.provider().list_work_items("77", "999"))

    assert excinfo.value.status_code == 404


def test_workflow_states_are_board_groups() -> None:
    fake = FakeMonday()
    fake.responses["query BoardGroups"] = {
        "data": {
            "boards": [
                {
                    "groups": [
                        {"id": "group_todo", "title": "To Do"},
                        {"id": "group_progress", "title": "Working on it"},
                        {"id": "group_done", "title": "Done"},
                    ]
                }
            ]
        }
    }

    states = _run(fake.provider().list_states("77", "101"))

    assert states == [
        {"id": "group_todo", "name": "To Do", "group": "To Do"},
        {"id": "group_progress", "name": "Working on it", "group": "Working on it"},
        {"id": "group_done", "name": "Done", "group": "Done"},
    ]


def test_transition_moves_the_item_by_group_id_or_title() -> None:
    fake = FakeMonday()
    fake.responses["query BoardGroups"] = {
        "data": {"boards": [{"groups": BOARD["groups"]}]}
    }
    fake.responses["mutation MoveItemToGroup"] = {
        "data": {"move_item_to_group": {"id": "501"}}
    }
    fake.responses["query ItemsById"] = {"data": {"items": [ITEM]}}
    provider = fake.provider()

    _run(provider.update_work_item("77", "101", "501", {"state": "group_done"}))
    _run(provider.update_work_item("77", "101", "501", {"state": "done"}))
    _run(provider.update_work_item("77", "101", "501", {"state": "DONE"}))

    mutations = [
        request for request in fake.requests if "MoveItemToGroup" in request["query"]
    ]
    assert [m["variables"]["groupId"] for m in mutations] == ["group_done"] * 3
    assert all(m["variables"]["itemId"] == "501" for m in mutations)


def test_unknown_transition_target_is_a_client_error() -> None:
    fake = FakeMonday()
    fake.responses["query BoardGroups"] = {
        "data": {"boards": [{"groups": BOARD["groups"]}]}
    }

    with pytest.raises(ValueError, match="No monday.com group"):
        _run(fake.provider().update_work_item("77", "101", "501", {"state": "Nope"}))


def test_graphql_errors_are_sanitized() -> None:
    fake = FakeMonday()
    fake.responses["query Boards"] = {
        "errors": [
            {
                "message": f"Invalid token {CREDENTIAL}",
                "extensions": {"code": "ResourceNotFound"},
            }
        ]
    }

    with pytest.raises(MondayAPIError) as excinfo:
        _run(fake.provider().list_projects("77"))

    message = str(excinfo.value)
    assert excinfo.value.status_code == 404
    assert CREDENTIAL not in message
    assert "[redacted]" in message


def test_http_errors_expose_the_upstream_status() -> None:
    fake = FakeMonday()
    fake.status_code = 429
    fake.responses["query Boards"] = {"data": {"boards": []}}

    with pytest.raises(MondayAPIError) as excinfo:
        _run(fake.provider().list_projects("77"))

    assert excinfo.value.status_code == 429


def test_invalid_json_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not json</html>")

    provider = MondayProvider(CREDENTIAL, transport=httpx.MockTransport(handler))

    with pytest.raises(MondayAPIError, match="invalid response"):
        _run(provider.list_projects("77"))


def test_non_numeric_workspace_ids_are_rejected() -> None:
    provider = MondayProvider(CREDENTIAL, transport=httpx.MockTransport(lambda _: None))

    with pytest.raises(ValueError, match="numeric"):
        _run(provider.list_projects("acme"))


def test_board_filters_are_rejected() -> None:
    provider = MondayProvider(CREDENTIAL, transport=httpx.MockTransport(lambda _: None))

    with pytest.raises(ValueError, match="board or iteration"):
        _run(provider.list_work_items("77", "101", board_id="1"))


def test_boards_iterations_and_scope_are_absent() -> None:
    provider = MondayProvider(CREDENTIAL, transport=httpx.MockTransport(lambda _: None))

    assert _run(provider.list_boards("77", "101")) == []
    assert _run(provider.list_sprints("77", "101")) == []
    assert _run(provider.get_board_scope("77", "101")) == {
        "board_id": None,
        "scope": "all",
        "sprint": None,
        "sprints": [],
    }


def test_subitems_are_flagged() -> None:
    subitem = {**ITEM, "id": "777", "parent_item": {"id": "501"}}

    normalized = MondayProvider._item_to_dict(subitem, BOARD)

    assert normalized["parent_id"] == "501"
    assert normalized["item_type"] == "subitem"


def test_legacy_column_type_and_text_are_supported() -> None:
    normalized = MondayProvider._item_to_dict(
        {
            "id": "1",
            "name": "Legacy board",
            "group": {"id": "g", "title": "Done"},
            "column_values": [
                {"id": "person", "type": "multiple-person", "text": "Ada", "value": None},
                {"id": "due", "type": "date", "text": "2026-01-02", "value": None},
            ],
        },
        {**BOARD, "id": "101", "name": "Roadmap"},
    )

    assert normalized["assignee"] == "Ada"
    assert normalized["state_category"] == "completed"
    assert normalized["due_date"] is None


def test_validate_slug_reports_invalid_workspaces() -> None:
    fake = FakeMonday()
    fake.responses["query Boards"] = {"data": {"boards": []}}
    provider = fake.provider()

    assert _run(provider.validate_slug("77")) == {
        "slug": "77",
        "valid": True,
        "projects_count": 0,
    }
    assert _run(provider.validate_slug("not-a-number")) is None


def test_monday_is_a_supported_provider() -> None:
    validate_provider_context("monday", CREDENTIAL, None)
    validate_provider_context("monday", CREDENTIAL, "https://api.monday.com")
    with pytest.raises(ValueError, match="not allowlisted"):
        validate_provider_context("monday", CREDENTIAL, "https://attacker.example")
    with pytest.raises(ValueError, match="must not be empty"):
        validate_provider_context("monday", "", None)
    assert Provider.MONDAY.value == "monday"


def test_capabilities_describe_monday_limits() -> None:
    capabilities = _run(
        PMGateway().execute(
            "get_capabilities",
            provider="monday",
            credential=CREDENTIAL,
        )
    )

    assert capabilities.provider.key == "monday"
    assert capabilities.provider.product_name == "monday.com"
    features = {feature.name: feature.supported for feature in capabilities.features}
    assert features["work-items.read"] is True
    assert features["work-items.transition"] is True
    assert features["workflow-states.list"] is True
    assert features["boards.list"] is False
    assert features["iterations.list"] is False
    assert features["comments.read"] is False


def test_monday_items_flow_through_the_standard_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contextlib import asynccontextmanager

    from pm_gateway.app import app
    from pm_gateway.impl import provider_api

    normalized = MondayProvider._item_to_dict(ITEM, BOARD)

    class FakeAdapter:
        async def list_work_items(self, workspace, container, **kwargs):
            assert (workspace, container) == ("77", "101")
            return [normalized]

    @asynccontextmanager
    async def fake_open_provider(provider, credential, base_url):
        assert provider == "monday"
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
                    "X-PM-Provider": "monday",
                    "X-PM-Provider-Credential": CREDENTIAL,
                },
            )

    response = _run(request())

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["status"]["category"] == "started"
    assert payload["items"][0]["source"]["provider"] == "monday"
    assert payload["items"][0]["source"]["resourceType"] == "item"
    assert CREDENTIAL not in response.text


def test_invalidate_closes_the_credential_bearing_client() -> None:
    fake = FakeMonday()
    fake.responses["query Boards"] = {"data": {"boards": []}}
    provider = fake.provider()

    _run(provider.list_projects("77"))
    assert provider._client is not None
    _run(provider.invalidate())
    assert provider._client is None


def _run(awaitable):
    import asyncio

    return asyncio.run(awaitable)
