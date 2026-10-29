import asyncio
import logging
from functools import partial

from plane import PlaneClient
from plane.models.query_params import WorkItemQueryParams
from plane.models.work_items import UpdateWorkItem

from pm_gateway.adapters.models import NormalizedWorkItem
from pm_gateway.adapters.normalization import (
    format_date,
    format_datetime,
    normalize_priority_value,
    normalize_state_category,
)
from pm_gateway.security import provider_timeout_seconds

logger = logging.getLogger("pm_gateway.adapters.plane")


class PlaneProvider:
    provider_key = "plane"

    def __init__(self, credential: str, base_url: str | None = None):
        self._credential = credential
        self._base_url = (base_url or "https://api.plane.so").rstrip("/")
        self._clients: dict[str, PlaneClient] = {}
        self._lock = asyncio.Lock()

    async def invalidate(self) -> None:
        """Drop all cached clients so the next call rebuilds from current settings.

        Called after one request so credential-bearing clients are not retained.
        """
        for client in self._clients.values():
            close = getattr(client, "close", None)
            if close is not None:
                try:
                    await asyncio.to_thread(close)
                except Exception as exc:  # pragma: no cover - best-effort cleanup
                    logger.debug("Could not close Plane client: %s", type(exc).__name__)
        self._clients.clear()

    async def _ensure(self, workspace_slug: str) -> PlaneClient:
        if workspace_slug in self._clients:
            return self._clients[workspace_slug]

        async with self._lock:
            if workspace_slug in self._clients:
                return self._clients[workspace_slug]

            if not self._credential:
                raise RuntimeError(
                    f"Plane API credential for workspace '{workspace_slug}' has an empty key."
                )

            client = PlaneClient(
                base_url=self._base_url,
                api_key=self._credential,
            )
            client.config.timeout = provider_timeout_seconds()
            self._clients[workspace_slug] = client
            return client

    def _normalize_work_item(self, raw: dict, project_id: str = "") -> dict:
        """Convert a Plane work item to the normalized model."""
        state = raw.get("state")
        state_name = None
        state_id = None
        state_category = None
        if isinstance(state, dict):
            state_name = state.get("name")
            state_id = state.get("id")
            state_category = normalize_state_category(state.get("group"))
        elif isinstance(state, str):
            state_name = state

        priority = raw.get("priority")
        priority_str = None
        if isinstance(priority, str):
            priority_str = priority
        elif isinstance(priority, dict):
            priority_str = priority.get("label") or priority.get("value")

        assignees = raw.get("assignees") or []
        assignee_name = None
        assignee_id = None
        if assignees and isinstance(assignees, list):
            first = assignees[0]
            if isinstance(first, dict):
                assignee_name = first.get("name") or first.get("display_name")
                assignee_id = first.get("id")
            elif isinstance(first, str):
                assignee_id = first

        labels = raw.get("labels") or []
        label_names = []
        for label in labels:
            if isinstance(label, dict):
                name = label.get("name")
                if name:
                    label_names.append(name)
            elif isinstance(label, str):
                label_names.append(label)

        item_type = raw.get("type")
        type_name = None
        if isinstance(item_type, dict):
            type_name = item_type.get("name")
        elif isinstance(item_type, str):
            type_name = item_type

        project = raw.get("project")
        project_name = None
        if isinstance(project, dict):
            project_name = project.get("name")

        estimate = raw.get("estimate_point")
        if estimate is not None:
            try:
                estimate = float(estimate)
            except (ValueError, TypeError):
                estimate = None

        sequence_id = raw.get("sequence_id")
        identifier = None
        if sequence_id is not None:
            identifier = str(sequence_id)

        if raw.get("description_stripped") is not None:
            description = raw.get("description_stripped")
            description_content_type = "text/plain"
        elif raw.get("description_html") is not None:
            description = raw.get("description_html")
            description_content_type = "text/html"
        else:
            description = None
            description_content_type = None

        return NormalizedWorkItem(
            id=raw.get("id", ""),
            identifier=identifier,
            title=raw.get("name", ""),
            description=description,
            description_content_type=description_content_type,
            state=state_name,
            state_id=state_id,
            state_category=state_category,
            priority=priority_str,
            priority_value=normalize_priority_value(priority_str, "plane"),
            estimate=estimate,
            start_date=format_date(raw.get("start_date")),
            due_date=format_date(raw.get("target_date")),
            completed_at=format_datetime(raw.get("completed_at")),
            created_at=format_datetime(raw.get("created_at")),
            updated_at=format_datetime(raw.get("updated_at")),
            assignee=assignee_name,
            assignee_id=assignee_id,
            creator_id=raw.get("created_by"),
            parent_id=raw.get("parent"),
            project_id=raw.get("project") if isinstance(raw.get("project"), str) else project_id,
            project_name=project_name,
            labels=label_names,
            item_type=type_name,
            provider="plane",
            raw=raw,
        ).model_dump()

    @staticmethod
    def _normalize_project(raw: dict) -> dict:
        """Convert a Plane project to a normalized dict."""
        return {
            "id": raw.get("id", ""),
            "name": raw.get("name", ""),
            "identifier": raw.get("identifier"),
        }

    @staticmethod
    def _normalize_state(raw: dict) -> dict:
        """Convert a Plane state to a normalized dict."""
        return {
            "id": raw.get("id", ""),
            "name": raw.get("name", ""),
            "group": raw.get("group"),
        }

    async def validate_slug(self, slug: str) -> dict | None:
        try:
            projects = await self.list_projects(slug)
            return {"slug": slug, "valid": True, "projects_count": len(projects)}
        except Exception:
            return None

    async def list_projects(self, workspace_slug: str) -> list[dict]:
        client = await self._ensure(workspace_slug)
        result = await asyncio.to_thread(
            partial(client.projects.list, workspace_slug)
        )
        return [self._normalize_project(p.model_dump()) for p in result.results]

    async def list_work_items(
        self, workspace_slug: str, project_id: str, expand: str | None = None,
        board_id: str | None = None, sprint_ids: list[str] | None = None,
    ) -> list[dict]:
        if board_id or sprint_ids:
            raise ValueError("Plane does not support board or iteration filters.")
        client = await self._ensure(workspace_slug)
        # Status is part of the gateway core representation, so request the
        # native state relation even when the consumer did not request a legacy
        # provider-specific expand value.
        params = WorkItemQueryParams(expand=expand or "state")
        result = await asyncio.to_thread(
            partial(client.work_items.list, workspace_slug, project_id, params=params)
        )
        return [self._normalize_work_item(w.model_dump(), project_id) for w in result.results]

    async def get_work_item(
        self, workspace_slug: str, project_id: str, work_item_id: str,
    ) -> dict:
        client = await self._ensure(workspace_slug)
        result = await asyncio.to_thread(
            partial(client.work_items.retrieve, workspace_slug, project_id, work_item_id)
        )
        return self._normalize_work_item(result.model_dump(), project_id)

    async def list_states(
        self, workspace_slug: str, project_id: str, board_id: str | None = None
    ) -> list[dict]:
        # Plane has no board concept; states are project-wide.
        client = await self._ensure(workspace_slug)
        result = await asyncio.to_thread(
            partial(client.states.list, workspace_slug, project_id)
        )
        return [self._normalize_state(s.model_dump()) for s in result.results]

    async def update_work_item(
        self, workspace_slug: str, project_id: str, work_item_id: str, data: dict,
    ) -> dict:
        client = await self._ensure(workspace_slug)
        payload = UpdateWorkItem(state=(data or {}).get("state"))
        result = await asyncio.to_thread(
            partial(client.work_items.update, workspace_slug, project_id, work_item_id, payload)
        )
        return self._normalize_work_item(result.model_dump(), project_id)

    async def list_boards(self, workspace_slug: str, project_id: str) -> list[dict]:
        """Plane has no board resource distinct from its project workflow."""
        return []

    async def list_sprints(
        self, workspace_slug: str, project_id: str, board_id: str | None = None
    ) -> list[dict]:
        return []

    async def get_board_scope(
        self,
        workspace_slug: str,
        project_id: str,
        board_id: str | None = None,
        sprint_ids: list[str] | None = None,
    ) -> dict:
        return {"board_id": None, "scope": "all", "sprint": None, "sprints": []}
