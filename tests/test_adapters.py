from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest

from pm_gateway.adapters.factory import open_provider
from pm_gateway.adapters.jira import JiraProvider
from pm_gateway.adapters.plane import PlaneProvider
from pm_gateway.adapters.planner import PlannerProvider
from pm_gateway.adapters.serialization import as_work_item
from pm_gateway.app import app
from pm_gateway.impl import provider_api


def _headers(provider: str = "plane") -> dict[str, str]:
    return {
        "Authorization": "Bearer gateway-secret",
        "X-PM-Provider": provider,
        "X-PM-Provider-Credential": "ephemeral-upstream-secret",
        **({"X-PM-Provider-Base-Url": "https://jira.example.test"} if provider == "jira" else {}),
    }


def test_plane_items_are_projected_without_returning_raw_payloads() -> None:
    provider = PlaneProvider("ephemeral-upstream-secret")
    normalized = provider._normalize_work_item(
        {
            "id": "issue-1",
            "sequence_id": 17,
            "name": "Fix login",
            "state": {"id": "state-1", "name": "In Progress", "group": "started"},
            "priority": "high",
            "assignees": [{"id": "user-1", "name": "Ada"}],
            "target_date": "2026-11-01",
            "sensitive": "must not be returned",
        },
        "project-1",
    )

    item = as_work_item(
        normalized,
        provider="plane",
        workspace_id="ws-1",
        container_id="project-1",
        base_url=None,
    )
    payload = item.to_dict()

    assert payload["id"] == "issue-1"
    assert payload["identifier"] == "17"
    assert payload["status"]["category"] == "started"
    assert payload["priority"]["level"] == "high"
    assert payload["assignees"][0]["displayName"] == "Ada"
    assert payload["source"]["provider"] == "plane"
    assert "raw" not in payload
    assert "additional_properties" not in repr(payload)
    assert "ephemeral-upstream-secret" not in repr(payload)


def test_jira_issue_mapping_preserves_native_status_and_priority() -> None:
    fields = SimpleNamespace(
        summary="Fix login",
        status=SimpleNamespace(id="3", name="In Progress", statusCategory=SimpleNamespace(name="In Progress")),
        priority=SimpleNamespace(name="Highest"),
        project=SimpleNamespace(key="APP", name="Application"),
        issuetype=SimpleNamespace(name="Bug"),
        assignee=SimpleNamespace(accountId="user-1", displayName="Ada"),
        reporter=None,
        fixVersions=[],
        components=[],
        labels=[],
    )
    issue = SimpleNamespace(key="APP-42", fields=fields)

    normalized = JiraProvider._issue_to_dict(issue)
    item = as_work_item(
        normalized,
        provider="jira",
        workspace_id="https://jira.example.test",
        container_id="APP",
        base_url="https://jira.example.test",
    )

    assert item.id == "APP-42"
    assert item.status and item.status.id == "3" and item.status.category == "started"
    assert item.priority and item.priority.level == "highest"
    assert item.url == "https://jira.example.test/browse/APP-42"


def test_planner_task_priority_and_completion_are_normalized() -> None:
    normalized = PlannerProvider._task_to_dict(
        {
            "id": "task-1",
            "title": "Ship feature",
            "priority": 1,
            "percentComplete": 100,
            "planId": "plan-1",
            "bucketId": "bucket-1",
            "completedDateTime": "2026-10-01T12:00:00Z",
        },
        "Done",
    )
    item = as_work_item(
        normalized,
        provider="planner",
        workspace_id="group-1",
        container_id="plan-1",
        base_url=None,
    )

    assert item.status and item.status.category == "completed"
    assert item.priority and item.priority.level == "highest"
    assert item.url == "https://tasks.office.com/plan-1/Home/Task/task-1"


@pytest.mark.asyncio
async def test_adapters_are_created_per_request_and_jira_needs_a_base_url() -> None:
    async with open_provider("plane", "one-time-secret") as first:
        async with open_provider("plane", "one-time-secret") as second:
            assert first is not second

    with pytest.raises(ValueError, match="required for Jira"):
        async with open_provider("jira", "api-token"):
            pass


@pytest.mark.asyncio
async def test_generated_list_work_items_routes_to_selected_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeAdapter:
        async def list_work_items(self, workspace, container, **kwargs):
            return [
                PlaneProvider("unused")._normalize_work_item(
                    {
                        "id": "issue-1",
                        "name": "Fix login",
                        "state": {"id": "state-1", "name": "In Progress", "group": "started"},
                    },
                    container,
                )
            ]

    @asynccontextmanager
    async def fake_open_provider(provider, credential, base_url):
        assert provider == "plane"
        assert credential == "ephemeral-upstream-secret"
        assert base_url is None
        yield FakeAdapter()

    monkeypatch.setenv("PM_GATEWAY_API_TOKEN", "gateway-secret")
    monkeypatch.setattr(provider_api, "open_provider", fake_open_provider)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/pm/v1/workspaces/ws-1/containers/project-1/work-items",
            headers=_headers(),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"][0]["title"] == "Fix login"
    assert payload["items"][0]["status"]["category"] == "started"
    assert "additional_properties" not in response.text
