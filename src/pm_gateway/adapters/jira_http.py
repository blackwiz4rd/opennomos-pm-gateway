"""Jira HTTP provider — implements the generic PMProvider interface via the
generated Jira OpenAPI client."""

from __future__ import annotations

import asyncio
import logging
import os
from types import SimpleNamespace
from typing import Any

import httpx

from pm_gateway.adapters.jira import JiraProvider
from pm_gateway.adapters.normalization import (
    normalize_state_category,
)
from pm_gateway.generated.providers.jira.api.issue_search_api import IssueSearchApi
from pm_gateway.generated.providers.jira.api.issues_api import IssuesApi
from pm_gateway.generated.providers.jira.api.projects_api import ProjectsApi
from pm_gateway.generated.providers.jira.api.workflow_statuses_api import (
    WorkflowStatusesApi,
)
from pm_gateway.generated.providers.jira.api_client import ApiClient
from pm_gateway.generated.providers.jira.configuration import Configuration
from pm_gateway.generated.providers.jira.models.issue_update_details import (
    IssueUpdateDetails,
)

logger = logging.getLogger("pm_gateway.adapters.jira_http")

DEFAULT_BASE_URL = "https://jira.atlassian.net"


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


def _to_obj(value: Any) -> Any:
    if isinstance(value, dict):
        return SimpleNamespace(**{k: _to_obj(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_to_obj(v) for v in value]
    return value


class JiraHttpAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class JiraHttpProvider:
    provider_key = "jira"

    def __init__(self, credential: str, base_url: str | None = None, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._credential = credential
        self._email, self._token = self._parse_credential(credential)
        if not base_url:
            raise ValueError("X-PM-Provider-Base-Url is required for Jira.")
        self._base_url = base_url.strip().rstrip("/")
        self._transport = transport
        self._client: ApiClient | None = None
        self._projects: ProjectsApi | None = None
        self._search: IssueSearchApi | None = None
        self._issues: IssuesApi | None = None
        self._statuses: WorkflowStatusesApi | None = None
        self._lock = asyncio.Lock()

    @staticmethod
    def _parse_credential(credential: str) -> tuple[str, str]:
        key, separator, token = str(credential or "").partition(":")
        key, token = key.strip(), token.strip()
        if not separator or not key or not token:
            raise ValueError("Jira credential must be in format: email:api-token")
        return key, token

    async def _ensure(self):
        if self._projects is not None:
            return self._projects, self._search, self._issues, self._statuses
        async with self._lock:
            if self._projects is None:
                config = Configuration()
                config.host = self._base_url
                config.username = self._email
                config.password = self._token
                client = ApiClient(config)
                if self._transport is not None:
                    client.rest_client.pool_manager = httpx.AsyncClient(transport=self._transport)
                self._client = client
                self._projects = ProjectsApi(client)
                self._search = IssueSearchApi(client)
                self._issues = IssuesApi(client)
                self._statuses = WorkflowStatusesApi(client)
            return self._projects, self._search, self._issues, self._statuses

    async def invalidate(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
        self._client = None
        self._projects = None
        self._search = None
        self._issues = None
        self._statuses = None

    def _sanitize(self, text: str) -> str:
        value = str(text or "")
        for secret in {self._credential, self._token, self._email}:
            if secret:
                value = value.replace(secret, "[redacted]")
        return value[:500]

    async def validate_slug(self, slug: str) -> dict | None:
        try:
            projects = await self.list_projects(slug)
            return {"slug": slug, "valid": True, "projects_count": len(projects)}
        except Exception:
            return None

    async def list_projects(self, workspace_slug: str) -> list[dict]:
        projects_api, *_ = await self._ensure()
        try:
            page = await projects_api.search_projects(max_results=50)
        except Exception as exc:
            raise JiraHttpAPIError(self._sanitize(str(exc)), status_code=getattr(exc, "status", None)) from exc
        values = getattr(page, "values", None) or []
        out = []
        for p in values:
            raw = _as_dict(p)
            shim = SimpleNamespace(key=raw.get("key"), name=raw.get("name"), id=raw.get("id"))
            out.append(JiraProvider._project_to_dict(shim))
        return out

    async def list_work_items(self, workspace_slug: str, project_id: str, expand: str | None = None, board_id: str | None = None, sprint_ids: list[str] | None = None) -> list[dict]:
        _, search_api, *_ = await self._ensure()
        jql = f'project = "{project_id}" ORDER BY updated DESC'
        try:
            result = await search_api.search_and_reconsile_issues_using_jql(jql=jql, max_results=100, fields=["*all"])
        except Exception as exc:
            raise JiraHttpAPIError(self._sanitize(str(exc)), status_code=getattr(exc, "status", None)) from exc
        issues = getattr(result, "issues", None) or []
        out = []
        for issue in issues:
            raw = _as_dict(issue)
            fields = _to_obj(raw.get("fields") or {})
            shim = SimpleNamespace(key=raw.get("key"), fields=fields, id=raw.get("id"))
            out.append(JiraProvider._issue_to_dict(shim))
        return out

    async def get_work_item(self, workspace_slug: str, project_id: str, work_item_id: str) -> dict:
        _, _, issues_api, _ = await self._ensure()
        try:
            issue = await issues_api.get_issue(issue_id_or_key=work_item_id, fields=["*all"])
        except Exception as exc:
            raise JiraHttpAPIError(self._sanitize(str(exc)), status_code=getattr(exc, "status", None)) from exc
        raw = _as_dict(issue)
        fields = _to_obj(raw.get("fields") or {})
        shim = SimpleNamespace(key=raw.get("key"), fields=fields, id=raw.get("id"))
        if not raw.get("key") and not raw.get("id"):
            raise JiraHttpAPIError("The requested Jira issue was not found.", status_code=404)
        return JiraProvider._issue_to_dict(shim)

    async def list_states(self, workspace_slug: str, project_id: str, board_id: str | None = None) -> list[dict]:
        *_, statuses_api = await self._ensure()
        try:
            statuses = await statuses_api.get_statuses()
        except Exception as exc:
            raise JiraHttpAPIError(self._sanitize(str(exc)), status_code=getattr(exc, "status", None)) from exc
        out = []
        for s in (statuses or []):
            raw = _as_dict(s)
            cat = (raw.get("statusCategory") or {}).get("name")
            out.append({"id": str(raw.get("id")), "name": raw.get("name"), "group": normalize_state_category(cat) or raw.get("name"), "description": raw.get("description")})
        return out

    async def update_work_item(self, workspace_slug: str, project_id: str, work_item_id: str, data: dict) -> dict:
        target = (data or {}).get("state")
        if not target:
            raise ValueError("A target Jira status is required.")
        states = await self.list_states(workspace_slug, project_id)
        needle = str(target).strip().casefold()
        match = next((s for s in states if needle in {str(s["id"]).casefold(), s["name"].casefold()}), None)
        if match is None:
            # try transitions
            pass
        _, _, issues_api, _ = await self._ensure()
        # find transition
        try:
            transitions = await issues_api.get_transitions(issue_id_or_key=work_item_id)
        except Exception as exc:
            raise JiraHttpAPIError(self._sanitize(str(exc)), status_code=getattr(exc, "status", None)) from exc
        trans_list = getattr(transitions, "transitions", None)
        if trans_list is None:
            trans_list = _as_dict(transitions).get("transitions", [])
        transition_id = None
        for t in (trans_list or []):
            raw = _as_dict(t)
            to_raw = _as_dict(raw.get("to"))
            if needle in {str(raw.get("id", "")).casefold(), str(raw.get("name", "")).casefold(), str(to_raw.get("id", "")).casefold(), str(to_raw.get("name", "")).casefold()}:
                transition_id = raw.get("id")
                break
        if transition_id is None:
            raise ValueError(f"No Jira transition matches '{target}' for issue {work_item_id}.")
        try:
            await issues_api.do_transition(issue_id_or_key=work_item_id, issue_update_details=IssueUpdateDetails(transition={"id": str(transition_id)}))
        except Exception as exc:
            raise JiraHttpAPIError(self._sanitize(str(exc)), status_code=getattr(exc, "status", None)) from exc
        return await self.get_work_item(workspace_slug, project_id, work_item_id)

    async def list_boards(self, workspace_slug: str, project_id: str) -> list[dict]:
        return []

    async def list_sprints(self, workspace_slug: str, project_id: str, board_id: str | None = None) -> list[dict]:
        return []

    async def get_board_scope(self, workspace_slug: str, project_id: str, board_id: str | None = None, sprint_ids: list[str] | None = None) -> dict:
        return {"board_id": None, "scope": "all", "sprint": None, "sprints": []}
