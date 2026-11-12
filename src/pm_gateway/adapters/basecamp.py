"""Basecamp provider — implements the generic PMProvider interface."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from pm_gateway.adapters.models import NormalizedWorkItem
from pm_gateway.adapters.normalization import (
    format_date,
    format_datetime,
)
from pm_gateway.generated.providers.basecamp.api.projects_api import ProjectsApi
from pm_gateway.generated.providers.basecamp.api.todos_api import TodosApi
from pm_gateway.generated.providers.basecamp.api_client import ApiClient
from pm_gateway.generated.providers.basecamp.configuration import Configuration

logger = logging.getLogger("pm_gateway.adapters.basecamp")

DEFAULT_BASE_URL = "https://3.basecampapi.com"
API_PATH = ""


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _as_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return dict(value)
    return {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _identifier(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


class BasecampAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class BasecampProvider:
    provider_key = "basecamp"

    def __init__(self, credential: str, base_url: str | None = None, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._credential = credential
        self._account_id, self._token = self._parse_credential(credential)
        base = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
        self._base_url = base
        self._transport = transport
        self._client: ApiClient | None = None
        self._projects: ProjectsApi | None = None
        self._todos: TodosApi | None = None
        self._lock = asyncio.Lock()

    @staticmethod
    def _parse_credential(credential: str) -> tuple[str, str]:
        key, separator, token = str(credential or "").partition(":")
        key, token = key.strip(), token.strip()
        if not separator or not key or not token:
            raise ValueError("Basecamp credential must be in format: account_id:token")
        return key, token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}

    async def _ensure(self) -> tuple[ProjectsApi, TodosApi]:
        if self._projects is not None and self._todos is not None:
            return self._projects, self._todos
        async with self._lock:
            if self._projects is None or self._todos is None:
                config = Configuration()
                config.host = self._base_url
                client = ApiClient(config)
                if self._transport is not None:
                    client.rest_client.pool_manager = httpx.AsyncClient(transport=self._transport)
                self._client = client
                self._projects = ProjectsApi(client)
                self._todos = TodosApi(client)
            return self._projects, self._todos

    async def invalidate(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
        self._client = None
        self._projects = None
        self._todos = None

    def _sanitize(self, text: str) -> str:
        value = str(text or "")
        for secret in {self._credential, self._token}:
            if secret:
                value = value.replace(secret, "[redacted]")
        return value[:500]

    @classmethod
    def _todo_to_dict(cls, todo: Any, project_id: str = "", todolist_id: str | None = None) -> dict[str, Any]:
        raw = _as_dict(todo)
        completed = bool(raw.get("completed"))
        state_category = "completed" if completed else None
        parent = _as_dict(raw.get("parent"))
        bucket = _as_dict(raw.get("bucket"))
        assignees = _as_list(raw.get("assignees"))
        assignee = assignees[0] if assignees else {}
        assignee = _as_dict(assignee)
        creator = _as_dict(raw.get("creator"))
        completion = _as_dict(raw.get("completion"))
        completed_at = None
        if completion:
            completed_at = format_datetime(completion.get("created_at"))
        return NormalizedWorkItem(
            id=str(raw.get("id") or ""),
            identifier=_identifier(raw.get("id")),
            title=raw.get("title") or raw.get("content") or "",
            description=raw.get("description") or raw.get("content") or None,
            description_content_type="text/html" if raw.get("description") else None,
            url=raw.get("app_url") or raw.get("url"),
            state=None,
            state_id=todolist_id,
            state_category=state_category,
            start_date=format_date(raw.get("starts_on")),
            due_date=format_date(raw.get("due_on")),
            completed_at=completed_at,
            created_at=format_datetime(raw.get("created_at")),
            updated_at=format_datetime(raw.get("updated_at")),
            assignee=assignee.get("name"),
            assignee_id=_identifier(assignee.get("id")),
            creator=creator.get("name"),
            creator_id=_identifier(creator.get("id")),
            parent_id=_identifier(parent.get("id")),
            project_id=_identifier(bucket.get("id")) or (str(project_id) if project_id else None),
            project_name=bucket.get("name"),
            labels=[],
            item_type="todo",
            custom_fields={},
            memberships=[],
            provider="basecamp",
            raw=raw,
        ).model_dump()

    @staticmethod
    def _normalize_project(raw: dict[str, Any]) -> dict[str, Any]:
        return {"id": str(raw.get("id") or ""), "name": raw.get("name") or str(raw.get("id") or ""), "identifier": None, "url": raw.get("app_url") or raw.get("url")}

    @staticmethod
    def _normalize_todolist(raw: dict[str, Any]) -> dict[str, Any]:
        name = raw.get("name") or raw.get("title") or str(raw.get("id") or "")
        return {"id": str(raw.get("id") or ""), "name": name, "group": name}

    async def validate_slug(self, slug: str) -> dict | None:
        try:
            projects = await self.list_projects(slug)
            return {"slug": slug, "valid": True, "projects_count": len(projects)}
        except Exception:
            return None

    async def _find_todoset_id(self, account_id: str, project_id: str) -> int | None:
        projects, _ = await self._ensure()
        try:
            project = await projects.get_project(account_id=account_id, project_id=int(project_id), _headers=self._headers())
        except Exception:
            # some endpoints use project id as int; fallback string not supported
            project = None
        raw = _as_dict(project)
        for item in _as_list(raw.get("dock")):
            item = _as_dict(item)
            if item.get("name") == "todoset" and item.get("enabled"):
                try:
                    return int(item.get("id"))
                except (TypeError, ValueError):
                    return item.get("id")
        return None

    async def list_projects(self, workspace_slug: str) -> list[dict]:
        account_id = str(workspace_slug or self._account_id).strip() or self._account_id
        projects_api, _ = await self._ensure()
        try:
            raw = await projects_api.list_projects(account_id=account_id, _headers=self._headers())
        except Exception as exc:
            raise BasecampAPIError(self._sanitize(str(exc)), status_code=getattr(exc, "status", None)) from exc
        return [self._normalize_project(_as_dict(p)) for p in (raw or []) if _as_dict(p).get("id") is not None]

    async def list_work_items(self, workspace_slug: str, project_id: str, expand: str | None = None, board_id: str | None = None, sprint_ids: list[str] | None = None) -> list[dict]:
        account_id = str(workspace_slug or self._account_id).strip() or self._account_id
        projects_api, todos_api = await self._ensure()
        todoset_id = await self._find_todoset_id(account_id, project_id)
        if todoset_id is None:
            return []
        try:
            todolists = await todos_api.list_todolists(account_id=account_id, todoset_id=todoset_id, _headers=self._headers())
        except Exception as exc:
            raise BasecampAPIError(self._sanitize(str(exc)), status_code=getattr(exc, "status", None)) from exc
        items = []
        for tl in (todolists or []):
            tl_raw = _as_dict(tl)
            todolist_id = tl_raw.get("id")
            try:
                todos = await todos_api.list_todos(account_id=account_id, todolist_id=int(todolist_id), _headers=self._headers())
            except Exception:
                todos = []
            for todo in (todos or []):
                normalized = self._todo_to_dict(todo, project_id, str(todolist_id) if todolist_id is not None else None)
                normalized["state"] = tl_raw.get("name") or tl_raw.get("title")
                items.append(normalized)
        return items

    async def get_work_item(self, workspace_slug: str, project_id: str, work_item_id: str) -> dict:
        account_id = str(workspace_slug or self._account_id).strip() or self._account_id
        _, todos_api = await self._ensure()
        try:
            todo = await todos_api.get_todo(account_id=account_id, todo_id=int(work_item_id), _headers=self._headers())
        except Exception as exc:
            raise BasecampAPIError(self._sanitize(str(exc)), status_code=getattr(exc, "status", None)) from exc
        raw = _as_dict(todo)
        if not raw.get("id"):
            raise BasecampAPIError("The requested Basecamp todo was not found.", status_code=404)
        return self._todo_to_dict(todo, project_id, None)

    async def list_states(self, workspace_slug: str, project_id: str, board_id: str | None = None) -> list[dict]:
        account_id = str(workspace_slug or self._account_id).strip() or self._account_id
        todoset_id = await self._find_todoset_id(account_id, project_id)
        if todoset_id is None:
            return []
        _, todos_api = await self._ensure()
        try:
            raw = await todos_api.list_todolists(account_id=account_id, todoset_id=todoset_id, _headers=self._headers())
        except Exception as exc:
            raise BasecampAPIError(self._sanitize(str(exc)), status_code=getattr(exc, "status", None)) from exc
        return [self._normalize_todolist(_as_dict(x)) for x in (raw or []) if _as_dict(x).get("id") is not None or _as_dict(x).get("name")]

    async def update_work_item(self, workspace_slug: str, project_id: str, work_item_id: str, data: dict) -> dict:
        target = (data or {}).get("state")
        if not target:
            raise ValueError("A target Basecamp todolist is required.")
        states = await self.list_states(workspace_slug, project_id)
        needle = str(target).strip().casefold()
        match = next((s for s in states if needle in {s["id"].casefold(), s["name"].casefold()}), None)
        if match is None:
            raise ValueError(f"No Basecamp todolist matches '{target}' on project {project_id}.")
        # Basecamp has no move-todo endpoint; completion is the supported state change.
        lowered = str(match["name"]).casefold()
        account_id = str(workspace_slug or self._account_id).strip() or self._account_id
        _, todos_api = await self._ensure()
        try:
            if "done" in lowered or "complete" in lowered:
                await todos_api.complete_todo(account_id=account_id, todo_id=int(work_item_id), _headers=self._headers())
            else:
                await todos_api.uncomplete_todo(account_id=account_id, todo_id=int(work_item_id), _headers=self._headers())
        except Exception as exc:
            raise BasecampAPIError(self._sanitize(str(exc)), status_code=getattr(exc, "status", None)) from exc
        return await self.get_work_item(workspace_slug, project_id, work_item_id)

    async def list_boards(self, workspace_slug: str, project_id: str) -> list[dict]:
        return []

    async def list_sprints(self, workspace_slug: str, project_id: str, board_id: str | None = None) -> list[dict]:
        return []

    async def get_board_scope(self, workspace_slug: str, project_id: str, board_id: str | None = None, sprint_ids: list[str] | None = None) -> dict:
        return {"board_id": None, "scope": "all", "sprint": None, "sprints": []}
