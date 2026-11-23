"""Live Plane compatibility checks.

These tests call a real Plane instance. They are skipped unless
``PLANE_LIVE=1`` is set and ``PLANE_API_TOKEN`` is available.
"""

from __future__ import annotations

import contextlib
import os

import pytest

from pm_gateway.adapters.plane import PlaneProvider
from pm_gateway.adapters.serialization import as_work_item

TOKEN = os.getenv("PLANE_API_TOKEN", "").strip()
BASE_URL = os.getenv("PLANE_BASE_URL", "").strip() or None
LIVE_ENABLED = os.getenv("PLANE_LIVE", "").strip().lower() in {"1", "true", "yes", "on"}
TEST_WORKSPACE_SLUG = os.getenv("PLANE_TEST_WORKSPACE_SLUG", "").strip()
TEST_PROJECT_ID = os.getenv("PLANE_TEST_PROJECT_ID", "").strip()

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (TOKEN and LIVE_ENABLED and TEST_WORKSPACE_SLUG),
        reason="live Plane checks need PLANE_LIVE=1, PLANE_API_TOKEN, and PLANE_TEST_WORKSPACE_SLUG",
    ),
]


@contextlib.asynccontextmanager
async def _provider(credential: str | None = None):
    provider = PlaneProvider(credential or TOKEN, BASE_URL)
    try:
        yield provider
    finally:
        await provider.invalidate()


async def _configured_project(provider: PlaneProvider) -> dict:
    if TEST_PROJECT_ID:
        return {"id": TEST_PROJECT_ID}
    projects = await provider.list_projects(TEST_WORKSPACE_SLUG)
    if not projects:
        pytest.skip("no Plane projects are visible to this token")
    for project in projects:
        items = await provider.list_work_items(TEST_WORKSPACE_SLUG, project["id"])
        if items:
            return {"id": project["id"]}
    return {"id": projects[0]["id"]}


async def test_token_identity(check) -> None:
    with check("auth / API token") as entry:
        async with _provider() as provider:
            projects = await provider.list_projects(TEST_WORKSPACE_SLUG)
        assert isinstance(projects, list)
        entry["detail"] = "passed"


async def test_projects_are_listable(check) -> None:
    with check("containers.list + workflow-states.list") as entry:
        async with _provider() as provider:
            projects = await provider.list_projects(TEST_WORKSPACE_SLUG)
            assert projects, "Plane returned no projects"
            project = await _configured_project(provider)
            states = await provider.list_states(TEST_WORKSPACE_SLUG, project["id"])
        entry["detail"] = "passed"


async def test_work_items_are_readable_and_normalized(check) -> None:
    with check("work-items.read + normalization") as entry:
        async with _provider() as provider:
            project = await _configured_project(provider)
            items = await provider.list_work_items(TEST_WORKSPACE_SLUG, project["id"])
            if not items:
                pytest.skip("the selected project has no active work items")
            for item in items:
                assert item["id"]
                assert item["provider"] == "plane"
                projected = as_work_item(
                    item, provider="plane", workspace_id=TEST_WORKSPACE_SLUG,
                    container_id=project["id"], base_url=BASE_URL,
                ).to_dict()
                assert projected["source"]["provider"] == "plane"
        entry["detail"] = "passed"


async def test_single_work_item_round_trips(check) -> None:
    with check("work-items.read (single item)") as entry:
        async with _provider() as provider:
            project = await _configured_project(provider)
            items = await provider.list_work_items(TEST_WORKSPACE_SLUG, project["id"])
            if not items:
                pytest.skip("the selected project has no active work items")
            fetched = await provider.get_work_item(
                TEST_WORKSPACE_SLUG, project["id"], items[0]["id"]
            )
            assert fetched["id"] == items[0]["id"]
        entry["detail"] = "passed"


async def test_unknown_project_maps_to_error(check) -> None:
    with check("error mapping (unknown project)") as entry:
        async with _provider() as provider:
            try:
                await provider.list_work_items(TEST_WORKSPACE_SLUG, "00000000-0000-0000-0000-000000000000")
            except Exception as exc:
                assert exc is not None
        entry["detail"] = "passed"


async def test_invalid_token_errors_are_sanitized(check) -> None:
    invalid = "pm-gateway-invalid-live-test-token"
    with check("auth failure handling") as entry:
        async with _provider() as provider:
            project = await _configured_project(provider)
        async with _provider(invalid) as bad:
            with pytest.raises(Exception):
                await bad.list_projects(TEST_WORKSPACE_SLUG)
        entry["detail"] = "passed"
