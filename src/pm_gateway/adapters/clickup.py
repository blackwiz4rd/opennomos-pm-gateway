"""ClickUp provider — implements the generic PMProvider interface."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from pm_gateway.adapters.models import NormalizedWorkItem
from pm_gateway.adapters.normalization import (
    normalize_priority_value,
)
from pm_gateway.security import provider_timeout_seconds

logger = logging.getLogger("pm_gateway.adapters.clickup")

DEFAULT_BASE_URL = "https://api.clickup.com/api/v2"
API_PATH = "/api/v2"

_PAGE_LIMIT = 100
_DEFAULT_MAX_ITEMS = 10_000

_STATUS_TYPE_CATEGORY = {
    "open": "unstarted",
    "custom": "started",
    "done": "completed",
    "closed": "completed",
}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _max_items() -> int:
    value = _env_int("PM_GATEWAY_CLICKUP_MAX_ITEMS", _DEFAULT_MAX_ITEMS)
    return value if value >= 0 else _DEFAULT_MAX_ITEMS


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _identifier(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _epoch_to_iso(value: Any) -> str | None:
    if value in (None, "", "0"):
        return None
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return None
    from datetime import UTC, datetime

    return datetime.fromtimestamp(millis / 1000, tz=UTC).isoformat()


def _epoch_to_date(value: Any) -> str | None:
    iso = _epoch_to_iso(value)
    return iso.split("T", 1)[0] if iso else None


class ClickUpAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class ClickUpProvider:
    provider_key = "clickup"

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
        return {"Authorization": self._credential.strip(), "Accept": "application/json"}

    async def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None) -> Any:
        client = await self._ensure()
        try:
            response = await client.request(method, path, params=params, json=json, headers=self._headers())
        except httpx.HTTPError as exc:
            raise ClickUpAPIError("The ClickUp request failed.", status_code=getattr(exc, "status_code", None)) from exc
        if response.status_code >= 400:
            try:
                body = response.json()
            except ValueError:
                body = {}
            message = body.get("err") or body.get("error")
            raise ClickUpAPIError(f"ClickUp API error: {self._sanitize(str(message))}" if message else f"ClickUp returned HTTP {response.status_code}.", status_code=response.status_code, code=str(body.get("ECODE")) if body.get("ECODE") else None)
        try:
            return response.json()
        except ValueError as exc:
            raise ClickUpAPIError("ClickUp returned an invalid response.") from exc

    async def _paginate(self, path: str, *, params: dict[str, Any] | None = None, cap: int | None = None) -> list[dict[str, Any]]:
        query = dict(params or {})
        limit = cap if cap is not None else _max_items()
        collected: list[dict[str, Any]] = []
        page = 0
        truncated = False
        while True:
            query["page"] = page
            payload = await self._request("GET", path, params=query)
            if not isinstance(payload, dict):
                break
            # generic: find first list value
            batch = []
            for v in payload.values():
                if isinstance(v, list):
                    batch = [x for x in v if isinstance(x, dict)]
                    break
            collected.extend(batch)
            if limit and len(collected) >= limit:
                collected = collected[:limit]
                truncated = True
                break
            if len(batch) < _PAGE_LIMIT:
                break
            page += 1
        if truncated:
            logger.warning("ClickUp listing truncated at %s items", limit)
        return collected

    @classmethod
    def _task_to_dict(cls, task: dict[str, Any], list_id: str = "") -> dict[str, Any]:
        raw = _as_dict(task)
        status = _as_dict(raw.get("status"))
        assignees = _as_list(raw.get("assignees"))
        assignee = _as_dict(assignees[0]) if assignees else {}
        creator = _as_dict(raw.get("creator"))
        list_info = _as_dict(raw.get("list"))
        space_info = _as_dict(raw.get("space"))
        folder_info = _as_dict(raw.get("folder"))
        project_id = _identifier(list_info.get("id")) or (str(list_id) if list_id else None)
        labels = [str(t.get("name")) for t in _as_list(raw.get("tags")) if _as_dict(t).get("name")]
        due = _epoch_to_date(raw.get("due_date"))
        start = _epoch_to_date(raw.get("start_date"))
        created = _epoch_to_iso(raw.get("date_created"))
        updated = _epoch_to_iso(raw.get("date_updated"))
        completed = _epoch_to_iso(raw.get("date_closed")) or _epoch_to_iso(raw.get("date_done"))
        priority_info = _as_dict(raw.get("priority"))
        priority_name = priority_info.get("priority") or priority_info.get("name")
        return NormalizedWorkItem(
            id=str(raw.get("id") or ""),
            identifier=_identifier(raw.get("custom_id")) or _identifier(raw.get("id")),
            title=raw.get("name") or "",
            description=(raw.get("text_content") or raw.get("description")) or None,
            description_content_type="text/plain" if raw.get("text_content") else ("text/html" if raw.get("description") else None),
            url=raw.get("url"),
            state=status.get("status"),
            state_id=status.get("id") or status.get("status"),
            state_category=_STATUS_TYPE_CATEGORY.get(str(status.get("type", "")).strip().lower()) if status.get("type") else None,
            priority=priority_name,
            priority_value=normalize_priority_value(priority_name),
            estimate=raw.get("points"),
            start_date=start,
            due_date=due,
            completed_at=completed,
            created_at=created,
            updated_at=updated,
            assignee=assignee.get("username") or assignee.get("email"),
            assignee_id=_identifier(assignee.get("id")),
            creator=creator.get("username") or creator.get("email"),
            creator_id=_identifier(creator.get("id")),
            parent_id=_identifier(raw.get("parent")),
            project_id=project_id,
            project_name=list_info.get("name"),
            labels=labels,
            item_type="subtask" if raw.get("parent") else "task",
            custom_fields={str(_as_dict(f).get("name")): _as_dict(f).get("value") for f in _as_list(raw.get("custom_fields")) if _as_dict(f).get("name") is not None and _as_dict(f).get("value") is not None},
            team_id=_identifier(space_info.get("id")),
            team_name=space_info.get("name"),
            memberships=[{"project_id": project_id, "project_name": list_info.get("name"), "folder_id": _identifier(folder_info.get("id")), "folder_name": folder_info.get("name"), "space_id": _identifier(space_info.get("id")), "space_name": space_info.get("name")}] if project_id else [],
            provider="clickup",
            raw=raw,
        ).model_dump()

    @staticmethod
    def _normalize_list(raw: dict[str, Any]) -> dict[str, Any]:
        name = raw.get("name") or str(raw.get("id") or "")
        return {"id": str(raw.get("id") or ""), "name": name, "group": name}

    async def validate_slug(self, slug: str) -> dict | None:
        try:
            projects = await self.list_projects(slug)
            return {"slug": slug, "valid": True, "projects_count": len(projects)}
        except Exception:
            return None

    async def list_projects(self, workspace_slug: str) -> list[dict]:
        team_id = str(workspace_slug or "").strip()
        if not team_id:
            raise ValueError("The ClickUp workspace id must not be empty.")
        results = []
        try:
            spaces_resp = await self._request("GET", f"/team/{team_id}/space", params={})
        except Exception as exc:
            raise ClickUpAPIError(self._sanitize(str(exc)), status_code=getattr(exc, "status_code", None)) from exc
        spaces = spaces_resp.get("spaces", []) if isinstance(spaces_resp, dict) else []
        for space in spaces:
            space_id = space.get("id")
            if space_id is None:
                continue
            # folderless lists
            try:
                fl = await self._request("GET", f"/space/{space_id}/list", params={})
                for lst in fl.get("lists", []) if isinstance(fl, dict) else []:
                    results.append(self._normalize_list(lst))
            except Exception as exc:
                logger.debug("Ignored exception: %s", type(exc).__name__)
            # folders
            try:
                folders_resp = await self._request("GET", f"/space/{space_id}/folder", params={})
            except Exception:
                folders_resp = {}
            folders = folders_resp.get("folders", []) if isinstance(folders_resp, dict) else []
            for folder in folders:
                folder_id = folder.get("id")
                if folder_id is None:
                    continue
                try:
                    lists_resp = await self._request("GET", f"/folder/{folder_id}/list", params={})
                except Exception:
                    lists_resp = {}
                for lst in (lists_resp.get("lists", []) if isinstance(lists_resp, dict) else []):
                    results.append(self._normalize_list(lst))
        return results

    async def list_work_items(self, workspace_slug: str, project_id: str, expand: str | None = None, board_id: str | None = None, sprint_ids: list[str] | None = None) -> list[dict]:
        if board_id or sprint_ids:
            raise ValueError("ClickUp does not support board or iteration filters.")
        collected = []
        page = 0
        cap = _max_items()
        truncated = False
        while True:
            payload = await self._request("GET", f"/list/{project_id}/task", params={"page": page, "include_closed": "true"})
            batch = payload.get("tasks", []) if isinstance(payload, dict) else []
            batch = [x for x in batch if isinstance(x, dict)]
            collected.extend(batch)
            if cap and len(collected) >= cap:
                collected = collected[:cap]
                truncated = True
                break
            last_page = payload.get("last_page") if isinstance(payload, dict) else None
            if last_page is True or len(batch) == 0:
                break
            page += 1
        if truncated:
            logger.warning("ClickUp listing %s truncated at %s items", project_id, cap)
        return [self._task_to_dict(t, project_id) for t in collected]

    async def get_work_item(self, workspace_slug: str, project_id: str, work_item_id: str) -> dict:
        task = await self._request("GET", f"/task/{work_item_id}", params={})
        if not (isinstance(task, dict) and task.get("id")):
            raise ClickUpAPIError("The requested ClickUp task was not found.", status_code=404)
        return self._task_to_dict(task, project_id)

    async def list_states(self, workspace_slug: str, project_id: str, board_id: str | None = None) -> list[dict]:
        lst = await self._request("GET", f"/list/{project_id}", params={})
        out = []
        for status in _as_list(lst.get("statuses") if isinstance(lst, dict) else []):
            st = _as_dict(status)
            name = st.get("status") or st.get("name")
            if not name:
                continue
            out.append({"id": str(st.get("id") or name), "name": name, "group": st.get("type")})
        return out

    async def update_work_item(self, workspace_slug: str, project_id: str, work_item_id: str, data: dict) -> dict:
        target = (data or {}).get("state")
        if not target:
            raise ValueError("A target ClickUp status is required.")
        states = await self.list_states(workspace_slug, project_id)
        needle = str(target).strip().casefold()
        match = next((s for s in states if needle in {s["id"].casefold(), s["name"].casefold()}), None)
        if match is None:
            raise ValueError(f"No ClickUp status matches '{target}' on list {project_id}.")
        await self._request("PUT", f"/task/{work_item_id}", json={"status": match["name"]})
        return await self.get_work_item(workspace_slug, project_id, work_item_id)

    async def list_boards(self, workspace_slug: str, project_id: str) -> list[dict]:
        return []

    async def list_sprints(self, workspace_slug: str, project_id: str, board_id: str | None = None) -> list[dict]:
        return []

    async def get_board_scope(self, workspace_slug: str, project_id: str, board_id: str | None = None, sprint_ids: list[str] | None = None) -> dict:
        return {"board_id": None, "scope": "all", "sprint": None, "sprints": []}
