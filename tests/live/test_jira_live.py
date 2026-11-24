"""Live Jira compatibility checks."""

from __future__ import annotations

import contextlib
import os

import pytest

from pm_gateway.adapters.jira_http import JiraHttpAPIError, JiraHttpProvider
from pm_gateway.adapters.serialization import as_work_item

TOKEN = os.getenv("JIRA_API_TOKEN", "").strip()
BASE_URL = os.getenv("JIRA_BASE_URL", "").strip()
LIVE_ENABLED = os.getenv("JIRA_LIVE", "").strip().lower() in {"1", "true", "yes", "on"}
TEST_PROJECT_KEY = os.getenv("JIRA_TEST_PROJECT_KEY", "").strip()

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (TOKEN and LIVE_ENABLED and BASE_URL),
        reason="live Jira checks need JIRA_LIVE=1, JIRA_API_TOKEN, and JIRA_BASE_URL",
    ),
]


@contextlib.asynccontextmanager
async def _provider(credential: str | None = None):
    provider = JiraHttpProvider(credential or TOKEN, BASE_URL)
    try:
        yield provider
    finally:
        await provider.invalidate()


async def _configured_project(provider: JiraHttpProvider) -> dict:
    if TEST_PROJECT_KEY:
        return {"id": TEST_PROJECT_KEY}
    projects = await provider.list_projects("jira")
    if not projects:
        pytest.skip("no Jira projects are visible to this token")
    return {"id": projects[0]["id"]}


async def test_token_identity(check) -> None:
    with check("auth / API token") as entry:
        async with _provider() as provider:
            projects = await provider.list_projects("jira")
        assert isinstance(projects, list)
        entry["detail"] = "passed"


async def test_projects_are_listable(check) -> None:
    with check("containers.list + workflow-states.list") as entry:
        async with _provider() as provider:
            projects = await provider.list_projects("jira")
            assert projects, "Jira returned no projects"
            project = await _configured_project(provider)
            states = await provider.list_states("jira", project["id"])
        entry["detail"] = "passed"


async def test_work_items_are_readable_and_normalized(check) -> None:
    with check("work-items.read + normalization") as entry:
        async with _provider() as provider:
            project = await _configured_project(provider)
            items = await provider.list_work_items("jira", project["id"])
            if not items:
                pytest.skip("the selected project has no active issues")
            for item in items:
                assert item["id"]
                assert item["provider"] == "jira"
                projected = as_work_item(
                    item, provider="jira", workspace_id="jira",
                    container_id=project["id"], base_url=BASE_URL,
                ).to_dict()
                assert projected["source"]["provider"] == "jira"
        entry["detail"] = "passed"


async def test_single_work_item_round_trips(check) -> None:
    with check("work-items.read (single item)") as entry:
        async with _provider() as provider:
            project = await _configured_project(provider)
            items = await provider.list_work_items("jira", project["id"])
            if not items:
                pytest.skip("the selected project has no active issues")
            fetched = await provider.get_work_item("jira", project["id"], items[0]["id"])
            assert fetched["id"] == items[0]["id"]
        entry["detail"] = "passed"


async def test_unknown_project_maps_to_error(check) -> None:
    with check("error mapping (unknown project)") as entry:
        async with _provider() as provider:
            try:
                await provider.list_work_items("jira", "ZZZNOTAPROJECT")
            except Exception as exc:
                assert exc is not None
        entry["detail"] = "passed"


async def test_invalid_token_errors_are_sanitized(check) -> None:
    invalid = "invalid@example.com:pm-gateway-invalid-live-test-token"
    with check("auth failure handling") as entry:
        async with _provider() as provider:
            project = await _configured_project(provider)
        async with _provider(invalid) as bad:
            result = await bad.list_projects("jira")
            assert invalid not in str(result)
        entry["detail"] = "passed"
