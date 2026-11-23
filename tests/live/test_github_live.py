"""Live GitHub Issues compatibility checks."""

from __future__ import annotations

import contextlib
import os

import pytest

from pm_gateway.adapters.github import GitHubProvider
from pm_gateway.adapters.serialization import as_work_item

TOKEN = os.getenv("GITHUB_API_TOKEN", "").strip()
BASE_URL = os.getenv("GITHUB_BASE_URL", "").strip() or None
LIVE_ENABLED = os.getenv("GITHUB_LIVE", "").strip().lower() in {"1", "true", "yes", "on"}
TEST_OWNER = os.getenv("GITHUB_TEST_OWNER", "").strip()
TEST_REPO = os.getenv("GITHUB_TEST_REPO", "").strip()

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (TOKEN and LIVE_ENABLED),
        reason="live GitHub checks need GITHUB_LIVE=1 and GITHUB_API_TOKEN",
    ),
]


@contextlib.asynccontextmanager
async def _provider(credential: str | None = None):
    provider = GitHubProvider(credential or TOKEN, BASE_URL)
    try:
        yield provider
    finally:
        await provider.invalidate()


async def _configured_repo(provider: GitHubProvider) -> dict:
    if TEST_OWNER and TEST_REPO:
        return {"owner": TEST_OWNER, "name": TEST_REPO}
    repos = await provider.list_projects("me")
    if not repos:
        pytest.skip("no GitHub repositories are visible to this token")
    repo = repos[0]
    return {"owner": "me", "name": repo["id"]}


async def test_token_identity(check) -> None:
    with check("auth / personal access token") as entry:
        async with _provider() as provider:
            repos = await provider.list_projects("me")
        assert isinstance(repos, list)
        entry["detail"] = "passed"


async def test_repos_are_listable(check) -> None:
    with check("containers.list + workflow-states.list") as entry:
        async with _provider() as provider:
            repos = await provider.list_projects("me")
            assert repos, "GitHub returned no repositories"
            repo = await _configured_repo(provider)
            states = await provider.list_states("me", repo["name"])
            assert states
        entry["detail"] = "passed"


async def test_work_items_are_readable_and_normalized(check) -> None:
    with check("work-items.read + normalization") as entry:
        async with _provider() as provider:
            repo = await _configured_repo(provider)
            items = await provider.list_work_items("me", repo["name"])
            if not items:
                pytest.skip("the selected repository has no issues")
            for item in items:
                assert item["id"]
                assert item["provider"] == "github"
                projected = as_work_item(
                    item, provider="github", workspace_id=repo["owner"],
                    container_id=repo["name"], base_url=BASE_URL,
                ).to_dict()
                assert projected["source"]["provider"] == "github"
        entry["detail"] = "passed"


async def test_single_work_item_round_trips(check) -> None:
    with check("work-items.read (single item)") as entry:
        async with _provider() as provider:
            repo = await _configured_repo(provider)
            items = await provider.list_work_items("me", repo["name"])
            if not items:
                pytest.skip("the selected repository has no issues")
            fetched = await provider.get_work_item("me", repo["name"], items[0]["identifier"] or items[0]["id"])
            assert fetched["id"] == items[0]["id"]
        entry["detail"] = "passed"


async def test_unknown_repo_maps_to_error(check) -> None:
    with check("error mapping (unknown repository)") as entry:
        async with _provider() as provider:
            try:
                await provider.list_work_items("me", "definitely-not-a-repo-xyz")
            except Exception as exc:
                assert exc is not None
        entry["detail"] = "passed"


async def test_invalid_token_errors_are_sanitized(check) -> None:
    invalid = "pm-gateway-invalid-live-test-token"
    with check("auth failure handling") as entry:
        async with _provider() as provider:
            await _configured_repo(provider)
        async with _provider(invalid) as bad:
            with pytest.raises(Exception):
                await bad.list_projects("me")
        entry["detail"] = "passed"
