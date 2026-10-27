# coding: utf-8

from typing import Dict, List  # noqa: F401
import importlib
import pkgutil

from pm_gateway.apis.discovery_api_base import BaseDiscoveryApi
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
from typing import List, Optional
from typing_extensions import Annotated
from pm_gateway.models.api_error import ApiError
from pm_gateway.models.board_scope import BoardScope
from pm_gateway.models.capabilities import Capabilities
from pm_gateway.models.container_page import ContainerPage
from pm_gateway.models.custom_field_definition_page import CustomFieldDefinitionPage
from pm_gateway.models.iteration_page import IterationPage
from pm_gateway.models.milestone_page import MilestonePage
from pm_gateway.models.workspace_page import WorkspacePage
from pm_gateway.security_api import get_token_bearerAuth

router = APIRouter()

ns_pkg = pm_gateway.impl
for _, name, _ in pkgutil.iter_modules(ns_pkg.__path__, ns_pkg.__name__ + "."):
    importlib.import_module(name)


@router.get(
    "/capabilities",
    responses={
        200: {"model": Capabilities, "description": "Capabilities available to the current authorization context."},
        401: {"model": ApiError, "description": "Authentication is missing or invalid."},
        403: {"model": ApiError, "description": "The authenticated principal lacks access to this resource."},
        500: {"model": ApiError, "description": "Unexpected server or upstream-provider failure."},
    },
    tags=["Discovery"],
    summary="Get supported API profiles and capabilities",
    response_model_by_alias=True,
)
async def get_capabilities(
    x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")] = Header(None, description="Adapter key selected by OpenNomos."),
    x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")] = Header(None, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.", min_length=1),
    x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")] = Header(None, description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults."),
    token_bearerAuth: TokenModel = Security(
        get_token_bearerAuth
    ),
) -> Capabilities:
    if not BaseDiscoveryApi.subclasses:
        raise HTTPException(status_code=500, detail="Not implemented")
    return await BaseDiscoveryApi.subclasses[0]().get_capabilities(x_pm_provider, x_pm_provider_credential, x_pm_provider_base_url)


@router.get(
    "/workspaces",
    responses={
        200: {"model": WorkspacePage, "description": "A page of workspaces."},
        401: {"model": ApiError, "description": "Authentication is missing or invalid."},
        403: {"model": ApiError, "description": "The authenticated principal lacks access to this resource."},
        429: {"model": ApiError, "description": "Request rate limit exceeded."},
        500: {"model": ApiError, "description": "Unexpected server or upstream-provider failure."},
    },
    tags=["Discovery"],
    summary="List accessible workspaces",
    response_model_by_alias=True,
)
async def list_workspaces(
    x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")] = Header(None, description="Adapter key selected by OpenNomos."),
    x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")] = Header(None, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.", min_length=1),
    cursor: Annotated[Optional[StrictStr], Field(description="Opaque continuation token; omit for the first page.")] = Query(None, description="Opaque continuation token; omit for the first page.", alias="cursor"),
    limit: Optional[Annotated[int, Field(le=200, ge=1)]] = Query(50, description="", alias="limit", ge=1, le=200),
    x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")] = Header(None, description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults."),
    x_pm_workspace_slugs: Annotated[Optional[StrictStr], Field(description="Comma-separated workspace slugs configured by OpenNomos for this credential. Used only by workspace discovery; the gateway stores none.")] = Header(None, description="Comma-separated workspace slugs configured by OpenNomos for this credential. Used only by workspace discovery; the gateway stores none."),
    token_bearerAuth: TokenModel = Security(
        get_token_bearerAuth
    ),
) -> WorkspacePage:
    if not BaseDiscoveryApi.subclasses:
        raise HTTPException(status_code=500, detail="Not implemented")
    return await BaseDiscoveryApi.subclasses[0]().list_workspaces(x_pm_provider, x_pm_provider_credential, cursor, limit, x_pm_provider_base_url, x_pm_workspace_slugs)


@router.get(
    "/workspaces/{workspaceId}/containers",
    responses={
        200: {"model": ContainerPage, "description": "A page of containers."},
        401: {"model": ApiError, "description": "Authentication is missing or invalid."},
        403: {"model": ApiError, "description": "The authenticated principal lacks access to this resource."},
        404: {"model": ApiError, "description": "Workspace, container, or work item was not found or is inaccessible."},
        429: {"model": ApiError, "description": "Request rate limit exceeded."},
        500: {"model": ApiError, "description": "Unexpected server or upstream-provider failure."},
    },
    tags=["Discovery"],
    summary="List containers in a workspace",
    response_model_by_alias=True,
)
async def list_containers(
    workspaceId: StrictStr = Path(..., description=""),
    x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")] = Header(None, description="Adapter key selected by OpenNomos."),
    x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")] = Header(None, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.", min_length=1),
    cursor: Annotated[Optional[StrictStr], Field(description="Opaque continuation token; omit for the first page.")] = Query(None, description="Opaque continuation token; omit for the first page.", alias="cursor"),
    limit: Optional[Annotated[int, Field(le=200, ge=1)]] = Query(50, description="", alias="limit", ge=1, le=200),
    x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")] = Header(None, description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults."),
    token_bearerAuth: TokenModel = Security(
        get_token_bearerAuth
    ),
) -> ContainerPage:
    """Containers represent provider-native organizational resources such as projects, boards, plans, lists, repositories, or sheets. The kind is provider-defined; clients must not assume all kinds have identical semantics."""
    if not BaseDiscoveryApi.subclasses:
        raise HTTPException(status_code=500, detail="Not implemented")
    return await BaseDiscoveryApi.subclasses[0]().list_containers(workspaceId, x_pm_provider, x_pm_provider_credential, cursor, limit, x_pm_provider_base_url)


@router.get(
    "/workspaces/{workspaceId}/containers/{containerId}/boards",
    responses={
        200: {"model": ContainerPage, "description": "A page of boards represented as provider-native containers."},
        401: {"model": ApiError, "description": "Authentication is missing or invalid."},
        403: {"model": ApiError, "description": "The authenticated principal lacks access to this resource."},
        404: {"model": ApiError, "description": "Workspace, container, or work item was not found or is inaccessible."},
        500: {"model": ApiError, "description": "Unexpected server or upstream-provider failure."},
    },
    tags=["Discovery"],
    summary="List provider-native boards for a container",
    response_model_by_alias=True,
)
async def list_boards(
    workspaceId: StrictStr = Path(..., description=""),
    containerId: StrictStr = Path(..., description=""),
    x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")] = Header(None, description="Adapter key selected by OpenNomos."),
    x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")] = Header(None, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.", min_length=1),
    cursor: Annotated[Optional[StrictStr], Field(description="Opaque continuation token; omit for the first page.")] = Query(None, description="Opaque continuation token; omit for the first page.", alias="cursor"),
    limit: Optional[Annotated[int, Field(le=200, ge=1)]] = Query(50, description="", alias="limit", ge=1, le=200),
    x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")] = Header(None, description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults."),
    token_bearerAuth: TokenModel = Security(
        get_token_bearerAuth
    ),
) -> ContainerPage:
    """Optional provider capability. Providers without a board concept return an empty page and advertise boards.list as unsupported."""
    if not BaseDiscoveryApi.subclasses:
        raise HTTPException(status_code=500, detail="Not implemented")
    return await BaseDiscoveryApi.subclasses[0]().list_boards(workspaceId, containerId, x_pm_provider, x_pm_provider_credential, cursor, limit, x_pm_provider_base_url)


@router.get(
    "/workspaces/{workspaceId}/containers/{containerId}/board-scope",
    responses={
        200: {"model": BoardScope, "description": "The effective board scope."},
        401: {"model": ApiError, "description": "Authentication is missing or invalid."},
        403: {"model": ApiError, "description": "The authenticated principal lacks access to this resource."},
        404: {"model": ApiError, "description": "Workspace, container, or work item was not found or is inaccessible."},
        500: {"model": ApiError, "description": "Unexpected server or upstream-provider failure."},
    },
    tags=["Discovery"],
    summary="Describe the selected board and iteration scope",
    response_model_by_alias=True,
)
async def get_board_scope(
    workspaceId: StrictStr = Path(..., description=""),
    containerId: StrictStr = Path(..., description=""),
    x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")] = Header(None, description="Adapter key selected by OpenNomos."),
    x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")] = Header(None, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.", min_length=1),
    board_id: Optional[StrictStr] = Query(None, description="", alias="boardId"),
    iteration_ids: Optional[List[StrictStr]] = Query(None, description="", alias="iterationIds"),
    x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")] = Header(None, description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults."),
    token_bearerAuth: TokenModel = Security(
        get_token_bearerAuth
    ),
) -> BoardScope:
    """Compatibility extension for providers with board/backlog semantics."""
    if not BaseDiscoveryApi.subclasses:
        raise HTTPException(status_code=500, detail="Not implemented")
    return await BaseDiscoveryApi.subclasses[0]().get_board_scope(workspaceId, containerId, x_pm_provider, x_pm_provider_credential, board_id, iteration_ids, x_pm_provider_base_url)


@router.get(
    "/workspaces/{workspaceId}/containers/{containerId}/custom-fields",
    responses={
        200: {"model": CustomFieldDefinitionPage, "description": "A page of effective custom-field definitions."},
        401: {"model": ApiError, "description": "Authentication is missing or invalid."},
        403: {"model": ApiError, "description": "The authenticated principal lacks access to this resource."},
        404: {"model": ApiError, "description": "Workspace, container, or work item was not found or is inaccessible."},
        501: {"model": ApiError, "description": "The selected provider does not support this optional operation."},
        500: {"model": ApiError, "description": "Unexpected server or upstream-provider failure."},
    },
    tags=["Discovery"],
    summary="List custom-field definitions for a container",
    response_model_by_alias=True,
)
async def list_custom_field_definitions(
    workspaceId: StrictStr = Path(..., description=""),
    containerId: StrictStr = Path(..., description=""),
    x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")] = Header(None, description="Adapter key selected by OpenNomos."),
    x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")] = Header(None, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.", min_length=1),
    cursor: Annotated[Optional[StrictStr], Field(description="Opaque continuation token; omit for the first page.")] = Query(None, description="Opaque continuation token; omit for the first page.", alias="cursor"),
    limit: Optional[Annotated[int, Field(le=200, ge=1)]] = Query(50, description="", alias="limit", ge=1, le=200),
    x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")] = Header(None, description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults."),
    token_bearerAuth: TokenModel = Security(
        get_token_bearerAuth
    ),
) -> CustomFieldDefinitionPage:
    """Optional capability. Definitions may be container-, work-item-type-, or workspace-scoped. Implementations should report the effective scope and must use stable field IDs rather than display names as identifiers."""
    if not BaseDiscoveryApi.subclasses:
        raise HTTPException(status_code=500, detail="Not implemented")
    return await BaseDiscoveryApi.subclasses[0]().list_custom_field_definitions(workspaceId, containerId, x_pm_provider, x_pm_provider_credential, cursor, limit, x_pm_provider_base_url)


@router.get(
    "/workspaces/{workspaceId}/containers/{containerId}/iterations",
    responses={
        200: {"model": IterationPage, "description": "A page of iterations."},
        401: {"model": ApiError, "description": "Authentication is missing or invalid."},
        403: {"model": ApiError, "description": "The authenticated principal lacks access to this resource."},
        404: {"model": ApiError, "description": "Workspace, container, or work item was not found or is inaccessible."},
        500: {"model": ApiError, "description": "Unexpected server or upstream-provider failure."},
    },
    tags=["Discovery"],
    summary="List iterations, cycles, or sprints for a container",
    response_model_by_alias=True,
)
async def list_iterations(
    workspaceId: StrictStr = Path(..., description=""),
    containerId: StrictStr = Path(..., description=""),
    x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")] = Header(None, description="Adapter key selected by OpenNomos."),
    x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")] = Header(None, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.", min_length=1),
    board_id: Annotated[Optional[StrictStr], Field(description="Optional board identifier for providers where iterations are board-scoped.")] = Query(None, description="Optional board identifier for providers where iterations are board-scoped.", alias="boardId"),
    cursor: Annotated[Optional[StrictStr], Field(description="Opaque continuation token; omit for the first page.")] = Query(None, description="Opaque continuation token; omit for the first page.", alias="cursor"),
    limit: Optional[Annotated[int, Field(le=200, ge=1)]] = Query(50, description="", alias="limit", ge=1, le=200),
    x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")] = Header(None, description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults."),
    token_bearerAuth: TokenModel = Security(
        get_token_bearerAuth
    ),
) -> IterationPage:
    """Optional capability. Return the provider-native kind and dates; do not imply that all iterations have the same planning or workflow semantics."""
    if not BaseDiscoveryApi.subclasses:
        raise HTTPException(status_code=500, detail="Not implemented")
    return await BaseDiscoveryApi.subclasses[0]().list_iterations(workspaceId, containerId, x_pm_provider, x_pm_provider_credential, board_id, cursor, limit, x_pm_provider_base_url)


@router.get(
    "/workspaces/{workspaceId}/containers/{containerId}/milestones",
    responses={
        200: {"model": MilestonePage, "description": "A page of milestones."},
        401: {"model": ApiError, "description": "Authentication is missing or invalid."},
        403: {"model": ApiError, "description": "The authenticated principal lacks access to this resource."},
        404: {"model": ApiError, "description": "Workspace, container, or work item was not found or is inaccessible."},
        501: {"model": ApiError, "description": "The selected provider does not support this optional operation."},
        500: {"model": ApiError, "description": "Unexpected server or upstream-provider failure."},
    },
    tags=["Discovery"],
    summary="List milestones or releases for a container",
    response_model_by_alias=True,
)
async def list_milestones(
    workspaceId: StrictStr = Path(..., description=""),
    containerId: StrictStr = Path(..., description=""),
    x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")] = Header(None, description="Adapter key selected by OpenNomos."),
    x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")] = Header(None, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.", min_length=1),
    cursor: Annotated[Optional[StrictStr], Field(description="Opaque continuation token; omit for the first page.")] = Query(None, description="Opaque continuation token; omit for the first page.", alias="cursor"),
    limit: Optional[Annotated[int, Field(le=200, ge=1)]] = Query(50, description="", alias="limit", ge=1, le=200),
    x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")] = Header(None, description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults."),
    token_bearerAuth: TokenModel = Security(
        get_token_bearerAuth
    ),
) -> MilestonePage:
    """Optional capability; native milestone and release semantics are retained."""
    if not BaseDiscoveryApi.subclasses:
        raise HTTPException(status_code=500, detail="Not implemented")
    return await BaseDiscoveryApi.subclasses[0]().list_milestones(workspaceId, containerId, x_pm_provider, x_pm_provider_credential, cursor, limit, x_pm_provider_base_url)
