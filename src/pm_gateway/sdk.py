"""In-process PM gateway SDK; no gateway process or network hop is involved."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pm_gateway.impl.provider_api import PMGateway as _ProviderGateway


class Operation(str, Enum):
    """Operations supported by the PM Gateway SDK."""

    GET_CAPABILITIES = "get_capabilities"
    LIST_WORKSPACES = "list_workspaces"
    LIST_CONTAINERS = "list_containers"
    LIST_BOARDS = "list_boards"
    GET_BOARD_SCOPE = "get_board_scope"
    LIST_CUSTOM_FIELD_DEFINITIONS = "list_custom_field_definitions"
    LIST_ITERATIONS = "list_iterations"
    LIST_MILESTONES = "list_milestones"
    LIST_WORK_ITEMS = "list_work_items"
    GET_WORK_ITEM = "get_work_item"
    UPDATE_WORK_ITEM = "update_work_item"
    LIST_WORK_ITEM_COMMENTS = "list_work_item_comments"
    LIST_WORK_ITEM_ATTACHMENTS = "list_work_item_attachments"
    LIST_WORKFLOW_STATES = "list_workflow_states"
    TRANSITION_WORK_ITEM = "transition_work_item"


class Provider(str, Enum):
    """Project-management providers supported by the gateway."""

    PLANE = "plane"
    JIRA = "jira"
    PLANNER = "planner"


class PMGateway:
    """Call PM provider operations directly from Python.

    Provider credentials are supplied per call and are never persisted. The
    returned values are the same generated models serialized by the HTTP API.
    """

    def __init__(self) -> None:
        self._gateway = _ProviderGateway()

    async def execute(
        self,
        operation: Operation,
        *,
        provider: Provider,
        credential: str,
        base_url: str | None = None,
        **params: Any,
    ) -> Any:
        """Execute a generated API operation directly against its adapter."""
        return await self._gateway.execute(
            operation.value,
            provider=provider.value,
            credential=credential,
            base_url=base_url,
            **params,
        )


__all__ = ["Operation", "PMGateway", "Provider"]
