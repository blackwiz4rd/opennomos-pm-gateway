# coding: utf-8

from typing import ClassVar, Dict, List, Tuple  # noqa: F401

from pydantic import Field, field_validator
from typing import Optional
from typing_extensions import Annotated
from pm_gateway.generated.models.api_error import ApiError
from pm_gateway.generated.models.transition_request import TransitionRequest
from pm_gateway.generated.models.work_item import WorkItem
from pm_gateway.generated.models.workflow_state_page import WorkflowStatePage
from pm_gateway.generated.security_api import get_token_bearerAuth

class BaseWorkflowApi:
    subclasses: ClassVar[Tuple] = ()

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        BaseWorkflowApi.subclasses = BaseWorkflowApi.subclasses + (cls,)
    async def list_workflow_states(
        self,
        workspaceId: str,
        containerId: str,
        x_pm_provider: Annotated[str, Field(description="Adapter key selected by OpenNomos.")],
        x_pm_provider_credential: Annotated[str, Field(min_length=1, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira/monday.com/Asana use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")],
        board_id: Annotated[Optional[str], Field(description="Optional provider-native board ID used to scope workflow states.")],
        x_pm_provider_base_url: Annotated[Optional[str], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane, Planner, monday.com, and Asana use their documented defaults.")],
        x_pm_provider_mode: Annotated[Optional[str], Field(description="Optional provider implementation mode. Used by providers with more than one backend (for example Jira's `sdk` and `http-client` modes). Defaults to `http-client`.")],
    ) -> WorkflowStatePage:
        ...


    async def transition_work_item(
        self,
        workspaceId: str,
        workItemId: str,
        x_pm_provider: Annotated[str, Field(description="Adapter key selected by OpenNomos.")],
        x_pm_provider_credential: Annotated[str, Field(min_length=1, description="Provider credential value held by OpenNomos and forwarded for this request only. Plane/Jira/monday.com/Asana use their API token form; Planner uses tenant_id:client_id:client_secret. Gateways and intermediaries must not persist or log this header. TLS is required.")],
        transition_request: Optional[TransitionRequest],
        container_id: Annotated[Optional[str], Field(description="Optional native container context required by some providers to retrieve a work item. OpenNomos should provide it when known.")],
        if_match: Annotated[Optional[str], Field(description="Opaque revision token, when supported.")],
        x_pm_provider_base_url: Annotated[Optional[str], Field(description="Optional provider API base URL from the OpenNomos credential record. Jira requires it; Plane, Planner, monday.com, and Asana use their documented defaults.")],
        x_pm_provider_mode: Annotated[Optional[str], Field(description="Optional provider implementation mode. Used by providers with more than one backend (for example Jira's `sdk` and `http-client` modes). Defaults to `http-client`.")],
    ) -> WorkItem:
        """Optional capability. The provider validates that the requested transition is allowed in the current workflow. A provider may expose available transitions in the workflow-state representation."""
        ...
