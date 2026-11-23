from __future__ import annotations

import asyncio

import httpx
import pytest

from pm_gateway.adapters.github import GitHubAPIError, GitHubProvider

CREDENTIAL = "ghp_secret"

REPO = {"id": 1, "name": "opennomos", "html_url": "https://github.com/octocat/opennomos"}
ISSUE = {
    "id": 101,
    "number": 42,
    "title": "Ship the GitHub adapter",
    "body": "Ships the integration.",
    "state": "open",
    "labels": [{"name": "backend"}],
    "assignee": {"login": "ada", "id": 9},
    "user": {"login": "bob", "id": 2},
    "html_url": "https://github.com/octocat/opennomos/issues/42",
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-02T00:00:00Z",
    "closed_at": None,
}


class FakeReposApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def repos_list_for_user(self, username, _headers=None):
        self.calls.append(("repos_list_for_user", {"username": username}))
        return [REPO]

    async def repos_list_for_org(self, org, _headers=None):
        self.calls.append(("repos_list_for_org", {"org": org}))
        return [REPO]

    async def repos_list_for_authenticated_user(self, _headers=None, per_page=None):
        self.calls.append(("repos_list_for_authenticated_user", {"per_page": per_page}))
        return [REPO]


class FakeIssuesApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def issues_list_for_repo(self, owner, repo, state=None, per_page=None, _headers=None):
        self.calls.append(("issues_list_for_repo", {"owner": owner, "repo": repo, "state": state}))
        return [ISSUE]

    async def issues_get(self, owner, repo, issue_number, _headers=None):
        self.calls.append(("issues_get", {"owner": owner, "repo": repo, "issue_number": issue_number}))
        return ISSUE

    async def issues_update(self, owner, repo, issue_number, issues_update_request=None, _headers=None):
        self.calls.append(("issues_update", {"owner": owner, "repo": repo, "issue_number": issue_number, "request": issues_update_request}))
        return ISSUE


def _provider() -> GitHubProvider:
    provider = GitHubProvider(CREDENTIAL)
    provider._repos = FakeReposApi()
    provider._issues = FakeIssuesApi()
    return provider


def _run(awaitable):
    return asyncio.run(awaitable)


def test_list_projects_normalizes_repos() -> None:
    provider = _provider()
    projects = _run(provider.list_projects("octocat"))

    assert projects == [
        {"id": "opennomos", "name": "opennomos", "identifier": None, "url": "https://github.com/octocat/opennomos"}
    ]


def test_list_work_items_normalizes_issues() -> None:
    provider = _provider()
    items = _run(provider.list_work_items("octocat", "opennomos"))

    assert [item["id"] for item in items] == ["101"]
    assert items[0]["identifier"] == "42"
    assert items[0]["title"] == "Ship the GitHub adapter"
    assert items[0]["state"] == "open"
    assert items[0]["state_category"] == "started"
    assert items[0]["labels"] == ["backend"]
    assert items[0]["assignee"] == "ada"
    assert items[0]["creator"] == "bob"
    assert items[0]["provider"] == "github"


def test_pull_requests_are_filtered_out() -> None:
    provider = _provider()
    provider._issues.issues_list_for_repo = _async_return([ {**ISSUE, "pull_request": {"url": "x"}} ])
    items = _run(provider.list_work_items("octocat", "opennomos"))
    assert items == []


def test_get_work_item_returns_issue() -> None:
    provider = _provider()
    item = _run(provider.get_work_item("octocat", "opennomos", "42"))

    assert item["id"] == "101"
    assert item["identifier"] == "42"


def test_list_states_returns_open_and_closed() -> None:
    provider = _provider()
    states = _run(provider.list_states("octocat", "opennomos"))

    assert states == [
        {"id": "open", "name": "Open", "group": "unstarted"},
        {"id": "closed", "name": "Closed", "group": "completed"},
    ]


def test_update_work_item_closes_issue() -> None:
    provider = _provider()
    item = _run(provider.update_work_item("octocat", "opennomos", "42", {"state": "closed"}))

    assert item["id"] == "101"
    assert any(call[0] == "issues_update" for call in provider._issues.calls)


def test_update_work_item_requires_recognized_state() -> None:
    provider = _provider()
    with pytest.raises(ValueError):
        _run(provider.update_work_item("octocat", "opennomos", "42", {"state": "nope"}))


def test_invalidate_clears_clients() -> None:
    provider = _provider()
    _run(provider.invalidate())
    assert provider._repos is None
    assert provider._issues is None


def _async_return(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner
