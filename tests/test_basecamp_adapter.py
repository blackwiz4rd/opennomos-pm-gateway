from __future__ import annotations

import asyncio

import httpx
import pytest

from pm_gateway.adapters.basecamp import BasecampAPIError, BasecampProvider

CREDENTIAL = "123:token"

PROJECT = {"id": 1, "name": "Project", "app_url": "https://basecamp.com/p/1", "dock": [{"name": "todoset", "enabled": True, "id": 10}]}
TODOLIST = {"id": 5, "name": "To Do", "title": "To Do"}
TODO = {
    "id": 7,
    "title": "Ship the Basecamp adapter",
    "description": "Ships the integration.",
    "completed": False,
    "bucket": {"id": 1, "name": "Project"},
    "assignees": [{"id": 9, "name": "Ada"}],
    "creator": {"id": 2, "name": "Bob"},
    "created_at": "2024-01-01T00:00:00Z",
    "updated_at": "2024-01-02T00:00:00Z",
    "app_url": "https://basecamp.com/todos/7",
}


class FakeProjectsApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def list_projects(self, account_id, _headers=None):
        self.calls.append(("list_projects", {"account_id": account_id}))
        return [PROJECT]

    async def get_project(self, account_id, project_id, _headers=None):
        self.calls.append(("get_project", {"account_id": account_id, "project_id": project_id}))
        return PROJECT


class FakeTodosApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.completed = False

    async def list_todolists(self, account_id, todoset_id, _headers=None):
        self.calls.append(("list_todolists", {"account_id": account_id, "todoset_id": todoset_id}))
        return [TODOLIST]

    async def list_todos(self, account_id, todolist_id, _headers=None):
        self.calls.append(("list_todos", {"account_id": account_id, "todolist_id": todolist_id}))
        return [TODO]

    async def get_todo(self, account_id, todo_id, _headers=None):
        self.calls.append(("get_todo", {"account_id": account_id, "todo_id": todo_id}))
        return TODO

    async def complete_todo(self, account_id, todo_id, _headers=None):
        self.calls.append(("complete_todo", {"account_id": account_id, "todo_id": todo_id}))
        self.completed = True

    async def uncomplete_todo(self, account_id, todo_id, _headers=None):
        self.calls.append(("uncomplete_todo", {"account_id": account_id, "todo_id": todo_id}))
        self.completed = False


def _provider() -> BasecampProvider:
    provider = BasecampProvider(CREDENTIAL)
    provider._projects = FakeProjectsApi()
    provider._todos = FakeTodosApi()
    return provider


def _run(awaitable):
    return asyncio.run(awaitable)


def test_credential_must_use_account_token_format() -> None:
    with pytest.raises(ValueError):
        BasecampProvider("only-token")


def test_list_projects_normalizes_projects() -> None:
    provider = _provider()
    projects = _run(provider.list_projects("123"))

    assert projects == [
        {"id": "1", "name": "Project", "identifier": None, "url": "https://basecamp.com/p/1"}
    ]


def test_list_work_items_normalizes_todos() -> None:
    provider = _provider()
    items = _run(provider.list_work_items("123", "1"))

    assert [item["id"] for item in items] == ["7"]
    assert items[0]["title"] == "Ship the Basecamp adapter"
    assert items[0]["state"] == "To Do"
    assert items[0]["state_id"] == "5"
    assert items[0]["assignee"] == "Ada"
    assert items[0]["creator"] == "Bob"
    assert items[0]["provider"] == "basecamp"


def test_get_work_item_returns_todo() -> None:
    provider = _provider()
    item = _run(provider.get_work_item("123", "1", "7"))

    assert item["id"] == "7"
    assert item["title"] == "Ship the Basecamp adapter"


def test_list_states_normalizes_todolists() -> None:
    provider = _provider()
    states = _run(provider.list_states("123", "1"))

    assert states == [{"id": "5", "name": "To Do", "group": "To Do"}]


def test_update_work_item_completes_todo() -> None:
    provider = _provider()
    # Add a "Done" list name so the match resolves to a completion action.
    provider._todos = FakeTodosApi()
    original_list = provider._todos.list_todolists
    async def patched_list_todolists(account_id, todoset_id, _headers=None):
        return [{"id": 6, "name": "Done"}]
    provider._todos.list_todolists = patched_list_todolists

    item = _run(provider.update_work_item("123", "1", "7", {"state": "Done"}))

    assert item["id"] == "7"
    assert any(call[0] == "complete_todo" for call in provider._todos.calls)


def test_update_work_item_requires_target() -> None:
    provider = _provider()
    with pytest.raises(ValueError):
        _run(provider.update_work_item("123", "1", "7", {}))


def test_update_work_item_unknown_target_raises() -> None:
    provider = _provider()
    with pytest.raises(ValueError):
        _run(provider.update_work_item("123", "1", "7", {"state": "Nope"}))


def test_invalidate_clears_clients() -> None:
    provider = _provider()
    _run(provider.invalidate())
    assert provider._projects is None
    assert provider._todos is None
