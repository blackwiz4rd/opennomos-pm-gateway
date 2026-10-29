"""Normalized work item model for OpenNomos PM providers.

This model provides a unified representation of work items across all
supported PM tools (Plane, Jira, Linear, ...). Each provider converts its
native format into this normalized shape before returning data to consumers.
"""

from typing import Any

from pydantic import BaseModel, Field


class NormalizedWorkItem(BaseModel):
    """A work item normalized across all PM providers."""

    model_config = {"extra": "allow"}

    # Identification
    id: str
    identifier: str | None = None
    title: str = ""
    description: str | None = None
    description_content_type: str | None = None
    url: str | None = None

    # Status
    state: str | None = None
    state_id: str | None = None
    state_category: str | None = None

    # Priority
    priority: str | None = None
    priority_value: int | None = None

    # Estimation
    estimate: float | None = None
    time_estimate: int | None = None
    time_spent: int | None = None

    # Dates
    start_date: str | None = None
    due_date: str | None = None
    completed_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    # People
    assignee: str | None = None
    assignee_id: str | None = None
    creator: str | None = None
    creator_id: str | None = None

    # Hierarchy
    parent_id: str | None = None
    epic_id: str | None = None
    epic_name: str | None = None
    project_id: str | None = None
    project_name: str | None = None
    team_id: str | None = None
    team_name: str | None = None

    # Classification
    labels: list[str] = Field(default_factory=list)
    item_type: str | None = None
    components: list[str] = Field(default_factory=list)
    fix_versions: list[str] = Field(default_factory=list)
    sprint_id: str | None = None
    sprint_name: str | None = None
    milestone_id: str | None = None
    milestone_name: str | None = None

    # Relationships
    blocks: list[str] = Field(default_factory=list)
    blocked_by: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)
    duplicates: list[str] = Field(default_factory=list)

    # Custom fields
    custom_fields: dict[str, Any] = Field(default_factory=dict)

    # Provider metadata
    provider: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)
