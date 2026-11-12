"""Live Asana compatibility checks.

These tests call the real Asana REST API. They are skipped unless
``ASANA_LIVE=1`` is set and ``ASANA_API_TOKEN`` is available in the environment
or the repository's ``.env`` file (loaded by ``conftest.py``; the value is never
printed).

Environment:
    ASANA_LIVE=1                 enable these checks explicitly
    ASANA_API_TOKEN              personal access token (required to run)
    ASANA_TEST_WORKSPACE_ID      workspace to inspect (default: discovered)
    ASANA_TEST_PROJECT_ID        project to inspect (default: first visible project)
    ASANA_LIVE_WRITE_PROJECT_ID  disposable project for the create/move/delete
                                 lifecycle; write checks are skipped without it
"""

from __future__ import annotations

import contextlib
import os
import uuid

import pytest

from pm_gateway.adapters.asana import AsanaAPIError, AsanaProvider
from pm_gateway.adapters.serialization import as_work_item

TOKEN = os.getenv("ASANA_API_TOKEN", "").strip()
LIVE_ENABLED = os.getenv("ASANA_LIVE", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
TEST_WORKSPACE_ID = os.getenv("ASANA_TEST_WORKSPACE_ID", "").strip()
TEST_PROJECT_ID = os.getenv("ASANA_TEST_PROJECT_ID", "").strip()
WRITE_PROJECT_ID = os.getenv("ASANA_LIVE_WRITE_PROJECT_ID", "").strip()

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (TOKEN and LIVE_ENABLED),
        reason=(
            "live Asana checks need ASANA_LIVE=1 and ASANA_API_TOKEN "
            "(environment or .env)"
        ),
    ),
]

_CREATE_TASK = """
mutation CreateTask($project: ID!, $name: String!) {
  create_task(input: {project: $project, name: $name}) {
    task { gid }
  }
}
"""


@contextlib.asynccontextmanager
async def _provider(credential: str | None = None):
    provider = AsanaProvider(credential or TOKEN)
    try:
        yield provider
    finally:
        await provider.invalidate()


async def _configured_project(provider: AsanaProvider) -> dict:
    """Return the project under test, preferring one that already has tasks."""
    if TEST_PROJECT_ID:
        return {"id": TEST_PROJECT_ID, "name": None, "workspace_id": TEST_WORKSPACE_ID}
    workspaces = await provider._paginate(
        "/workspaces", params={"opt_fields": "gid,name"}
    )
    if not workspaces:
        pytest.skip("no Asana workspaces are visible to this token")
    workspace_id = TEST_WORKSPACE_ID or str(workspaces[0].get("gid") or "")
    if not workspace_id:
        pytest.skip("could not determine an Asana workspace gid")
    projects = await provider.list_projects(workspace_id)
    if not projects:
        pytest.skip("the selected workspace has no visible projects")
    for project in projects:
        tasks = await provider.list_work_items(workspace_id, project["id"])
        if tasks:
            return {"id": project["id"], "name": project["name"], "workspace_id": workspace_id}
    first = projects[0]
    return {"id": first["id"], "name": first["name"], "workspace_id": workspace_id}


def _workspace_id(project: dict) -> str:
    value = TEST_WORKSPACE_ID or str(project.get("workspace_id") or "")
    if not value:
        pytest.skip("could not determine an Asana workspace gid")
    return value


async def test_token_identity(check) -> None:
    with check("auth / personal access token") as entry:
        async with _provider() as provider:
            me = await provider._request(
                "GET", "/users/me", params={"opt_fields": "gid,name"}
            )
        assert me.get("gid"), "Asana did not return an identity for the token"
        entry["detail"] = f"user {me.get('name') or me.get('gid')}"


async def test_workspaces_are_listable(check) -> None:
    with check("workspaces.list") as entry:
        async with _provider() as provider:
            workspaces = await provider._paginate(
                "/workspaces", params={"opt_fields": "gid,name"}
            )
        assert workspaces, "Asana returned no workspaces for this token"
        entry["detail"] = f"{len(workspaces)} workspace(s)"


async def test_containers_and_sections_resolve(check) -> None:
    with check("containers.list + workflow-states.list") as entry:
        async with _provider() as provider:
            project = await _configured_project(provider)
            workspace_id = _workspace_id(project)
            containers = await provider.list_projects(workspace_id)
            assert containers, "workspace returned no projects"
            assert any(container["id"] == project["id"] for container in containers)
            states = await provider.list_states(workspace_id, project["id"])
            assert states, "project returned no sections"
            assert all(state["id"] for state in states)
        entry["detail"] = (
            f"{len(containers)} project(s), {len(states)} section(s) on "
            f"project {project['id']}"
        )


async def test_work_items_are_readable_and_normalized(check) -> None:
    with check("work-items.read + normalization") as entry:
        async with _provider() as provider:
            project = await _configured_project(provider)
            workspace_id = _workspace_id(project)
            items = await provider.list_work_items(workspace_id, project["id"])
            if not items:
                pytest.skip("the selected project has no active tasks")
            for item in items:
                assert item["id"]
                assert item["provider"] == "asana"
                assert item["project_id"]
                projected = as_work_item(
                    item,
                    provider="asana",
                    workspace_id=workspace_id,
                    container_id=project["id"],
                    base_url=None,
                ).to_dict()
                assert projected["source"]["provider"] == "asana"
                assert projected["source"]["resourceType"] == "task"
                assert projected["title"] is not None
        entry["detail"] = f"{len(items)} task(s) on project {project['id']}"


async def test_single_work_item_round_trips(check) -> None:
    with check("work-items.read (single task)") as entry:
        async with _provider() as provider:
            project = await _configured_project(provider)
            workspace_id = _workspace_id(project)
            items = await provider.list_work_items(workspace_id, project["id"])
            if not items:
                pytest.skip("the selected project has no active tasks")
            item = items[0]
            fetched = await provider.get_work_item(
                workspace_id, project["id"], item["id"]
            )
            assert fetched["id"] == item["id"]
            assert fetched["project_id"] == item["project_id"]
        entry["detail"] = f"task {item['id']}"


async def test_offset_pagination_is_supported(check) -> None:
    with check("work-items pagination (offset)") as entry:
        async with _provider() as provider:
            project = await _configured_project(provider)
            workspace_id = _workspace_id(project)
            items = await provider.list_work_items(workspace_id, project["id"])
            if len(items) < 2:
                pytest.skip("the selected project needs at least two tasks")
            first = await provider._request(
                "GET",
                "/tasks",
                params={
                    "project": project["id"],
                    "limit": 1,
                    "opt_fields": "gid",
                },
            )
            page = first.get("next_page") or {}
            offset = page.get("offset")
            if not offset:
                pytest.skip("Asana returned no offset for the first page")
            following = await provider._request(
                "GET",
                "/tasks",
                params={
                    "project": project["id"],
                    "limit": 1,
                    "offset": offset,
                    "opt_fields": "gid",
                },
            )
            first_ids = {str(item.get("gid")) for item in first.get("data") or []}
            next_ids = {str(item.get("gid")) for item in following.get("data") or []}
            assert next_ids
            assert not (first_ids & next_ids)
        entry["detail"] = f"offset pagination on project {project['id']}"


async def test_unknown_project_maps_to_not_found(check) -> None:
    with check("error mapping (unknown project)") as entry:
        async with _provider() as provider:
            with pytest.raises(AsanaAPIError) as excinfo:
                await provider.list_states(TEST_WORKSPACE_ID, "9999999999999")
        assert excinfo.value.status_code in {400, 404}
        entry["detail"] = f"AsanaAPIError -> {excinfo.value.status_code}"


async def test_invalid_token_errors_are_sanitized(check) -> None:
    invalid = "pm-gateway-invalid-live-test-token"
    with check("auth failure handling") as entry:
        async with _provider() as provider:
            project = await _configured_project(provider)
        async with _provider(invalid) as bad:
            with pytest.raises(AsanaAPIError) as excinfo:
                await bad.list_projects(_workspace_id(project))
        message = str(excinfo.value)
        assert invalid not in message
        entry["detail"] = f"sanitized error (status {excinfo.value.status_code})"


async def test_create_move_delete_lifecycle(check) -> None:
    if not WRITE_PROJECT_ID:
        pytest.skip("ASANA_LIVE_WRITE_PROJECT_ID is not configured")
    with check("work-items.transition (create/move/delete)") as entry:
        async with _provider() as provider:
            states = await provider.list_states(TEST_WORKSPACE_ID, WRITE_PROJECT_ID)
            if len(states) < 2:
                pytest.skip("the write project needs at least two sections")
            created = await provider._request(
                "POST",
                "/tasks",
                json={
                    "data": {
                        "name": f"pm-gateway live check {uuid.uuid4().hex[:8]}",
                        "projects": [WRITE_PROJECT_ID],
                    }
                },
            )
            task_id = str(created.get("gid") or "")
            assert task_id
            try:
                fetched = await provider.get_work_item(
                    TEST_WORKSPACE_ID, WRITE_PROJECT_ID, task_id
                )
                assert fetched["id"] == task_id

                moved = await provider.update_work_item(
                    TEST_WORKSPACE_ID,
                    WRITE_PROJECT_ID,
                    task_id,
                    {"state": states[1]["id"]},
                )
                assert moved["state_id"] == states[1]["id"]

                moved_back = await provider.update_work_item(
                    TEST_WORKSPACE_ID,
                    WRITE_PROJECT_ID,
                    task_id,
                    {"state": states[0]["name"]},
                )
                assert moved_back["state_id"] == states[0]["id"]
            finally:
                await provider._request("DELETE", f"/tasks/{task_id}")
        entry["detail"] = (
            f"task {task_id} moved {states[0]['id']} -> {states[1]['id']} -> "
            f"{states[0]['id']} and deleted"
        )


def test_contract_gaps_are_reported(check) -> None:
    """Record resources Asana has but the standard contract does not expose."""
    gaps = {
        "comments.read": "Asana stories exist; the gateway contract does not expose comments for any provider yet",
        "attachments.read": "Asana attachments exist; attachments are not implemented for any provider yet",
        "custom-fields.discovery": "Asana custom fields are read as task values; the definition endpoint is not exposed by the contract",
        "milestones.list": "Asana milestones are a task resource_subtype, not a separate resource",
        "iterations.list": "no native Asana iteration/sprint resource",
        "boards.list": "Asana projects are the containers; there is no nested board level",
        "work-items.update": "only workflow transitions are exposed; arbitrary field updates are not",
        "work-items.multi-container": "multi-project memberships are preserved in extensions, but the contract exposes a single container membership",
    }
    for capability, reason in gaps.items():
        with check(capability) as entry:
            entry["status"] = "N/A"
            entry["detail"] = reason
