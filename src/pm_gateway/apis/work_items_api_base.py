# coding: utf-8

from typing import ClassVar, Dict, List, Tuple  # noqa: F401

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

class BaseWorkItemsApi:
    subclasses: ClassVar[Tuple] = ()

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        BaseWorkItemsApi.subclasses = BaseWorkItemsApi.subclasses + (cls,)
    async def list_work_items(
        self,
        workspaceId: StrictStr,
        containerId: StrictStr,
        x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")],
        x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")],
        cursor: Annotated[Optional[StrictStr], Field(description="Opaque continuation token; omit for the first page.")],
        limit: Optional[Annotated[int, Field(le=200, ge=1)]],
        changed_since: Annotated[Optional[datetime], Field(description="Return items updated at or after this RFC 3339 timestamp.")],
        state_id: Annotated[Optional[StrictStr], Field(description="Provider-native workflow state ID.")],
        assignee_id: Annotated[Optional[StrictStr], Field(description="Provider-scoped principal ID.")],
        archived: Annotated[Optional[StrictBool], Field(description="Filter for archived items; omission means no archive filter.")],
        deleted: Annotated[Optional[StrictBool], Field(description="Filter for deleted items when the provider exposes deleted records. A provider that cannot list tombstones must report that capability as unsupported rather than treating deleted=false as a complete sync.")],
        query: Annotated[Optional[StrictStr], Field(description="Optional provider-supported text search.")],
        sort: Annotated[Optional[StrictStr], Field(description="Sort expression supported by the implementation. Supported fields must be declared in capabilities; a leading minus indicates descending order.")],
        board_id: Annotated[Optional[StrictStr], Field(description="Provider-native board identifier. Optional on providers without board concepts.")],
        iteration_ids: Annotated[Optional[List[StrictStr]], Field(description="Provider-native iteration identifiers, such as Jira sprint IDs.")],
        x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")],
    ) -> WorkItemPage:
        """Unsupported filters must be reported through capabilities or rejected; implementations must not silently return results that ignore a filter. A cursor is opaque to clients and is only valid for the query context documented by the implementation."""
        ...


    async def get_work_item(
        self,
        workspaceId: StrictStr,
        workItemId: StrictStr,
        x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")],
        x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")],
        container_id: Annotated[Optional[StrictStr], Field(description="Optional native container context required by some providers to retrieve a work item. OpenNomos should provide it when known.")],
        x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")],
    ) -> WorkItem:
        ...


    async def update_work_item(
        self,
        workspaceId: StrictStr,
        workItemId: StrictStr,
        x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")],
        x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")],
        work_item_patch: WorkItemPatch,
        container_id: Annotated[Optional[StrictStr], Field(description="Optional native container context required by some providers to retrieve a work item. OpenNomos should provide it when known.")],
        if_match: Annotated[Optional[StrictStr], Field(description="Opaque revision token returned by GET, when supported.")],
        x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")],
    ) -> WorkItem:
        """This operation is available only when the update capability is advertised. Omitted properties are unchanged. A null value requests clearing a field only when that field is nullable and writable. State changes that require a workflow action use the transitions operation."""
        ...


    async def list_work_item_comments(
        self,
        workspaceId: StrictStr,
        workItemId: StrictStr,
        x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")],
        x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")],
        cursor: Annotated[Optional[StrictStr], Field(description="Opaque continuation token; omit for the first page.")],
        limit: Optional[Annotated[int, Field(le=200, ge=1)]],
        x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")],
    ) -> CommentPage:
        """Optional capability. Providers may expose comments as stories, updates, notes, or discussions."""
        ...


    async def list_work_item_attachments(
        self,
        workspaceId: StrictStr,
        workItemId: StrictStr,
        x_pm_provider: Annotated[StrictStr, Field(description="Adapter key selected by OpenNomos.")],
        x_pm_provider_credential: Annotated[str, Field(min_length=1, strict=True, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")],
        cursor: Annotated[Optional[StrictStr], Field(description="Opaque continuation token; omit for the first page.")],
        limit: Optional[Annotated[int, Field(le=200, ge=1)]],
        x_pm_provider_base_url: Annotated[Optional[StrictStr], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane and Planner use their documented defaults.")],
    ) -> AttachmentPage:
        """Optional capability. File content download is outside this listing operation."""
        ...
