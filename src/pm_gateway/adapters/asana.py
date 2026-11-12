"""Asana provider — implements the generic PMProvider interface.

Uses the Asana REST API (``https://app.asana.com/api/1.0``) with a personal
access token.
"""

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
    normalize_state_category,
)
from pm_gateway.security import provider_timeout_seconds

logger = logging.getLogger("pm_gateway.adapters.asana")

DEFAULT_BASE_URL = "https://app.asana.com/api/1.0"
API_PATH = "/api/1.0"

_PAGE_LIMIT = 100
_DEFAULT_MAX_ITEMS = 10_000

_TASK_FIELDS = (
    "gid", "name", "notes", "completed", "completed_at", "created_at", "modified_at",
    "due_on", "due_at", "start_on", "assignee.gid", "assignee.name", "created_by.gid",
    "created_by.name", "parent.gid", "memberships.project.gid", "memberships.project.name",
    "memberships.section.gid", "memberships.section.name", "tags.gid", "tags.name",
    "custom_fields.gid", "custom_fields.name", "custom_fields.display_value",
    "custom_fields.number_value", "custom_fields.text_value", "custom_fields.enum_value.gid",
    "custom_fields.enum_value.name", "custom_fields.date_value", "custom_fields.people_value.gid",
    "custom_fields.people_value.name", "custom_fields.multi_enum_values.gid",
    "custom_fields.multi_enum_values.name", "permalink_url", "resource_subtype", "num_subtasks",
)
_PROJECT_FIELDS = ("gid", "name", "permalink_url", "resource_subtype")
_SECTION_FIELDS = ("gid", "name")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _max_items() -> int:
    value = _env_int("PM_GATEWAY_ASANA_MAX_ITEMS", _DEFAULT_MAX_ITEMS)
    return value if value >= 0 else _DEFAULT_MAX_ITEMS


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _gid(value: Any) -> str | None:
    if isinstance(value, dict):
        raw = value.get("gid")
    elif isinstance(value, (str, int)):
        raw = value
    else:
        return None
    if raw is None:
        return None
    text = str(raw)
    return text or None


def _custom_field_value(field: dict[str, Any]) -> Any:
    for key in ("display_value", "text_value", "number_value", "date_value", "enum_value", "multi_enum_values", "people_value"):
        value = field.get(key)
        if value is None:
            continue
        if isinstance(value, dict):
            return value.get("name") or value.get("gid")
        if isinstance(value, list):
            names = [item.get("name") or item.get("gid") for item in value if isinstance(item, dict)]
            names = [name for name in names if name]
            if names:
                return ", ".join(str(name) for name in names)
            continue
        return value
    return None


class AsanaAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class AsanaProvider:
    provider_key = "asana"

    def __init__(self, credential: str, base_url: str | None = None, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._credential = credential
        self._base_url = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
        self._base_url = self._base_url.removesuffix(API_PATH)
        self._base_url = f"{self._base_url}{API_PATH}"
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    def _build_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self._base_url, timeout=provider_timeout_seconds(), follow_redirects=False, transport=self._transport)

    async def _ensure(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is None:
                self._client = self._build_client()
            return self._client

    async def invalidate(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.aclose()
            except Exception as exc:
                logger.debug("Ignored exception: %s", type(exc).__name__)

    def _sanitize(self, text: str) -> str:
        value = str(text or "")
        for secret in {self._credential, self._credential.strip()}:
            if secret:
                value = value.replace(secret, "[redacted]")
        return value[:500]

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._credential.strip()}", "Accept": "application/json"}

    async def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None) -> Any:
        client = await self._ensure()
        try:
            response = await client.request(method, path, params=params, json=json, headers=self._headers())
        except httpx.HTTPError as exc:
            raise AsanaAPIError("The Asana request failed.", status_code=getattr(exc, "status_code", None)) from exc
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            errors = _as_list(payload.get("errors")) if isinstance(payload, dict) else []
            first = _as_dict(errors[0]) if errors else {}
            message = first.get("message")
            raise AsanaAPIError(f"Asana API error: {self._sanitize(str(message))}" if message else f"Asana returned HTTP {response.status_code}.", status_code=response.status_code, code=str(first.get("code")) if first.get("code") else None)
        try:
            payload = response.json()
        except ValueError as exc:
            raise AsanaAPIError("Asana returned an invalid response.") from exc
        if not isinstance(payload, dict):
            raise AsanaAPIError("Asana returned an invalid response.")
        data = payload.get("data")
        return data if isinstance(data, dict) else payload

    async def _paginate(self, path: str, *, params: dict[str, Any] | None = None, cap: int | None = None) -> list[dict[str, Any]]:
        query = dict(params or {})
        query.setdefault("limit", _PAGE_LIMIT)
        limit = cap if cap is not None else _max_items()
        collected: list[dict[str, Any]] = []
        offset: str | None = None
        truncated = False
        while True:
            if offset:
                query["offset"] = offset
            payload = await self._request("GET", path, params=query)
            batch = [item for item in _as_list(payload.get("data") if isinstance(payload, dict) else payload) if isinstance(item, dict)]
            if not batch and isinstance(payload, list):
                batch = [item for item in payload if isinstance(item, dict)]
            collected.extend(batch)
            if limit and len(collected) >= limit:
                if len(collected) > limit:
                    collected = collected[:limit]
                truncated = True
                break
            next_page = _as_dict(payload.get("next_page")) if isinstance(payload, dict) else {}
            offset = next_page.get("offset")
            if not offset:
                break
        if truncated:
            logger.warning("Asana listing truncated at %s items", limit)
        return collected

    @classmethod
    def _task_to_dict(cls, task: dict[str, Any], project_id: str = "") -> dict[str, Any]:
        memberships = [m for m in _as_list(task.get("memberships")) if isinstance(m, dict)]
        primary = None
        for membership in memberships:
            if _gid(membership.get("project")) == str(project_id or ""):
                primary = membership
                break
        if primary is None and memberships:
            primary = memberships[0]
        primary_project = _as_dict((primary or {}).get("project"))
        section = _as_dict((primary or {}).get("section"))
        state_name = section.get("name")
        state_id = _gid(section)
        state_category = normalize_state_category(state_name)
        if state_category is None and task.get("completed"):
            state_category = "completed"
        assignee = _as_dict(task.get("assignee"))
        creator = _as_dict(task.get("created_by"))
        parent_id = _gid(task.get("parent"))
        labels = []
        for tag in _as_list(task.get("tags")):
            name = _as_dict(tag).get("name")
            if name:
                labels.append(str(name))
        custom_fields: dict[str, Any] = {}
        estimate = None
        for field in _as_list(task.get("custom_fields")):
            field = _as_dict(field)
            name = field.get("name")
            if not name:
                continue
            value = _custom_field_value(field)
            if value is None:
                continue
            if estimate is None:
                numeric = field.get("number_value")
                if isinstance(numeric, (int, float)) and not isinstance(numeric, bool):
                    estimate = float(numeric)
            custom_fields[str(name)] = value
        due_date = task.get("due_on") or task.get("due_at")
        start_date = task.get("start_on") or task.get("start_at")
        return NormalizedWorkItem(
            id=str(task.get("gid") or ""),
            identifier=str(task.get("gid")) if task.get("gid") is not None else None,
            title=task.get("name") or "",
            description=task.get("notes") or None,
            description_content_type="text/plain" if task.get("notes") else None,
            url=task.get("permalink_url"),
            state=state_name,
            state_id=state_id,
            state_category=state_category,
            estimate=estimate,
            start_date=format_date(start_date),
            due_date=format_date(due_date),
            completed_at=format_datetime(task.get("completed_at")),
            created_at=format_datetime(task.get("created_at")),
            updated_at=format_datetime(task.get("modified_at")),
            assignee=assignee.get("name"),
            assignee_id=_gid(assignee),
            creator=creator.get("name"),
            creator_id=_gid(creator),
            parent_id=parent_id,
            project_id=_gid(primary_project) or (str(project_id) if project_id else None),
            project_name=primary_project.get("name"),
            labels=labels,
            item_type="subtask" if parent_id else "task",
            custom_fields=custom_fields,
            memberships=[{"project_id": _gid(m.get("project")), "project_name": _as_dict(m.get("project")).get("name"), "section_id": _gid(m.get("section")), "section_name": _as_dict(m.get("section")).get("name")} for m in memberships],
            provider="asana",
            raw=dict(task),
        ).model_dump()

    @staticmethod
    def _normalize_project(raw: dict[str, Any]) -> dict[str, Any]:
        return {"id": str(raw.get("gid") or ""), "name": raw.get("name") or str(raw.get("gid") or ""), "identifier": None, "url": raw.get("permalink_url")}

    @staticmethod
    def _normalize_section(raw: dict[str, Any]) -> dict[str, Any]:
        name = raw.get("name") or str(raw.get("gid") or "")
        return {"id": str(raw.get("gid") or ""), "name": name, "group": name}

    async def validate_slug(self, slug: str) -> dict | None:
        try:
            projects = await self.list_projects(slug)
            return {"slug": slug, "valid": True, "projects_count": len(projects)}
        except Exception:
            return None

    async def list_projects(self, workspace_slug: str) -> list[dict]:
        workspace_id = str(workspace_slug or "").strip()
        if not workspace_id.isdigit():
            raise ValueError("The Asana workspace gid must be numeric.")
        raw = await self._paginate(f"/workspaces/{workspace_id}/projects", params={"opt_fields": ",".join(_PROJECT_FIELDS)})
        return [self._normalize_project(p) for p in raw if p.get("gid") is not None]

    async def list_work_items(self, workspace_slug: str, project_id: str, expand: str | None = None, board_id: str | None = None, sprint_ids: list[str] | None = None) -> list[dict]:
        if board_id or sprint_ids:
            raise ValueError("Asana does not support board or iteration filters.")
        raw = await self._paginate("/tasks", params={"project": str(project_id), "opt_fields": ",".join(_TASK_FIELDS)})
        return [self._task_to_dict(task, project_id) for task in raw]

    async def get_work_item(self, workspace_slug: str, project_id: str, work_item_id: str) -> dict:
        task = await self._request("GET", f"/tasks/{work_item_id}", params={"opt_fields": ",".join(_TASK_FIELDS)})
        if not (isinstance(task, dict) and task.get("gid")):
            raise AsanaAPIError("The requested Asana task was not found.", status_code=404)
        return self._task_to_dict(task, project_id)

    async def list_states(self, workspace_slug: str, project_id: str, board_id: str | None = None) -> list[dict]:
        raw = await self._paginate(f"/projects/{project_id}/sections", params={"opt_fields": ",".join(_SECTION_FIELDS)})
        return [self._normalize_section(s) for s in raw if s.get("gid") is not None or s.get("name")]

    async def update_work_item(self, workspace_slug: str, project_id: str, work_item_id: str, data: dict) -> dict:
        target = (data or {}).get("state")
        if not target:
            raise ValueError("A target Asana section is required.")
        states = await self.list_states(workspace_slug, project_id)
        needle = str(target).strip().casefold()
        match = next((s for s in states if needle in {s["id"].casefold(), s["name"].casefold()}), None)
        if match is None:
            raise ValueError(f"No Asana section matches '{target}' on project {project_id}.")
        await self._request("POST", f"/sections/{match['id']}/addTask", json={"data": {"task": str(work_item_id)}})
        return await self.get_work_item(workspace_slug, project_id, work_item_id)

    async def list_boards(self, workspace_slug: str, project_id: str) -> list[dict]:
        return []

    async def list_sprints(self, workspace_slug: str, project_id: str, board_id: str | None = None) -> list[dict]:
        return []

    async def get_board_scope(self, workspace_slug: str, project_id: str, board_id: str | None = None, sprint_ids: list[str] | None = None) -> dict:
        return {"board_id": None, "scope": "all", "sprint": None, "sprints": []}
