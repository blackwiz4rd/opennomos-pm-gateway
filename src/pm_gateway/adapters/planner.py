"""Microsoft Planner provider — implements the generic PMProvider interface.

Uses the Microsoft Graph API (https://graph.microsoft.com/v1.0) with OAuth2
client-credentials authentication via ``msal``. Planner concepts map onto the
interface as follows:

* workspace_slug -> Microsoft 365 Group ID (the group that owns plans)
* project_id     -> Planner Plan ID
* work_item_id   -> Planner Task ID
* state          -> Bucket name (Planner's board columns)
* state_category -> derived from ``percentComplete`` (0=unstarted, 1-99=started, 100=completed)

Authentication: the credential ``api_key`` is a colon-separated string
``tenant_id:client_id:client_secret`` used to acquire an app-only token from
Microsoft Entra ID. The token is cached and auto-refreshed by ``msal``.
"""

import asyncio
import logging
import time
from typing import Any

import httpx
from msal import ConfidentialClientApplication

from pm_gateway.adapters.models import NormalizedWorkItem
from pm_gateway.adapters.normalization import (
    format_date,
    format_datetime,
)
from pm_gateway.security import provider_timeout_seconds, validate_provider_base_url

logger = logging.getLogger("pm_gateway.adapters.planner")

# Microsoft Graph API base URL (can be overridden for sovereign clouds)
_GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

# Planner priority mapping: Planner uses 0-10 scale where 0=urgent, 10=low.
# We map to the Linear convention used by the normalized model.
_PLANNER_PRIORITY_MAP: dict[int, int] = {
    0: 1,   # urgent
    1: 1,   # urgent
    2: 2,   # important
    3: 2,   # important
    4: 2,   # important
    5: 3,   # medium
    6: 3,   # medium
    7: 3,   # medium
    8: 4,   # low
    9: 4,   # low
    10: 4,  # low
}


def _map_planner_priority(value: int | None) -> int | None:
    """Map a Planner priority (0-10) to the normalized scale (0-4)."""
    if value is None:
        return None
    return _PLANNER_PRIORITY_MAP.get(value)


def _state_category_from_percent(percent_complete: int | None) -> str | None:
    """Derive a state category from a Planner task's percentComplete."""
    if percent_complete is None:
        return None
    if percent_complete >= 100:
        return "completed"
    if percent_complete > 0:
        return "started"
    return "unstarted"


class PlannerProvider:
    provider_key = "planner"

    def __init__(self, credential: str, base_url: str | None = None) -> None:
        self._credential = credential
        if base_url is not None:
            validate_provider_base_url("planner", base_url)
        self._base_url = (base_url or _GRAPH_BASE_URL).strip().rstrip("/")
        self._credential_parts = self._parse_api_key(credential)
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._tokens: dict[str, dict] = {}  # workspace_slug -> {"token": str, "expires_on": int}
        self._msal_app: ConfidentialClientApplication | None = None
        self._msal_lock = asyncio.Lock()
        self._lock = asyncio.Lock()

    async def invalidate(self) -> None:
        """Drop all cached clients and tokens so the next call rebuilds from current settings."""
        for client in self._clients.values():
            await client.aclose()
        self._clients.clear()
        self._tokens.clear()
        self._msal_app = None

    # ── credential parsing ──

    @staticmethod
    def _parse_api_key(api_key: str) -> tuple[str, str, str]:
        """Parse ``tenant_id:client_id:client_secret`` from the api_key field."""
        parts = api_key.split(":", 2)
        if len(parts) != 3:
            raise ValueError(
                "Planner credential must be in format: tenant_id:client_id:client_secret"
            )
        tenant_id, client_id, client_secret = parts
        if not all([tenant_id.strip(), client_id.strip(), client_secret.strip()]):
            raise ValueError(
                "Planner credential must be in format: tenant_id:client_id:client_secret"
            )
        return tenant_id.strip(), client_id.strip(), client_secret.strip()

    # ── MSAL token management ──

    async def _get_msal_app(self, tenant_id: str, client_id: str, client_secret: str) -> ConfidentialClientApplication:
        """Get or create the MSAL ConfidentialClientApplication."""
        if self._msal_app is not None:
            return self._msal_app

        async with self._msal_lock:
            if self._msal_app is not None:
                return self._msal_app

            authority = f"https://login.microsoftonline.com/{tenant_id}"
            self._msal_app = ConfidentialClientApplication(
                client_id=client_id,
                client_credential=client_secret,
                authority=authority,
            )
            return self._msal_app

    async def _acquire_token(self, workspace_slug: str, tenant_id: str, client_id: str, client_secret: str) -> str:
        """Acquire an app-only token for Microsoft Graph, with caching."""
        cached = self._tokens.get(workspace_slug)
        if cached and cached.get("expires_on", 0) > time.time() + 300:
            # Token still valid (with 5-minute buffer)
            return cached["token"]

        app = await self._get_msal_app(tenant_id, client_id, client_secret)

        result = await asyncio.to_thread(
            app.acquire_token_for_client,
            scopes=["https://graph.microsoft.com/.default"],
        )

        if "access_token" not in result:
            error = result.get("error", "unknown")
            error_desc = result.get("error_description", "No description")
            raise RuntimeError(f"MSAL token acquisition failed: {error} — {error_desc}")

        self._tokens[workspace_slug] = {
            "token": result["access_token"],
            "expires_on": int(result.get("expires_on", 0)),
        }
        return result["access_token"]

    # ── HTTP client management ──

    async def _ensure(self, workspace_slug: str) -> httpx.AsyncClient:
        if workspace_slug in self._clients:
            return self._clients[workspace_slug]

        async with self._lock:
            if workspace_slug in self._clients:
                return self._clients[workspace_slug]

            if not self._credential:
                raise RuntimeError(
                    f"Planner credential for workspace '{workspace_slug}' has an empty key."
                )

            tenant_id, client_id, client_secret = self._credential_parts
            token = await self._acquire_token(workspace_slug, tenant_id, client_id, client_secret)

            client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                timeout=provider_timeout_seconds(),
                follow_redirects=False,
            )
            self._clients[workspace_slug] = client
            return client

    async def _refresh_token_and_retry(self, workspace_slug: str, method: str, path: str, **kwargs) -> httpx.Response:
        """Refresh the token and retry a request that failed with 401."""
        # Clear cached token
        self._tokens.pop(workspace_slug, None)

        tenant_id, client_id, client_secret = self._credential_parts
        token = await self._acquire_token(workspace_slug, tenant_id, client_id, client_secret)

        # Update client headers
        client = self._clients.get(workspace_slug)
        if client is not None:
            client.headers["Authorization"] = f"Bearer {token}"

        return await client.request(method, path, **kwargs)

    async def _graph_get(self, workspace_slug: str, path: str, params: dict | None = None) -> dict:
        """Make a GET request to the Graph API."""
        client = await self._ensure(workspace_slug)
        response = await client.get(path, params=params)

        if response.status_code == 401:
            # Token expired mid-request, refresh and retry
            response = await self._refresh_token_and_retry(workspace_slug, "GET", path, params=params)

        response.raise_for_status()
        return response.json()

    async def _graph_patch(self, workspace_slug: str, path: str, data: dict) -> dict:
        """Make a PATCH request to the Graph API."""
        client = await self._ensure(workspace_slug)
        response = await client.patch(path, json=data)

        if response.status_code == 401:
            response = await self._refresh_token_and_retry(workspace_slug, "PATCH", path, json=data)

        response.raise_for_status()
        return response.json()

    # ── normalization ──

    @staticmethod
    def _plan_to_dict(plan: dict) -> dict:
        """Convert a Planner plan to a normalized project dict."""
        return {
            "id": plan.get("id", ""),
            "name": plan.get("title", ""),
            "identifier": plan.get("id", ""),
        }

    @staticmethod
    def _bucket_to_dict(bucket: dict) -> dict:
        """Convert a Planner bucket to a normalized state dict."""
        return {
            "id": bucket.get("id", ""),
            "name": bucket.get("name", ""),
            "order_hint": bucket.get("orderHint"),
        }

    @staticmethod
    def _task_to_dict(task: dict, bucket_name: str | None = None) -> dict:
        """Convert a Planner task to the normalized work item model."""
        percent = task.get("percentComplete")
        state_category = _state_category_from_percent(percent)

        # Determine state name from bucket
        state_name = bucket_name
        if state_name is None:
            # Try to get bucket name from bucketId if we have a lookup
            state_name = task.get("bucketId", "")

        # Priority
        priority_value = task.get("priority")
        normalized_priority = _map_planner_priority(priority_value)

        # Assignments
        assignments = task.get("assignments", {})
        assignee_id = None
        if assignments:
            # Get the first assignee
            assignee_id = next(iter(assignments.keys()), None)

        # Dates
        start_date = task.get("startDateTime")
        due_date = task.get("dueDateTime")
        created_at = task.get("createdDateTime")
        completed_at = task.get("completedDateTime")

        # Item type
        item_type = "task"
        applied_categories = task.get("appliedCategories", {})
        if applied_categories:
            # Planner categories can indicate task types
            item_type = ",".join(applied_categories.keys())

        return NormalizedWorkItem(
            id=task.get("id", ""),
            identifier=task.get("id", ""),
            title=task.get("title", ""),
            description=(task.get("details", {}) or {}).get("description") if task.get("details") else None,
            state=state_name,
            state_id=task.get("bucketId"),
            state_category=state_category,
            priority=str(priority_value) if priority_value is not None else None,
            priority_value=normalized_priority,
            start_date=format_date(start_date) if start_date else None,
            due_date=format_date(due_date) if due_date else None,
            completed_at=format_datetime(completed_at) if completed_at else None,
            created_at=format_datetime(created_at) if created_at else None,
            updated_at=format_datetime(created_at) if created_at else None,
            assignee_id=assignee_id,
            project_id=task.get("planId"),
            labels=list(applied_categories.keys()) if applied_categories else [],
            item_type=item_type,
            provider="planner",
            raw=task,
        ).model_dump()

    # ── PMProvider interface ──

    async def validate_slug(self, slug: str) -> dict | None:
        try:
            projects = await self.list_projects(slug)
            return {"slug": slug, "valid": True, "projects_count": len(projects)}
        except Exception:
            return None

    async def list_projects(self, workspace_slug: str) -> list[dict]:
        """List all Planner plans in a Microsoft 365 group."""
        data = await self._graph_get(
            workspace_slug,
            f"/groups/{workspace_slug}/planner/plans",
        )
        plans = data.get("value", [])
        return [self._plan_to_dict(p) for p in plans]

    async def list_work_items(
        self,
        workspace_slug: str,
        project_id: str,
        expand: str | None = None,
        board_id: str | None = None,
        sprint_ids: list[str] | None = None,
    ) -> list[dict]:
        """List tasks in a Planner plan, optionally filtered by bucket."""
        if sprint_ids:
            raise ValueError("Microsoft Planner does not support iterations or sprints.")
        params = {}
        if expand:
            params["$expand"] = expand

        data = await self._graph_get(
            workspace_slug,
            f"/planner/plans/{project_id}/tasks",
            params=params if params else None,
        )
        tasks = data.get("value", [])

        # Build bucket lookup for state names
        bucket_names: dict[str, str] = {}
        try:
            bucket_data = await self._graph_get(
                workspace_slug,
                f"/planner/plans/{project_id}/buckets",
            )
            for b in bucket_data.get("value", []):
                bucket_names[b.get("id", "")] = b.get("name", "")
        except Exception:
            logger.debug("Could not fetch buckets for plan %s", project_id)

        # Filter by bucket if specified
        if board_id:
            tasks = [t for t in tasks if t.get("bucketId") == board_id]

        return [self._task_to_dict(t, bucket_names.get(t.get("bucketId"))) for t in tasks]

    async def get_work_item(
        self,
        workspace_slug: str,
        project_id: str,
        work_item_id: str,
    ) -> dict:
        """Get a single Planner task with details."""
        params = {"$expand": "details,assignments"}
        data = await self._graph_get(
            workspace_slug,
            f"/planner/tasks/{work_item_id}",
            params=params,
        )

        # Get bucket name
        bucket_names: dict[str, str] = {}
        try:
            bucket_data = await self._graph_get(
                workspace_slug,
                f"/planner/plans/{project_id}/buckets",
            )
            for b in bucket_data.get("value", []):
                bucket_names[b.get("id", "")] = b.get("name", "")
        except Exception as exc:
            logger.debug("Could not fetch buckets for plan %s: %s", project_id, type(exc).__name__)

        return self._task_to_dict(data, bucket_names.get(data.get("bucketId")))

    async def list_states(
        self, workspace_slug: str, project_id: str, board_id: str | None = None
    ) -> list[dict]:
        """List buckets (states) in a Planner plan."""
        data = await self._graph_get(
            workspace_slug,
            f"/planner/plans/{project_id}/buckets",
        )
        buckets = data.get("value", [])
        return [self._bucket_to_dict(b) for b in buckets]

    async def update_work_item(
        self,
        workspace_slug: str,
        project_id: str,
        work_item_id: str,
        data: dict,
    ) -> dict:
        """Update a Planner task (state/percentComplete/priority)."""
        patch_data: dict[str, Any] = {}

        # State change -> move to bucket
        target_state = (data or {}).get("state")
        if target_state:
            # Find bucket by name
            bucket_data = await self._graph_get(
                workspace_slug,
                f"/planner/plans/{project_id}/buckets",
            )
            for b in bucket_data.get("value", []):
                if target_state in (b.get("id"), b.get("name")) or b.get("name", "").lower() == target_state.lower():
                    patch_data["bucketId"] = b["id"]
                    break

        # Also allow direct percentComplete updates
        if "percentComplete" in (data or {}):
            patch_data["percentComplete"] = data["percentComplete"]

        if patch_data:
            # Planner uses ETag for concurrency; fetch current task first
            current = await self._graph_get(
                workspace_slug,
                f"/planner/tasks/{work_item_id}",
            )
            etag = current.get("@odata.etag", "")

            client = await self._ensure(workspace_slug)
            headers = {"If-Match": etag} if etag else {}
            response = await client.patch(
                f"/planner/tasks/{work_item_id}",
                json=patch_data,
                headers=headers,
            )

            if response.status_code == 401:
                response = await self._refresh_token_and_retry(
                    workspace_slug, "PATCH", f"/planner/tasks/{work_item_id}",
                    json=patch_data,
                )

            response.raise_for_status()
            updated = response.json()
        else:
            updated = await self._graph_get(
                workspace_slug,
                f"/planner/tasks/{work_item_id}",
            )

        # Get bucket name for the updated task
        bucket_names: dict[str, str] = {}
        try:
            bucket_data = await self._graph_get(
                workspace_slug,
                f"/planner/plans/{project_id}/buckets",
            )
            for b in bucket_data.get("value", []):
                bucket_names[b.get("id", "")] = b.get("name", "")
        except Exception as exc:
            logger.debug("Could not fetch buckets for plan %s: %s", project_id, type(exc).__name__)

        return self._task_to_dict(updated, bucket_names.get(updated.get("bucketId")))

    async def list_boards(self, workspace_slug: str, project_id: str) -> list[dict]:
        """Return buckets as boards (Planner buckets act as board columns)."""
        buckets = await self.list_states(workspace_slug, project_id)
        return [
            {
                "id": b["id"],
                "name": b["name"],
                "type": "bucket",
            }
            for b in buckets
        ]

    async def list_sprints(
        self, workspace_slug: str, project_id: str, board_id: str | None = None
    ) -> list[dict]:
        """Planner has no sprint concept — return empty list."""
        return []

    async def get_board_scope(
        self,
        workspace_slug: str,
        project_id: str,
        board_id: str | None = None,
        sprint_ids: list[str] | None = None,
    ) -> dict | None:
        """Describe the board scope for a Planner bucket."""
        if board_id:
            return {
                "board_id": board_id,
                "scope": "all",
                "sprint": None,
                "sprints": [],
            }
        return None
