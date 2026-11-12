"""Provider adapters for the PM gateway."""
from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from pm_gateway.adapters.asana import AsanaProvider
from pm_gateway.adapters.basecamp import BasecampProvider
from pm_gateway.adapters.clickup import ClickUpProvider
from pm_gateway.adapters.compliant import CompliantProvider
from pm_gateway.adapters.github import GitHubProvider
from pm_gateway.adapters.jira import JiraProvider
from pm_gateway.adapters.monday import MondayProvider
from pm_gateway.adapters.plane import PlaneProvider
from pm_gateway.adapters.planner import PlannerProvider
from pm_gateway.adapters.trello import TrelloProvider
from pm_gateway.security import (
    validate_provider_base_url,
    validate_provider_destination,
)

SUPPORTED_PROVIDERS = frozenset(
    {"plane", "jira", "planner", "monday", "asana", "clickup", "trello", "basecamp", "github", "compliant"}
)


def normalize_provider(provider: str) -> str:
    return (provider or "").strip().lower()


def validate_provider_context(provider: str, credential: str, base_url: str | None) -> None:
    provider = normalize_provider(provider)
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported PM provider: {provider}")
    if not credential:
        raise ValueError("Provider credential must not be empty.")
    validate_provider_base_url(provider, base_url)


@asynccontextmanager
async def open_provider(
    provider: str,
    credential: str,
    base_url: str | None = None,
    *,
    mode: str | None = None,
) -> AsyncIterator[object]:
    """Create a request-scoped adapter and release credential-bearing clients."""
    normalized = normalize_provider(provider)
    validate_provider_context(normalized, credential, base_url)
    validated_base_url = validate_provider_base_url(normalized, base_url)
    await validate_provider_destination(normalized, validated_base_url)
    adapter_type = {
        "plane": PlaneProvider,
        "planner": PlannerProvider,
        "monday": MondayProvider,
        "asana": AsanaProvider,
        "clickup": ClickUpProvider,
        "trello": TrelloProvider,
        "basecamp": BasecampProvider,
        "github": GitHubProvider,
        "compliant": CompliantProvider,
    }[normalized]
    if normalized == "jira":
        mode_value = (mode or "http-client").strip().lower()
        if mode_value == "sdk":
            adapter_type = JiraProvider
        else:
            from pm_gateway.adapters.jira_http import JiraHttpProvider

            adapter_type = JiraHttpProvider
    adapter = adapter_type(credential, validated_base_url)
    try:
        yield adapter
    finally:
        cleanup = getattr(adapter, "invalidate", None)
        if cleanup is not None:
            await cleanup()
