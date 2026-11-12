"""GitHub Issues provider — implements the generic PMProvider interface."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from pm_gateway.adapters.models import NormalizedWorkItem
from pm_gateway.security import provider_timeout_seconds
from pm_gateway.adapters.normalization import (
    format_datetime,
)
from pm_gateway.generated.providers.github.api.issues_api import IssuesApi
from pm_gateway.generated.providers.github.api.repos_api import ReposApi
from pm_gateway.generated.providers.github.api_client import ApiClient
from pm_gateway.generated.providers.github.configuration import Configuration
from pm_gateway.generated.providers.github.models.issues_update_request import (
    IssuesUpdateRequest,
)

logger = logging.getLogger("pm_gateway.adapters.github")

DEFAULT_BASE_URL = "https://api.github.com"


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


class GitHubAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class GitHubProvider:
    provider_key = "github"

    def __init__(self, credential: str, base_url: str | None = None, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._credential = credential
        self._base_url = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
        self._transport = transport
        self._client: ApiClient | None = None
        self._repos: ReposApi | None = None
        self._issues: IssuesApi | None = None
        self._lock = asyncio.Lock()

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._credential.strip()}", "Accept": "application/vnd.github+json"}

    async def _ensure(self) -> tuple[ReposApi, IssuesApi]:
        if self._repos is not None and self._issues is not None:
            return self._repos, self._issues
        async with self._lock:
            if self._repos is None or self._issues is None:
                config = Configuration()
                config.host = self._base_url
                client = ApiClient(config)
                if self._transport is not None:
                    client.rest_client.pool_manager = httpx.AsyncClient(transport=self._transport)
                self._client = client
                self._repos = ReposApi(client)
                self._issues = IssuesApi(client)
            return self._repos, self._issues

    async def invalidate(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
        self._client = None
        self._repos = None
        self._issues = None

    def _sanitize(self, text: str) -> str:
        value = str(text or "")
        for secret in {self._credential, self._credential.strip()}:
            if secret:
                value = value.replace(secret, "[redacted]")
        return value[:500]

    def _owner(self, workspace_slug: str) -> str:
        slug = str(workspace_slug or "").strip()
        if slug and slug.lower() not in {"", "me"}:
            return slug
        return "me"

    @classmethod
    def _issue_to_dict(cls, issue: Any, project_id: str, owner: str) -> dict[str, Any]:
        raw = _as_dict(issue)
        if raw.get("pull_request"):
            return {}
        labels = [str(_as_dict(l).get("name")) for l in _as_list(raw.get("labels")) if _as_dict(l).get("name")]
        assignee = _as_dict(raw.get("assignee"))
        state = raw.get("state")
        state_category = "completed" if state == "closed" else "started" if state == "open" else None
        number = raw.get("number")
        return NormalizedWorkItem(
            id=str(raw.get("id") or number or ""),
            identifier=str(number) if number is not None else _identifier(raw.get("id")),
            title=raw.get("title") or "",
            description=raw.get("body") or None,
            description_content_type="text/markdown" if raw.get("body") else None,
            url=raw.get("html_url") or raw.get("url"),
            state=state,
            state_id=state,
            state_category=state_category,
            start_date=None,
            due_date=None,
            completed_at=format_datetime(raw.get("closed_at")),
            created_at=format_datetime(raw.get("created_at")),
            updated_at=format_datetime(raw.get("updated_at")),
            assignee=assignee.get("login"),
            assignee_id=_identifier(assignee.get("id")),
            creator=_as_dict(raw.get("user")).get("login"),
            creator_id=_identifier(_as_dict(raw.get("user")).get("id")),
            parent_id=None,
            project_id=str(project_id),
            project_name=None,
            labels=labels,
            item_type="issue",
            custom_fields={},
            memberships=[],
            provider="github",
            raw=raw,
        ).model_dump()

    @staticmethod
    def _normalize_repo(raw: dict[str, Any]) -> dict[str, Any]:
        return {"id": str(raw.get("name") or ""), "name": raw.get("name") or str(raw.get("id") or ""), "identifier": None, "url": raw.get("html_url") or raw.get("url")}

    async def validate_slug(self, slug: str) -> dict | None:
        try:
            projects = await self.list_projects(slug)
            return {"slug": slug, "valid": True, "projects_count": len(projects)}
        except Exception:
            return None

    async def list_projects(self, workspace_slug: str) -> list[dict]:
        repos_api, _ = await self._ensure()
        headers = self._headers()
        owner = self._owner(workspace_slug)
        try:
            if owner == "me":
                raw = await repos_api.repos_list_for_authenticated_user(_headers=headers)
            else:
                try:
                    raw = await repos_api.repos_list_for_user(username=owner, _headers=headers)
                except Exception:
                    raw = await repos_api.repos_list_for_org(org=owner, _headers=headers)
        except Exception as exc:
            raise GitHubAPIError(self._sanitize(str(exc)), status_code=getattr(exc, "status", None)) from exc
        return [self._normalize_repo(_as_dict(r)) for r in (raw or []) if _as_dict(r).get("name")]

    async def _resolve_owner(self, workspace_slug: str) -> str:
        owner = self._owner(workspace_slug)
        if owner != "me":
            return owner
        repos_api, _ = await self._ensure()
        try:
            raw = await repos_api.repos_list_for_authenticated_user(per_page=1, _headers=self._headers())
            if raw:
                login = _as_dict(raw[0]).get("owner", {}).get("login")
                if login:
                    return str(login)
        except Exception:
            pass
        try:
            import httpx
            response = await asyncio.to_thread(
                lambda: httpx.get(
                    f"{self._base_url.rstrip('/')}/user",
                    headers=self._headers(),
                    timeout=provider_timeout_seconds(),
                )
            )
            if response.status_code == 200:
                login = response.json().get("login")
                if login:
                    return str(login)
        except Exception:
            pass
        return owner

    async def list_work_items(self, workspace_slug: str, project_id: str, expand: str | None = None, board_id: str | None = None, sprint_ids: list[str] | None = None) -> list[dict]:
        owner = await self._resolve_owner(workspace_slug)
        _, issues_api = await self._ensure()
        headers = self._headers()
        try:
            raw = await issues_api.issues_list_for_repo(owner=owner, repo=project_id, state="all", per_page=100, _headers=headers)
        except Exception as exc:
            raise GitHubAPIError(self._sanitize(str(exc)), status_code=getattr(exc, "status", None)) from exc
        items = []
        for issue in (raw or []):
            normalized = self._issue_to_dict(issue, project_id, owner)
            if normalized:
                items.append(normalized)
        return items

    async def get_work_item(self, workspace_slug: str, project_id: str, work_item_id: str) -> dict:
        owner = await self._resolve_owner(workspace_slug)
        _, issues_api = await self._ensure()
        try:
            issue = await issues_api.issues_get(owner=owner, repo=project_id, issue_number=int(work_item_id), _headers=self._headers())
        except Exception as exc:
            raise GitHubAPIError(self._sanitize(str(exc)), status_code=getattr(exc, "status", None)) from exc
        raw = _as_dict(issue)
        if not raw.get("id") and raw.get("number") is None:
            raise GitHubAPIError("The requested GitHub issue was not found.", status_code=404)
        return self._issue_to_dict(issue, project_id, owner)

    async def list_states(self, workspace_slug: str, project_id: str, board_id: str | None = None) -> list[dict]:
        return [
            {"id": "open", "name": "Open", "group": "unstarted"},
            {"id": "closed", "name": "Closed", "group": "completed"},
        ]

    async def update_work_item(self, workspace_slug: str, project_id: str, work_item_id: str, data: dict) -> dict:
        target = str((data or {}).get("state") or "").strip().lower()
        if target not in {"open", "closed"}:
            # accept state names
            if "close" in target or "done" in target or "complete" in target:
                target = "closed"
            elif "open" in target or "reopen" in target:
                target = "open"
            else:
                raise ValueError(f"No GitHub state matches '{target}'.")
        owner = await self._resolve_owner(workspace_slug)
        _, issues_api = await self._ensure()
        try:
            await issues_api.issues_update(owner=owner, repo=project_id, issue_number=int(work_item_id), issues_update_request=IssuesUpdateRequest(state=target), _headers=self._headers())
        except Exception as exc:
            raise GitHubAPIError(self._sanitize(str(exc)), status_code=getattr(exc, "status", None)) from exc
        return await self.get_work_item(workspace_slug, project_id, work_item_id)

    async def list_boards(self, workspace_slug: str, project_id: str) -> list[dict]:
        return []

    async def list_sprints(self, workspace_slug: str, project_id: str, board_id: str | None = None) -> list[dict]:
        return []

    async def get_board_scope(self, workspace_slug: str, project_id: str, board_id: str | None = None, sprint_ids: list[str] | None = None) -> dict:
        return {"board_id": None, "scope": "all", "sprint": None, "sprints": []}
