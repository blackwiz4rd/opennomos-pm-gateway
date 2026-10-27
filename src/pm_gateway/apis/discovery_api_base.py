# coding: utf-8

from typing import ClassVar, Dict, List, Tuple  # noqa: F401

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

class BaseDiscoveryApi:
    subclasses: ClassVar[Tuple] = ()

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        BaseDiscoveryApi.subclasses = BaseDiscoveryApi.subclasses + (cls,)
    async def get_capabilities(
        self,
        x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")],
        x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")],
        x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")],
    ) -> Capabilities:
        ...


    async def list_workspaces(
        self,
        x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")],
        x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")],
        cursor: Annotated[Optional[StrictStr], Field(description="Opaque continuation token; omit for the first page.")],
        limit: Optional[Annotated[int, Field(le=200, ge=1)]],
        x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")],
        x_pm_workspace_slugs: Annotated[Optional[StrictStr], Field(description="Comma-separated workspace slugs configured by OpenNomos for this credential. Used only by workspace discovery; the gateway stores none.")],
    ) -> WorkspacePage:
        ...


    async def list_containers(
        self,
        workspaceId: StrictStr,
        x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")],
        x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")],
        cursor: Annotated[Optional[StrictStr], Field(description="Opaque continuation token; omit for the first page.")],
        limit: Optional[Annotated[int, Field(le=200, ge=1)]],
        x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")],
    ) -> ContainerPage:
        """Containers represent provider-native organizational resources such as projects, boards, plans, lists, repositories, or sheets. The kind is provider-defined; clients must not assume all kinds have identical semantics."""
        ...


    async def list_boards(
        self,
        workspaceId: StrictStr,
        containerId: StrictStr,
        x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")],
        x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")],
        cursor: Annotated[Optional[StrictStr], Field(description="Opaque continuation token; omit for the first page.")],
        limit: Optional[Annotated[int, Field(le=200, ge=1)]],
        x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")],
    ) -> ContainerPage:
        """Optional provider capability. Providers without a board concept return an empty page and advertise boards.list as unsupported."""
        ...


    async def get_board_scope(
        self,
        workspaceId: StrictStr,
        containerId: StrictStr,
        x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")],
        x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")],
        board_id: Optional[StrictStr],
        iteration_ids: Optional[List[StrictStr]],
        x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")],
    ) -> BoardScope:
        """Compatibility extension for providers with board/backlog semantics."""
        ...


    async def list_custom_field_definitions(
        self,
        workspaceId: StrictStr,
        containerId: StrictStr,
        x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")],
        x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")],
        cursor: Annotated[Optional[StrictStr], Field(description="Opaque continuation token; omit for the first page.")],
        limit: Optional[Annotated[int, Field(le=200, ge=1)]],
        x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")],
    ) -> CustomFieldDefinitionPage:
        """Optional capability. Definitions may be container-, work-item-type-, or workspace-scoped. Implementations should report the effective scope and must use stable field IDs rather than display names as identifiers."""
        ...


    async def list_iterations(
        self,
        workspaceId: StrictStr,
        containerId: StrictStr,
        x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")],
        x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")],
        board_id: Annotated[Optional[StrictStr], Field(description="Optional board identifier for providers where iterations are board-scoped.")],
        cursor: Annotated[Optional[StrictStr], Field(description="Opaque continuation token; omit for the first page.")],
        limit: Optional[Annotated[int, Field(le=200, ge=1)]],
        x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")],
    ) -> IterationPage:
        """Optional capability. Return the provider-native kind and dates; do not imply that all iterations have the same planning or workflow semantics."""
        ...


    async def list_milestones(
        self,
        workspaceId: StrictStr,
        containerId: StrictStr,
        x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")],
        x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")],
        cursor: Annotated[Optional[StrictStr], Field(description="Opaque continuation token; omit for the first page.")],
        limit: Optional[Annotated[int, Field(le=200, ge=1)]],
        x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")],
    ) -> MilestonePage:
        """Optional capability; native milestone and release semantics are retained."""
        ...
