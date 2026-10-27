# coding: utf-8

from typing import Dict, List  # noqa: F401
import importlib
import pkgutil

from pm_gateway.apis.work_items_api_base import BaseWorkItemsApi
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
from datetime import datetime
from pydantic import Field, StrictBool, StrictStr, field_validator
from typing import List, Optional
from typing_extensions import Annotated
from pm_gateway.models.api_error import ApiError
from pm_gateway.models.attachment_page import AttachmentPage
from pm_gateway.models.comment_page import CommentPage
from pm_gateway.models.work_item import WorkItem
from pm_gateway.models.work_item_page import WorkItemPage
from pm_gateway.models.work_item_patch import WorkItemPatch
from pm_gateway.security_api import get_token_bearerAuth

router = APIRouter()

ns_pkg = pm_gateway.impl
for _, name, _ in pkgutil.iter_modules(ns_pkg.__path__, ns_pkg.__name__ + "."):
    importlib.import_module(name)


@router.get(
    "/workspaces/{workspaceId}/containers/{containerId}/work-items",
    responses={
        200: {"model": WorkItemPage, "description": "A page of work items."},
        400: {"model": ApiError, "description": "Invalid request or unsupported query parameter."},
        401: {"model": ApiError, "description": "Authentication is missing or invalid."},
        403: {"model": ApiError, "description": "The authenticated principal lacks access to this resource."},
        404: {"model": ApiError, "description": "Workspace, container, or work item was not found or is inaccessible."},
        429: {"model": ApiError, "description": "Request rate limit exceeded."},
        500: {"model": ApiError, "description": "Unexpected server or upstream-provider failure."},
    },
    tags=["Work items"],
    summary="List work items in a container",
    response_model_by_alias=True,
)
async def list_work_items(
    workspaceId: StrictStr = Path(..., description=""),
    containerId: StrictStr = Path(..., description=""),
    x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")] = Header(None, description="Adapter key selected by OpenNomos."),
    x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")] = Header(None, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.", min_length=1),
    cursor: Annotated[Optional[StrictStr], Field(description="Opaque continuation token; omit for the first page.")] = Query(None, description="Opaque continuation token; omit for the first page.", alias="cursor"),
    limit: Optional[Annotated[int, Field(le=200, ge=1)]] = Query(50, description="", alias="limit", ge=1, le=200),
    changed_since: Annotated[Optional[datetime], Field(description="Return items updated at or after this RFC 3339 timestamp.")] = Query(None, description="Return items updated at or after this RFC 3339 timestamp.", alias="changedSince"),
    state_id: Annotated[Optional[StrictStr], Field(description="Provider-native workflow state ID.")] = Query(None, description="Provider-native workflow state ID.", alias="stateId"),
    assignee_id: Annotated[Optional[StrictStr], Field(description="Provider-scoped principal ID.")] = Query(None, description="Provider-scoped principal ID.", alias="assigneeId"),
    archived: Annotated[Optional[StrictBool], Field(description="Filter for archived items; omission means no archive filter.")] = Query(None, description="Filter for archived items; omission means no archive filter.", alias="archived"),
    deleted: Annotated[Optional[StrictBool], Field(description="Filter for deleted items when the provider exposes deleted records. A provider that cannot list tombstones must report that capability as unsupported rather than treating deleted=false as a complete sync.")] = Query(None, description="Filter for deleted items when the provider exposes deleted records. A provider that cannot list tombstones must report that capability as unsupported rather than treating deleted&#x3D;false as a complete sync.", alias="deleted"),
    query: Annotated[Optional[StrictStr], Field(description="Optional provider-supported text search.")] = Query(None, description="Optional provider-supported text search.", alias="query"),
    sort: Annotated[Optional[StrictStr], Field(description="Sort expression supported by the implementation. Supported fields must be declared in capabilities; a leading minus indicates descending order.")] = Query(None, description="Sort expression supported by the implementation. Supported fields must be declared in capabilities; a leading minus indicates descending order.", alias="sort", examples=["-updatedAt"]),
    board_id: Annotated[Optional[StrictStr], Field(description="Provider-native board identifier. Optional on providers without board concepts.")] = Query(None, description="Provider-native board identifier. Optional on providers without board concepts.", alias="boardId"),
    iteration_ids: Annotated[Optional[List[StrictStr]], Field(description="Provider-native iteration identifiers, such as Jira sprint IDs.")] = Query(None, description="Provider-native iteration identifiers, such as Jira sprint IDs.", alias="iterationIds"),
    x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")] = Header(None, description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults."),
    token_bearerAuth: TokenModel = Security(
        get_token_bearerAuth
    ),
) -> WorkItemPage:
    """Unsupported filters must be reported through capabilities or rejected; implementations must not silently return results that ignore a filter. A cursor is opaque to clients and is only valid for the query context documented by the implementation."""
    if not BaseWorkItemsApi.subclasses:
        raise HTTPException(status_code=500, detail="Not implemented")
    return await BaseWorkItemsApi.subclasses[0]().list_work_items(workspaceId, containerId, x_pm_provider, x_pm_provider_credential, cursor, limit, changed_since, state_id, assignee_id, archived, deleted, query, sort, board_id, iteration_ids, x_pm_provider_base_url)


@router.get(
    "/workspaces/{workspaceId}/work-items/{workItemId}",
    responses={
        200: {"model": WorkItem, "description": "The work item."},
        401: {"model": ApiError, "description": "Authentication is missing or invalid."},
        403: {"model": ApiError, "description": "The authenticated principal lacks access to this resource."},
        404: {"model": ApiError, "description": "Workspace, container, or work item was not found or is inaccessible."},
        429: {"model": ApiError, "description": "Request rate limit exceeded."},
        500: {"model": ApiError, "description": "Unexpected server or upstream-provider failure."},
    },
    tags=["Work items"],
    summary="Get a work item",
    response_model_by_alias=True,
)
async def get_work_item(
    workspaceId: StrictStr = Path(..., description=""),
    workItemId: StrictStr = Path(..., description=""),
    x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")] = Header(None, description="Adapter key selected by OpenNomos."),
    x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")] = Header(None, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.", min_length=1),
    container_id: Annotated[Optional[StrictStr], Field(description="Optional native container context required by some providers to retrieve a work item. OpenNomos should provide it when known.")] = Query(None, description="Optional native container context required by some providers to retrieve a work item. OpenNomos should provide it when known.", alias="containerId"),
    x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")] = Header(None, description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults."),
    token_bearerAuth: TokenModel = Security(
        get_token_bearerAuth
    ),
) -> WorkItem:
    if not BaseWorkItemsApi.subclasses:
        raise HTTPException(status_code=500, detail="Not implemented")
    return await BaseWorkItemsApi.subclasses[0]().get_work_item(workspaceId, workItemId, x_pm_provider, x_pm_provider_credential, container_id, x_pm_provider_base_url)


@router.patch(
    "/workspaces/{workspaceId}/work-items/{workItemId}",
    responses={
        200: {"model": WorkItem, "description": "Updated work item."},
        400: {"model": ApiError, "description": "Invalid request or unsupported query parameter."},
        401: {"model": ApiError, "description": "Authentication is missing or invalid."},
        403: {"model": ApiError, "description": "The authenticated principal lacks access to this resource."},
        404: {"model": ApiError, "description": "Workspace, container, or work item was not found or is inaccessible."},
        409: {"model": ApiError, "description": "The requested operation conflicts with current provider state."},
        412: {"model": ApiError, "description": "The supplied revision token does not match the current resource."},
        422: {"model": ApiError, "description": "The provider rejected one or more field values or transitions."},
        501: {"model": ApiError, "description": "The selected provider does not support this optional operation."},
        429: {"model": ApiError, "description": "Request rate limit exceeded."},
        500: {"model": ApiError, "description": "Unexpected server or upstream-provider failure."},
    },
    tags=["Work items"],
    summary="Update supported work-item fields",
    response_model_by_alias=True,
)
async def update_work_item(
    workspaceId: StrictStr = Path(..., description=""),
    workItemId: StrictStr = Path(..., description=""),
    x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")] = Header(None, description="Adapter key selected by OpenNomos."),
    x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")] = Header(None, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.", min_length=1),
    work_item_patch: WorkItemPatch = Body(None, description=""),
    container_id: Annotated[Optional[StrictStr], Field(description="Optional native container context required by some providers to retrieve a work item. OpenNomos should provide it when known.")] = Query(None, description="Optional native container context required by some providers to retrieve a work item. OpenNomos should provide it when known.", alias="containerId"),
    if_match: Annotated[Optional[StrictStr], Field(description="Opaque revision token returned by GET, when supported.")] = Header(None, description="Opaque revision token returned by GET, when supported."),
    x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")] = Header(None, description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults."),
    token_bearerAuth: TokenModel = Security(
        get_token_bearerAuth
    ),
) -> WorkItem:
    """This operation is available only when the update capability is advertised. Omitted properties are unchanged. A null value requests clearing a field only when that field is nullable and writable. State changes that require a workflow action use the transitions operation."""
    if not BaseWorkItemsApi.subclasses:
        raise HTTPException(status_code=500, detail="Not implemented")
    return await BaseWorkItemsApi.subclasses[0]().update_work_item(workspaceId, workItemId, x_pm_provider, x_pm_provider_credential, work_item_patch, container_id, if_match, x_pm_provider_base_url)


@router.get(
    "/workspaces/{workspaceId}/work-items/{workItemId}/comments",
    responses={
        200: {"model": CommentPage, "description": "A page of comments."},
        401: {"model": ApiError, "description": "Authentication is missing or invalid."},
        403: {"model": ApiError, "description": "The authenticated principal lacks access to this resource."},
        404: {"model": ApiError, "description": "Workspace, container, or work item was not found or is inaccessible."},
        501: {"model": ApiError, "description": "The selected provider does not support this optional operation."},
        500: {"model": ApiError, "description": "Unexpected server or upstream-provider failure."},
    },
    tags=["Work items"],
    summary="List comments or discussion entries on a work item",
    response_model_by_alias=True,
)
async def list_work_item_comments(
    workspaceId: StrictStr = Path(..., description=""),
    workItemId: StrictStr = Path(..., description=""),
    x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")] = Header(None, description="Adapter key selected by OpenNomos."),
    x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")] = Header(None, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.", min_length=1),
    cursor: Annotated[Optional[StrictStr], Field(description="Opaque continuation token; omit for the first page.")] = Query(None, description="Opaque continuation token; omit for the first page.", alias="cursor"),
    limit: Optional[Annotated[int, Field(le=200, ge=1)]] = Query(50, description="", alias="limit", ge=1, le=200),
    x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")] = Header(None, description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults."),
    token_bearerAuth: TokenModel = Security(
        get_token_bearerAuth
    ),
) -> CommentPage:
    """Optional capability. Providers may expose comments as stories, updates, notes, or discussions."""
    if not BaseWorkItemsApi.subclasses:
        raise HTTPException(status_code=500, detail="Not implemented")
    return await BaseWorkItemsApi.subclasses[0]().list_work_item_comments(workspaceId, workItemId, x_pm_provider, x_pm_provider_credential, cursor, limit, x_pm_provider_base_url)


@router.get(
    "/workspaces/{workspaceId}/work-items/{workItemId}/attachments",
    responses={
        200: {"model": AttachmentPage, "description": "A page of attachment metadata."},
        401: {"model": ApiError, "description": "Authentication is missing or invalid."},
        403: {"model": ApiError, "description": "The authenticated principal lacks access to this resource."},
        404: {"model": ApiError, "description": "Workspace, container, or work item was not found or is inaccessible."},
        501: {"model": ApiError, "description": "The selected provider does not support this optional operation."},
        500: {"model": ApiError, "description": "Unexpected server or upstream-provider failure."},
    },
    tags=["Work items"],
    summary="List files and links attached to a work item",
    response_model_by_alias=True,
)
async def list_work_item_attachments(
    workspaceId: StrictStr = Path(..., description=""),
    workItemId: StrictStr = Path(..., description=""),
    x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")] = Header(None, description="Adapter key selected by OpenNomos."),
    x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")] = Header(None, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.", min_length=1),
    cursor: Annotated[Optional[StrictStr], Field(description="Opaque continuation token; omit for the first page.")] = Query(None, description="Opaque continuation token; omit for the first page.", alias="cursor"),
    limit: Optional[Annotated[int, Field(le=200, ge=1)]] = Query(50, description="", alias="limit", ge=1, le=200),
    x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")] = Header(None, description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults."),
    token_bearerAuth: TokenModel = Security(
        get_token_bearerAuth
    ),
) -> AttachmentPage:
    """Optional capability. File content download is outside this listing operation."""
    if not BaseWorkItemsApi.subclasses:
        raise HTTPException(status_code=500, detail="Not implemented")
    return await BaseWorkItemsApi.subclasses[0]().list_work_item_attachments(workspaceId, workItemId, x_pm_provider, x_pm_provider_credential, cursor, limit, x_pm_provider_base_url)
