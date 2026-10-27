"""Shared normalization helpers for PM providers.

These utilities convert provider-specific values into the normalized
representations used by :class:`NormalizedWorkItem`.
"""

from datetime import datetime
from typing import Any


# ── state category ──

# Map various state group/category names to the normalized set:
# backlog, unstarted, started, completed, cancelled
_STATE_CATEGORY_MAP: dict[str, str] = {
    # Plane state groups
    "backlog": "backlog",
    "unstarted": "unstarted",
    "started": "started",
    "completed": "completed",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    # Jira status categories
    "todo": "unstarted",
    "in progress": "started",
    "in-progress": "started",
    "done": "completed",
    # Generic
    "open": "unstarted",
    "closed": "completed",
    "resolved": "completed",
    # Planner (bucket names are free-form, but common defaults)
    "not started": "unstarted",
    "in progress": "started",
    "doing": "started",
    "done": "completed",
    "completed": "completed",
    "to do": "unstarted",
}


def normalize_state_category(group: str | None) -> str | None:
    """Map a provider state group/category to a normalized category.

    Returns one of: ``backlog``, ``unstarted``, ``started``, ``completed``,
    ``cancelled``. Returns ``None`` when the group is not recognized.
    """
    if not group:
        return None
    key = group.strip().lower()
    return _STATE_CATEGORY_MAP.get(key)


# ── priority value ──

# Map provider priority names to a numeric scale (0=none, 1=urgent, 2=high,
# 3=medium, 4=low). This follows the Linear convention.
_PRIORITY_VALUE_MAP: dict[str, int] = {
    "urgent": 1,
    "high": 2,
    "medium": 3,
    "normal": 3,
    "low": 4,
    "lowest": 4,
    "none": 0,
    "": 0,
}

# Jira priority names vary by instance; map common variants.
_JIRA_PRIORITY_MAP: dict[str, int] = {
    "highest": 1,
    "high": 2,
    "medium": 3,
    "low": 4,
    "lowest": 5,
}


def normalize_priority_value(priority: str | None, provider: str = "") -> int | None:
    """Map a provider priority name to a numeric value (0-4).

    Returns ``None`` when the priority is not recognized.
    """
    if not priority:
        return None
    key = priority.strip().lower()
    if provider == "jira":
        return _JIRA_PRIORITY_MAP.get(key)
    return _PRIORITY_VALUE_MAP.get(key)


# ── date formatting ──

def format_datetime(value: Any) -> str | None:
    """Format a datetime-like value to an ISO 8601 string.

    Handles ``datetime`` objects, ISO strings, and Jira-style dates
    (``YYYY-MM-DDTHH:MM:SS.mmm+ZZZZ``).
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        # Try to parse and re-format to ensure consistency
        try:
            # Handle Jira format: 2024-01-15T10:30:00.000+0000
            if len(value) > 20 and value[19] == ".":
                # Parse with milliseconds and timezone
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                return dt.isoformat()
            # Try standard ISO format
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt.isoformat()
        except (ValueError, IndexError):
            return value
    return str(value)


def format_date(value: Any) -> str | None:
    """Format a date-like value to an ISO date string (``YYYY-MM-DD``)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, str):
        # If it's a datetime string, extract just the date part
        if "T" in value:
            return value.split("T")[0]
        return value
    return str(value)


# ── URL construction ──

def build_work_item_url(base_url: str, project_id: str, work_item_id: str, provider: str) -> str | None:
    """Construct a web URL for a work item."""
    base = base_url.rstrip("/")
    if provider == "plane":
        return f"{base}/{project_id}/issues/{work_item_id}"
    if provider == "jira":
        return f"{base}/browse/{work_item_id}"
    if provider == "planner":
        return f"https://tasks.office.com/{project_id}/Home/Task/{work_item_id}"
    return None
