from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator
from urllib.parse import urlsplit

from pm_gateway.adapters.jira import JiraProvider
from pm_gateway.adapters.plane import PlaneProvider
from pm_gateway.adapters.planner import PlannerProvider

SUPPORTED_PROVIDERS = frozenset({"plane", "jira", "planner"})


def _validate_base_url(provider: str, base_url: str | None) -> str | None:
    if base_url is None or not base_url.strip():
        if provider == "jira":
            raise ValueError("X-PM-Provider-Base-Url is required for Jira.")
        return None
    value = base_url.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("Provider base URL must be an absolute HTTP(S) URL.")
    if parsed.username or parsed.password:
        raise ValueError("Provider base URL must not contain embedded credentials.")
    if parsed.query or parsed.fragment:
        raise ValueError("Provider base URL must not contain a query or fragment.")
    return value


def validate_provider_context(provider: str, credential: str, base_url: str | None) -> None:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported PM provider: {provider}")
    if not credential:
        raise ValueError("Provider credential must not be empty.")
    _validate_base_url(provider, base_url)


@asynccontextmanager
async def open_provider(
    provider: str,
    credential: str,
    base_url: str | None = None,
) -> AsyncIterator[object]:
    """Create a request-scoped adapter and release credential-bearing clients."""
    validate_provider_context(provider, credential, base_url)
    validated_base_url = _validate_base_url(provider, base_url)
    adapter_type = {
        "plane": PlaneProvider,
        "jira": JiraProvider,
        "planner": PlannerProvider,
    }[provider]
    adapter = adapter_type(credential, validated_base_url)
    try:
        yield adapter
    finally:
        cleanup = getattr(adapter, "invalidate", None)
        if cleanup is not None:
            await cleanup()
