"""Celoxis provider."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx

from pm_gateway.adapters.models import NormalizedWorkItem
from pm_gateway.adapters.normalization import (
    format_date,
    normalize_priority_value,
    normalize_state_category,
)
from pm_gateway.security import provider_timeout_seconds

logger = logging.getLogger("pm_gateway.adapters.celoxis")

DEFAULT_BASE_URL = "https://app.celoxis.com/psa/api/v2"
API_PATH = "/psa/api/v2"

_PAGE_LIMIT = 250
_DEFAULT_MAX_ITEMS = 10_000


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _max_items() -> int:
    value = _env_int("PM_GATEWAY_CELOXIS_MAX_ITEMS", _DEFAULT_MAX_ITEMS)
    return value if value >= 0 else _DEFAULT_MAX_ITEMS


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _identifier(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _normalize_base_url(base_url: str | None) -> str:
    base = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
    if base.endswith("/psa/api/v2"):
        return base
    if base.endswith("/api/v2"):
        return f"{base[: -len('/api/v2')]}/psa/api/v2"
    if base.endswith("/psa"):
        return f"{base}/api/v2"
    return f"{base}/psa/api/v2"


class CeloxisAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class CeloxisProvider:
    provider_key = "celoxis"

    def __init__(self, credential: str, base_url: str | None = None, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._credential = credential
        self._base_url = _normalize_base_url(base_url)
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
        return {"Authorization": f"Bearer {self._credential.strip()}", "Accept": "application/json", "Content-Type": "application/json"}

    async def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: Any = None) -> Any:
        client = await self._ensure()
        try:
            response = await client.request(method, path, params=params, json=json, headers=self._headers())
        except httpx.HTTPError as exc:
            raise CeloxisAPIError("The Celoxis request failed.", status_code=getattr(exc, "status_code", None)) from exc
        if response.status_code >= 400:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            message = None
            code = None
            if isinstance(payload, dict):
                message = payload.get("error") or payload.get("message") or payload.get("err")
                code = payload.get("code") or payload.get("errorCode") or payload.get("ECODE")
            raise CeloxisAPIError(
                f"Celoxis API error: {self._sanitize(str(message))}" if message else f"Celoxis returned HTTP {response.status_code}.",
                status_code=response.status_code,
                code=str(code) if code else None,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise CeloxisAPIError("Celoxis returned an invalid response.") from exc
        if not isinstance(payload, (dict, list)):
            raise CeloxisAPIError("Celoxis returned an invalid response.")
        return payload

    def _unwrap(self, payload: Any) -> Any:
        if isinstance(payload, dict) and "data" in payload:
            return payload["data"]
        return payload

    async def _paginate(self, path: str, *, params: dict[str, Any] | None = None, cap: int | None = None) -> list[dict[str, Any]]:
        query = dict(params or {})
        limit = cap if cap is not None else _max_items()
        collected: list[dict[str, Any]] = []
        page = 1
        truncated = False
        while True:
            query["page"] = page
            payload = await self._request("GET", path, params=query)
            data = self._unwrap(payload)
            if isinstance(data, list):
                batch = [item for item in data if isinstance(item, dict)]
            elif isinstance(data, dict):
                batch = [data]
            else:
                batch = []
            collected.extend(batch)
            if limit and len(collected) >= limit:
                collected = collected[:limit]
                truncated = True
                break
            next_page = payload.get("nextPage") if isinstance(payload, dict) else None
            if not next_page:
                break
            page = next_page
        if truncated:
            logger.warning("Celoxis listing truncated at %s items", limit)
        return collected

    @classmethod
    def _task_to_dict(cls, task: dict[str, Any], project_id: str = "") -> dict[str, Any]:
        raw = _as_dict(task) if False else (task if isinstance(task, dict) else {})
        state = raw.get("custom_kanban_state") or raw.get("state")
        custom_fields: dict[str, Any] = {}
        for key, value in raw.items():
            if str(key).startswith("custom_") and value not in (None, ""):
                custom_fields[str(key)] = value
        completed = raw.get("actualFinish")
        completed_at = format_date(completed) if completed not in (None, "") else None
        return NormalizedWorkItem(
            id=str(raw.get("id") or ""),
            identifier=_identifier(raw.get("id")),
            title=raw.get("name") or "",
            description=raw.get("description") or None,
            description_content_type="text/plain" if raw.get("description") else None,
            url=raw.get("url"),
            state=state,
            state_id=_identifier(state),
            state_category=normalize_state_category(state),
            priority=raw.get("priority"),
            priority_value=normalize_priority_value(str(raw.get("priority") or "")),
            start_date=format_date(raw.get("plannedStart")),
            due_date=format_date(raw.get("plannedFinish") or raw.get("deadline")),
            completed_at=completed_at,
            created_at=format_date(raw.get("created")),
            updated_at=format_date(raw.get("lastModified")),
            assignee=None,
            assignee_id=None,
            creator=None,
            creator_id=None,
            parent_id=None,
            project_id=_identifier(project_id) if project_id else None,
            project_name=None,
            labels=[],
            item_type="task",
            custom_fields=custom_fields,
            provider="celoxis",
            raw=raw,
        ).model_dump()

    @staticmethod
    def _normalize_project(raw: dict[str, Any]) -> dict[str, Any]:
        raw = raw if isinstance(raw, dict) else {}
        return {
            "id": str(raw.get("id") or ""),
            "name": raw.get("name") or str(raw.get("id") or ""),
            "identifier": raw.get("code"),
            "url": raw.get("url"),
        }

    async def validate_slug(self, slug: str) -> dict | None:
        try:
            projects = await self.list_projects(slug)
            return {"slug": slug, "valid": True, "projects_count": len(projects)}
        except Exception:
            return None

    async def list_projects(self, workspace_slug: str) -> list[dict]:
        slug = str(workspace_slug or "").strip()
        if not slug:
            raise ValueError("The Celoxis workspace id must not be empty.")
        raw = await self._paginate("/projects", params={})
        return [self._normalize_project(p) for p in raw if isinstance(p, dict) and p.get("id") is not None]

    async def list_work_items(self, workspace_slug: str, project_id: str, expand: str | None = None, board_id: str | None = None, sprint_ids: list[str] | None = None) -> list[dict]:
        if board_id or sprint_ids:
            raise ValueError("Celoxis does not support board or iteration filters.")
        filter_obj = {"project.id": str(project_id)}
        raw = await self._paginate("/tasks", params={"filter": json.dumps(filter_obj, separators=(",", ":"))})
        return [self._task_to_dict(t, project_id) for t in raw]

    async def get_work_item(self, workspace_slug: str, project_id: str, work_item_id: str) -> dict:
        payload = await self._request("GET", f"/tasks/{work_item_id}")
        task = self._unwrap(payload)
        if not (isinstance(task, dict) and task.get("id")):
            raise CeloxisAPIError("The requested Celoxis task was not found.", status_code=404)
        return self._task_to_dict(task, project_id)

    async def list_states(self, workspace_slug: str, project_id: str, board_id: str | None = None) -> list[dict]:
        return []

    async def update_work_item(self, workspace_slug: str, project_id: str, work_item_id: str, data: dict) -> dict:
        target = (data or {}).get("state")
        if not target:
            raise ValueError("A target Celoxis status is required.")
        states = await self.list_states(workspace_slug, project_id)
        needle = str(target).strip().casefold()
        match = next((s for s in states if needle in {s["id"].casefold(), s["name"].casefold()}), None)
        if match is None:
            raise ValueError(f"No Celoxis status matches '{target}' on project {project_id}.")
        await self._request("PATCH", "/tasks", json={"id": work_item_id, "custom_kanban_state": match["name"]})
        return await self.get_work_item(workspace_slug, project_id, work_item_id)

    async def list_boards(self, workspace_slug: str, project_id: str) -> list[dict]:
        return []

    async def list_sprints(self, workspace_slug: str, project_id: str, board_id: str | None = None) -> list[dict]:
        return []

    async def get_board_scope(self, workspace_slug: str, project_id: str, board_id: str | None = None, sprint_ids: list[str] | None = None) -> dict:
        return {"board_id": None, "scope": "all", "sprint": None, "sprints": []}
