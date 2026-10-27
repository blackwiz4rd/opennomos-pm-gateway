from __future__ import annotations

import base64
import binascii
import inspect
import logging
from datetime import timezone
from typing import Any

from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from pm_gateway.adapters.factory import open_provider, validate_provider_context
from pm_gateway.adapters.normalization import normalize_state_category
from pm_gateway.adapters.serialization import as_board, as_container, as_iteration, as_work_item
from pm_gateway.apis.discovery_api_base import BaseDiscoveryApi
from pm_gateway.apis.work_items_api_base import BaseWorkItemsApi
from pm_gateway.apis.workflow_api_base import BaseWorkflowApi
from pm_gateway.models.board_scope import BoardScope
from pm_gateway.models.capabilities import Capabilities
from pm_gateway.models.container_page import ContainerPage
from pm_gateway.models.feature_capability import FeatureCapability
from pm_gateway.models.iteration_page import IterationPage
from pm_gateway.models.mapping_metadata import MappingMetadata
from pm_gateway.models.provider_ref import ProviderRef
from pm_gateway.models.workflow_state import WorkflowState
from pm_gateway.models.workflow_state_page import WorkflowStatePage
from pm_gateway.models.workspace import Workspace
from pm_gateway.models.workspace_page import WorkspacePage
from pm_gateway.models.work_item_page import WorkItemPage

logger = logging.getLogger("pm_gateway.adapters")

_OPERATION_BASES = {
    "get_capabilities": BaseDiscoveryApi,
    "list_workspaces": BaseDiscoveryApi,
    "list_containers": BaseDiscoveryApi,
    "list_boards": BaseDiscoveryApi,
    "get_board_scope": BaseDiscoveryApi,
    "list_custom_field_definitions": BaseDiscoveryApi,
    "list_iterations": BaseDiscoveryApi,
    "list_milestones": BaseDiscoveryApi,
    "list_work_items": BaseWorkItemsApi,
    "get_work_item": BaseWorkItemsApi,
    "update_work_item": BaseWorkItemsApi,
    "list_work_item_comments": BaseWorkItemsApi,
    "list_work_item_attachments": BaseWorkItemsApi,
    "list_workflow_states": BaseWorkflowApi,
    "transition_work_item": BaseWorkflowApi,
}

_UNSUPPORTED_OPERATIONS = frozenset(
    {
        "update_work_item",
        "list_custom_field_definitions",
        "list_milestones",
        "list_work_item_comments",
        "list_work_item_attachments",
    }
)

_PROVIDER_NAMES = {"plane": "Plane", "jira": "Jira", "planner": "Microsoft Planner"}


def _arguments(base: type, operation: str, args: tuple, kwargs: dict) -> dict[str, Any]:
    signature = inspect.signature(getattr(base, operation))
    bound = signature.bind(None, *args, **kwargs)
    bound.apply_defaults()
    return {key: value for key, value in bound.arguments.items() if key != "self"}


def _cursor_offset(cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        value = base64.urlsafe_b64decode(padded.encode()).decode()
        if not value.startswith("offset:"):
            raise ValueError
        offset = int(value.removeprefix("offset:"))
        if offset < 0:
            raise ValueError
        return offset
    except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="Invalid continuation cursor.") from exc


def _next_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"offset:{offset}".encode()).decode().rstrip("=")


def _page(items: list[Any], cursor: str | None, limit: int | None) -> tuple[list[Any], str | None]:
    start = _cursor_offset(cursor)
    page_size = limit or 50
    selected = items[start : start + page_size]
    next_cursor = _next_cursor(start + page_size) if start + page_size < len(items) else None
    return selected, next_cursor


def _provider_ref(provider: str) -> ProviderRef:
    return ProviderRef(key=provider, product_name=_PROVIDER_NAMES[provider])


def _features(provider: str) -> list[FeatureCapability]:
    supports_boards = provider in {"jira", "planner"}
    supports_iterations = provider == "jira"
    return [
        FeatureCapability(name="workspaces.list", supported=True, scope="workspace"),
        FeatureCapability(name="containers.list", supported=True, scope="workspace"),
        FeatureCapability(name="work-items.read", supported=True, scope="container"),
        FeatureCapability(name="work-items.transition", supported=True, scope="work-item"),
        FeatureCapability(name="work-items.update", supported=False, scope="work-item"),
        FeatureCapability(name="workflow-states.list", supported=True, scope="container"),
        FeatureCapability(name="boards.list", supported=supports_boards, scope="container"),
        FeatureCapability(name="board-scope.read", supported=supports_boards, scope="container"),
        FeatureCapability(name="iterations.list", supported=supports_iterations, scope="container"),
        FeatureCapability(name="custom-fields.discovery", supported=False, scope="container"),
        FeatureCapability(name="milestones.list", supported=False, scope="container"),
        FeatureCapability(name="comments.read", supported=False, scope="work-item"),
        FeatureCapability(name="attachments.read", supported=False, scope="work-item"),
        FeatureCapability(name="work-items.deleted-filter", supported=False, scope="container"),
        FeatureCapability(name="work-items.archived-filter", supported=False, scope="container"),
    ]


def _filter_work_items(items: list[Any], params: dict[str, Any]) -> list[Any]:
    if params.get("archived") is not None:
        raise HTTPException(status_code=400, detail="The selected provider cannot filter archived work items.")
    if params.get("deleted") is not None:
        raise HTTPException(status_code=400, detail="The selected provider cannot filter deleted work items.")

    state_id = params.get("state_id")
    if state_id:
        items = [item for item in items if item.status and item.status.id == state_id]
    assignee_id = params.get("assignee_id")
    if assignee_id:
        items = [item for item in items if any(person.id == assignee_id for person in item.assignees or [])]
    changed_since = params.get("changed_since")
    if changed_since:
        if changed_since.tzinfo is None:
            changed_since = changed_since.replace(tzinfo=timezone.utc)
        def was_changed_since(item):
            updated_at = item.dates.updated_at if item.dates else None
            if updated_at is None:
                return False
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            return updated_at >= changed_since

        items = [item for item in items if was_changed_since(item)]
    query = params.get("query")
    if query:
        needle = query.casefold()
        items = [
            item for item in items
            if needle in item.title.casefold() or needle in (item.description or "").casefold()
        ]

    sort = params.get("sort")
    if sort:
        descending = sort.startswith("-")
        field = sort[1:] if sort[:1] in {"-", "+"} else sort

        def timestamp(value):
            if value is not None and value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value

        accessors = {
            "title": lambda item: item.title.casefold(),
            "createdAt": lambda item: timestamp(item.dates.created_at) if item.dates else None,
            "updatedAt": lambda item: timestamp(item.dates.updated_at) if item.dates else None,
        }
        if field not in accessors:
            raise HTTPException(status_code=400, detail=f"Unsupported sort field: {field}")
        items = sorted(items, key=lambda item: (accessors[field](item) is None, accessors[field](item)), reverse=descending)
    return items


def _unsupported(operation: str) -> None:
    raise HTTPException(
        status_code=501,
        detail=f"The selected provider does not support {operation}.",
    )


async def _run(operation: str, args: tuple, kwargs: dict):
    base = _OPERATION_BASES[operation]
    params = _arguments(base, operation, args, kwargs)
    provider = params.get("x_pm_provider")
    credential = params.get("x_pm_provider_credential")
    base_url = params.get("x_pm_provider_base_url")

    try:
        return await _dispatch(operation, params, provider, credential, base_url)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        # Never log upstream exception text: client libraries can include request
        # details, and this service receives credentials in request headers.
        logger.error("Provider %s failed during %s (%s)", provider, operation, type(exc).__name__)
        status_code = getattr(exc, "status_code", None)
        if status_code is None:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
        if status_code == 429:
            raise HTTPException(status_code=429, detail="The PM provider rate limit was reached.") from exc
        if status_code == 404:
            raise HTTPException(status_code=404, detail="The requested provider resource was not found.") from exc
        raise HTTPException(status_code=502, detail="The PM provider request failed.") from exc


async def _dispatch(operation: str, params: dict[str, Any], provider: str, credential: str, base_url: str | None):
    validate_provider_context(provider, credential, base_url)
    workspace_id = params.get("workspaceId")
    container_id = params.get("containerId") or params.get("container_id")
    work_item_id = params.get("workItemId")
    if operation == "get_capabilities":
        return Capabilities(
            api_version="1.0",
            core_profile="core-read-0.1",
            provider=_provider_ref(provider),
            features=_features(provider),
            limits={"pageSizeMax": 200},
        )

    if operation == "list_workspaces":
        slugs = [slug.strip() for slug in (params.get("x_pm_workspace_slugs") or "").split(",") if slug.strip()]
        workspaces = [
            Workspace(id=slug, name=slug, provider=_provider_ref(provider))
            for slug in slugs
        ]
        page, next_cursor = _page(workspaces, params.get("cursor"), params.get("limit"))
        return WorkspacePage(items=page, next_cursor=next_cursor)

    async with open_provider(provider, credential, base_url) as adapter:
        if operation == "list_containers":
            raw = await adapter.list_projects(workspace_id)
            values = [as_container(value, provider) for value in raw]
            page, next_cursor = _page(values, params.get("cursor"), params.get("limit"))
            return ContainerPage(items=page, next_cursor=next_cursor)

        if operation == "list_boards":
            raw = await adapter.list_boards(workspace_id, container_id)
            values = [as_board(value, provider) for value in raw]
            page, next_cursor = _page(values, params.get("cursor"), params.get("limit"))
            return ContainerPage(items=page, next_cursor=next_cursor)

        if operation == "get_board_scope":
            scope = await adapter.get_board_scope(
                workspace_id,
                container_id,
                board_id=params.get("board_id"),
                sprint_ids=params.get("iteration_ids"),
            )
            scope = scope or {"board_id": None, "scope": "all", "sprint": None, "sprints": []}
            current = scope.get("sprint")
            return BoardScope(
                board_id=scope.get("board_id"),
                scope=scope.get("scope", "all"),
                sprint=as_iteration(current, provider) if current else None,
                sprints=[as_iteration(value, provider) for value in scope.get("sprints", [])],
            )

        if operation == "list_iterations":
            raw = await adapter.list_sprints(
                workspace_id, container_id, params.get("board_id")
            )
            values = [as_iteration(value, provider) for value in raw]
            page, next_cursor = _page(values, params.get("cursor"), params.get("limit"))
            return IterationPage(items=page, next_cursor=next_cursor)

        if operation == "list_workflow_states":
            raw = await adapter.list_states(workspace_id, container_id, params.get("board_id"))
            values = [
                WorkflowState(
                    id=str(value["id"]) if value.get("id") is not None else None,
                    name=str(value.get("name") or value.get("id") or "Unknown"),
                    category=normalize_state_category(value.get("group") or value.get("name")),
                    mapping=MappingMetadata(
                        quality="mapped" if normalize_state_category(value.get("group") or value.get("name")) else "unmapped"
                    ),
                )
                for value in raw
            ]
            return WorkflowStatePage(items=values, next_cursor=None)

        if operation == "list_work_items":
            raw_items = await adapter.list_work_items(
                workspace_id,
                container_id,
                board_id=params.get("board_id"),
                sprint_ids=params.get("iteration_ids"),
            )
            items = [
                as_work_item(
                    value,
                    provider=provider,
                    workspace_id=workspace_id,
                    container_id=container_id,
                    base_url=base_url,
                )
                for value in raw_items
            ]
            items = _filter_work_items(items, params)
            page, next_cursor = _page(items, params.get("cursor"), params.get("limit"))
            return WorkItemPage(items=page, next_cursor=next_cursor)

        if operation == "get_work_item":
            if provider in {"plane", "planner"} and not container_id:
                raise HTTPException(
                    status_code=400,
                    detail="containerId is required by this provider to retrieve the work item.",
                )
            raw = await adapter.get_work_item(workspace_id, container_id or "", work_item_id)
            return as_work_item(
                raw,
                provider=provider,
                workspace_id=workspace_id,
                container_id=container_id or raw.get("project_id") or "",
                base_url=base_url,
            )

        if operation == "transition_work_item":
            if provider in {"plane", "planner"} and not container_id:
                raise HTTPException(
                    status_code=400,
                    detail="containerId is required by this provider to transition the work item.",
                )
            request = params.get("transition_request")
            target = None
            if request is not None:
                target = request.target_state_id or request.target_state_name
            if not target:
                raise HTTPException(status_code=400, detail="A target state ID or name is required.")
            raw = await adapter.update_work_item(
                workspace_id,
                container_id or "",
                work_item_id,
                {"state": target},
            )
            return as_work_item(
                raw,
                provider=provider,
                workspace_id=workspace_id,
                container_id=container_id or raw.get("project_id") or "",
                base_url=base_url,
            )

        if operation in _UNSUPPORTED_OPERATIONS:
            _unsupported(operation.replace("_", " "))

    raise HTTPException(status_code=501, detail=f"Unknown operation: {operation}")


class _ProviderApi:
    def __getattribute__(self, name: str):
        if name in _OPERATION_BASES:

            async def invoke(*args, **kwargs):
                result = await _run(name, args, kwargs)
                return JSONResponse(content=jsonable_encoder(_wire_value(result)))

            return invoke
        return object.__getattribute__(self, name)


def _wire_value(value: Any) -> Any:
    """Use generated model helpers to omit generator-only additional_properties."""
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _wire_value(to_dict())
    if isinstance(value, dict):
        return {key: _wire_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_wire_value(item) for item in value]
    return value


class GatewayDiscoveryApi(_ProviderApi, BaseDiscoveryApi):
    pass


class GatewayWorkItemsApi(_ProviderApi, BaseWorkItemsApi):
    pass


class GatewayWorkflowApi(_ProviderApi, BaseWorkflowApi):
    pass
