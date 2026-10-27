# coding: utf-8

from typing import Dict, List  # noqa: F401
import importlib
import pkgutil

from pm_gateway.apis.workflow_api_base import BaseWorkflowApi
import pm_gateway.impl

from fastapi import (  # noqa: F401
    APIRouter,
    Body,
    Cookie,
    Depends,
    Form,
    Header,
    HTTPException,
    Path,
    Query,
    Response,
    Security,
    status,
)

from pm_gateway.models.extra_models import TokenModel  # noqa: F401
from pydantic import Field, StrictStr, field_validator
from typing import Optional
from typing_extensions import Annotated
from pm_gateway.models.api_error import ApiError
from pm_gateway.models.transition_request import TransitionRequest
from pm_gateway.models.work_item import WorkItem
from pm_gateway.models.workflow_state_page import WorkflowStatePage
from pm_gateway.security_api import get_token_bearerAuth

router = APIRouter()

ns_pkg = pm_gateway.impl
for _, name, _ in pkgutil.iter_modules(ns_pkg.__path__, ns_pkg.__name__ + "."):
    importlib.import_module(name)


@router.get(
    "/workspaces/{workspaceId}/containers/{containerId}/workflow-states",
    responses={
        200: {"model": WorkflowStatePage, "description": "Native workflow states and optional common categories."},
        401: {"model": ApiError, "description": "Authentication is missing or invalid."},
        403: {"model": ApiError, "description": "The authenticated principal lacks access to this resource."},
        404: {"model": ApiError, "description": "Workspace, container, or work item was not found or is inaccessible."},
        500: {"model": ApiError, "description": "Unexpected server or upstream-provider failure."},
    },
    tags=["Workflow"],
    summary="List native workflow states for a container",
    response_model_by_alias=True,
)
async def list_workflow_states(
    workspaceId: StrictStr = Path(..., description=""),
    containerId: StrictStr = Path(..., description=""),
    x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")] = Header(None, description="Adapter key selected by OpenNomos."),
    x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")] = Header(None, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.", min_length=1),
    board_id: Annotated[Optional[StrictStr], Field(description="Optional provider-native board ID used to scope workflow states.")] = Query(None, description="Optional provider-native board ID used to scope workflow states.", alias="boardId"),
    x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")] = Header(None, description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults."),
    token_bearerAuth: TokenModel = Security(
        get_token_bearerAuth
    ),
) -> WorkflowStatePage:
    if not BaseWorkflowApi.subclasses:
        raise HTTPException(status_code=500, detail="Not implemented")
    return await BaseWorkflowApi.subclasses[0]().list_workflow_states(workspaceId, containerId, x_pm_provider, x_pm_provider_credential, board_id, x_pm_provider_base_url)


@router.post(
    "/workspaces/{workspaceId}/work-items/{workItemId}/transitions",
    responses={
        200: {"model": WorkItem, "description": "Work item after the transition."},
        400: {"model": ApiError, "description": "Invalid request or unsupported query parameter."},
        401: {"model": ApiError, "description": "Authentication is missing or invalid."},
        403: {"model": ApiError, "description": "The authenticated principal lacks access to this resource."},
        404: {"model": ApiError, "description": "Workspace, container, or work item was not found or is inaccessible."},
        409: {"model": ApiError, "description": "The requested operation conflicts with current provider state."},
        422: {"model": ApiError, "description": "The provider rejected one or more field values or transitions."},
        501: {"model": ApiError, "description": "The selected provider does not support this optional operation."},
        429: {"model": ApiError, "description": "Request rate limit exceeded."},
        500: {"model": ApiError, "description": "Unexpected server or upstream-provider failure."},
    },
    tags=["Workflow"],
    summary="Transition a work item to a native workflow state",
    response_model_by_alias=True,
)
async def transition_work_item(
    workspaceId: StrictStr = Path(..., description=""),
    workItemId: StrictStr = Path(..., description=""),
    x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")] = Header(None, description="Adapter key selected by OpenNomos."),
    x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")] = Header(None, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.", min_length=1),
    transition_request: Optional[TransitionRequest] = Body(None, description=""),
    container_id: Annotated[Optional[StrictStr], Field(description="Optional native container context required by some providers to retrieve a work item. OpenNomos should provide it when known.")] = Query(None, description="Optional native container context required by some providers to retrieve a work item. OpenNomos should provide it when known.", alias="containerId"),
    if_match: Annotated[Optional[StrictStr], Field(description="Opaque revision token, when supported.")] = Header(None, description="Opaque revision token, when supported."),
    x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")] = Header(None, description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults."),
    token_bearerAuth: TokenModel = Security(
        get_token_bearerAuth
    ),
) -> WorkItem:
    """Optional capability. The provider validates that the requested transition is allowed in the current workflow. A provider may expose available transitions in the workflow-state representation."""
    if not BaseWorkflowApi.subclasses:
        raise HTTPException(status_code=500, detail="Not implemented")
    return await BaseWorkflowApi.subclasses[0]().transition_work_item(workspaceId, workItemId, x_pm_provider, x_pm_provider_credential, transition_request, container_id, if_match, x_pm_provider_base_url)
