from __future__ import annotations

from typing import Any

from pm_gateway.adapters.normalization import build_work_item_url, normalize_state_category
from pm_gateway.security import safe_url
from pm_gateway.generated.models.container import Container
from pm_gateway.generated.models.container_ref import ContainerRef
from pm_gateway.generated.models.estimate_value import EstimateValue
from pm_gateway.generated.models.iteration_ref import IterationRef
from pm_gateway.generated.models.label_ref import LabelRef
from pm_gateway.generated.models.mapping_metadata import MappingMetadata
from pm_gateway.generated.models.named_ref import NamedRef
from pm_gateway.generated.models.principal_ref import PrincipalRef
from pm_gateway.generated.models.priority_value import PriorityValue
from pm_gateway.generated.models.source_metadata import SourceMetadata
from pm_gateway.generated.models.work_item import WorkItem
from pm_gateway.generated.models.work_item_dates import WorkItemDates
from pm_gateway.generated.models.work_item_lifecycle import WorkItemLifecycle
from pm_gateway.generated.models.work_item_ref import WorkItemRef
from pm_gateway.generated.models.work_item_relationship import WorkItemRelationship
from pm_gateway.generated.models.workflow_state_ref import WorkflowStateRef

_PRIORITY_LEVELS = {
    "urgent": "highest",
    "critical": "highest",
    "highest": "highest",
    "high": "high",
    "important": "high",
    "normal": "medium",
    "medium": "medium",
    "low": "low",
    "lowest": "lowest",
    "none": "none",
}


def _mapping(quality: str, note: str | None = None) -> MappingMetadata:
    return MappingMetadata(quality=quality, note=note)


def _description(value: Any) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    if isinstance(value, str):
        return value, "text/plain"
    if isinstance(value, dict):
        chunks: list[str] = []

        def collect(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("type") == "text" and isinstance(node.get("text"), str):
                    chunks.append(node["text"])
                for child in node.values():
                    collect(child)
            elif isinstance(node, list):
                for child in node:
                    collect(child)

        collect(value)
        return "\n".join(chunks), "text/plain"
    return str(value), "text/plain"


def as_container(raw: dict[str, Any], provider: str) -> Container:
    identifier = str(raw.get("id", ""))
    kind = "plan" if provider == "planner" else "project"
    extensions: dict[str, Any] = {}
    if raw.get("identifier") is not None:
        extensions[f"{provider}:native"] = {"identifier": raw.get("identifier")}
    return Container(
        id=identifier,
        kind=kind,
        name=str(raw.get("name") or identifier),
        url=raw.get("url"),
        extensions=extensions or None,
    )


def as_board(raw: dict[str, Any], provider: str) -> Container:
    identifier = str(raw.get("id", ""))
    return Container(
        id=identifier,
        kind="board",
        name=str(raw.get("name") or identifier),
        extensions={f"{provider}:native": {"type": raw.get("type")}},
    )


def as_iteration(raw: dict[str, Any], provider: str) -> IterationRef:
    identifier = str(raw.get("id", ""))
    state = raw.get("state")
    return IterationRef(
        id=identifier,
        name=raw.get("name"),
        kind="backlog" if identifier == "backlog" else "sprint",
        state=state,
        starts_at=raw.get("startDate") or raw.get("start_date"),
        ends_at=raw.get("endDate") or raw.get("end_date"),
        extensions={f"{provider}:native": raw} if raw else None,
    )


def as_work_item(
    raw: dict[str, Any],
    *,
    provider: str,
    workspace_id: str,
    container_id: str,
    base_url: str | None,
) -> WorkItem:
    """Project the existing OpenNomos provider shape onto the gateway contract."""
    identifier = str(raw.get("id") or "")
    native_priority = raw.get("priority")
    priority_text = str(native_priority).strip().lower() if native_priority is not None else ""
    common_priority = _PRIORITY_LEVELS.get(priority_text)
    if provider == "planner" and priority_text.isdigit():
        planner_priority = int(priority_text)
        if planner_priority in {0, 1}:
            common_priority = "highest"
        elif planner_priority in {2, 3, 4}:
            common_priority = "high"
        elif planner_priority in {5, 6, 7}:
            common_priority = "medium"
        elif planner_priority in {8, 9, 10}:
            common_priority = "low"
    priority = None
    if native_priority is not None:
        priority = PriorityValue(
            level=common_priority,
            native_value=native_priority,
            native_scale=("planner-0-10" if provider == "planner" else f"{provider}-native"),
            mapping=_mapping(
                "mapped" if common_priority else "unmapped",
                None if common_priority else "Native priority has no common mapping.",
            ),
        )

    state_name = raw.get("state")
    state_category = raw.get("state_category") or normalize_state_category(state_name)
    status = None
    if state_name is not None or raw.get("state_id") is not None:
        status = WorkflowStateRef(
            id=str(raw["state_id"]) if raw.get("state_id") is not None else None,
            name=str(state_name or raw.get("state_id") or "Unknown"),
            category=state_category,
            mapping=_mapping("mapped" if state_category else "unmapped"),
        )

    assignees: list[PrincipalRef] = []
    if raw.get("assignee_id") is not None:
        assignees.append(
            PrincipalRef(
                id=str(raw["assignee_id"]),
                display_name=raw.get("assignee"),
            )
        )
    creator = None
    if raw.get("creator_id") is not None:
        creator = PrincipalRef(
            id=str(raw["creator_id"]),
            display_name=raw.get("creator"),
        )

    dates_data = {
        "start_date": raw.get("start_date"),
        "due_date": raw.get("due_date"),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "completed_at": raw.get("completed_at"),
    }
    present_dates = {key: value for key, value in dates_data.items() if value is not None}
    dates = WorkItemDates(**present_dates) if present_dates else None

    estimate = None
    if raw.get("estimate") is not None:
        estimate = EstimateValue(
            value=raw.get("estimate"),
            unit="story-points" if provider == "plane" else "provider-defined",
            scale=f"{provider}-estimate",
            native_value=raw.get("estimate"),
            mapping=_mapping("exact" if provider == "plane" else "inferred"),
        )

    labels = [LabelRef(name=str(label)) for label in (raw.get("labels") or []) if label]
    versions = [NamedRef(name=str(value)) for value in (raw.get("fix_versions") or []) if value]
    components = [NamedRef(name=str(value)) for value in (raw.get("components") or []) if value]
    item_type = raw.get("item_type")
    parent_id = raw.get("parent_id") or raw.get("epic_id")
    parent = WorkItemRef(id=str(parent_id)) if parent_id else None

    relationships: list[WorkItemRelationship] = []
    for key, relation in (
        ("blocks", "blocks"),
        ("blocked_by", "blocked_by"),
        ("related", "relates_to"),
        ("duplicates", "duplicates"),
    ):
        relationships.extend(
            WorkItemRelationship(
                type=relation,
                target={"id": str(target)},
                native_type=None,
                mapping=_mapping("exact"),
            )
            for target in (raw.get(key) or [])
        )

    resource_type = {"jira": "issue", "planner": "task", "plane": "work-item"}[provider]
    url_base = base_url
    if provider == "plane" and not url_base:
        url_base = "https://app.plane.so"
    native_url = build_work_item_url(url_base or "", container_id, identifier, provider)
    extensions: dict[str, Any] = {}
    extra: dict[str, Any] = {}
    if raw.get("epic_name"):
        extra["epicName"] = raw["epic_name"]
    if raw.get("epic_id"):
        extra["epicId"] = raw["epic_id"]
    if raw.get("team_id"):
        extra["teamId"] = raw["team_id"]
    if raw.get("team_name"):
        extra["teamName"] = raw["team_name"]
    if extra:
        extensions[f"{provider}:native"] = extra

    description, inferred_content_type = _description(raw.get("description"))
    description_content_type = raw.get("description_content_type") or inferred_content_type
    return WorkItem(
        id=identifier,
        identifier=str(raw["identifier"]) if raw.get("identifier") is not None else None,
        title=str(raw.get("title") or ""),
        description=description,
        description_content_type=description_content_type,
        url=safe_url(raw.get("url")) or native_url,
        status=status,
        priority=priority,
        estimate=estimate,
        assignees=assignees,
        creator=creator,
        memberships=[
            ContainerRef(
                id=container_id,
                kind="project" if provider != "planner" else "plan",
                name=raw.get("project_name"),
            )
        ],
        lifecycle=WorkItemLifecycle(archived=False, deleted=False),
        parent=parent,
        dates=dates,
        labels=labels,
        components=components,
        versions=versions,
        item_type=NamedRef(name=str(item_type)) if item_type else None,
        relationships=relationships,
        custom_fields=[
            {"id": f"{provider}:{key}", "name": key, "value": value}
            for key, value in (raw.get("custom_fields") or {}).items()
        ],
        source=SourceMetadata(
            provider=provider,
            resource_type=resource_type,
            native_url=native_url,
        ),
        extensions=extensions or None,
    )
