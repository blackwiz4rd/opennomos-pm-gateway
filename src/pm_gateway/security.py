"""Security helpers for provider destinations, identifiers, URLs, and tokens.

The gateway accepts provider credentials per call. A caller-supplied provider
base URL is therefore security-sensitive: it decides where those credentials
are sent. These helpers keep that boundary explicit and fail closed.
"""

from __future__ import annotations

import asyncio
import hmac
import ipaddress
import os
import re
import socket
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlsplit

# App-only Microsoft Graph tokens must only be sent to Microsoft Graph. Add
# sovereign-cloud endpoints here rather than allowing arbitrary Planner hosts.
PLANNER_GRAPH_HOSTS = frozenset(
    {
        "graph.microsoft.com",
        "graph.microsoft.us",
        "dod-graph.microsoft.us",
        "microsoftgraph.chinacloudapi.cn",
    }
)

# Known SaaS endpoints. Self-hosted and other providers must be added to
# PM_GATEWAY_ALLOWED_PROVIDER_HOSTS; compliant providers have no default host.
_DEFAULT_ALLOWED_HOSTS = {
    "jira": ("*.atlassian.net",),
    "plane": ("api.plane.so",),
    "planner": tuple(PLANNER_GRAPH_HOSTS),
    "compliant": (),
}

_NATIVE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_DNS_TIMEOUT_SECONDS = 5.0


def provider_timeout_seconds() -> float:
    """Return the provider request timeout, defaulting to 30 seconds."""
    raw = os.getenv("PM_GATEWAY_PROVIDER_TIMEOUT_SECONDS", "30")
    try:
        value = float(raw)
    except ValueError:
        return 30.0
    return value if value > 0 else 30.0


def expose_upstream_errors() -> bool:
    """Whether provider error bodies may be returned to the caller.

    Disabled by default: raw upstream bodies can contain internal service data
    and are especially risky when the destination was chosen by the caller.
    """
    return _env_flag("PM_GATEWAY_EXPOSE_UPSTREAM_ERRORS")


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _allowed_hosts() -> set[str]:
    raw = os.getenv("PM_GATEWAY_ALLOWED_PROVIDER_HOSTS", "")
    return {
        item.strip().lower().rstrip(".")
        for item in raw.split(",")
        if item.strip()
    }


def _host_allowed(host: str, allowed: set[str]) -> bool:
    host = host.lower().rstrip(".")
    if host in allowed:
        return True
    for pattern in allowed:
        if pattern.startswith("*."):
            suffix = pattern[1:]
            if host.endswith(suffix) and host != suffix.lstrip("."):
                return True
    return False


def _public_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    address = ipaddress.ip_address(value.split("%", 1)[0])
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return address.ipv4_mapped
    return address


def _reject_non_global_literal(host: str) -> None:
    try:
        address = _public_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise ValueError("Provider base URL must target a public host.")


def validate_provider_base_url(provider: str, base_url: str | None) -> str | None:
    """Validate and normalize a provider base URL.

    Rules:
    - ``jira`` and ``compliant`` require a base URL; the others have defaults.
    - HTTPS is required unless ``PM_GATEWAY_ALLOW_INSECURE_HTTP`` is enabled.
    - Embedded credentials, queries, and fragments are rejected.
    - Planner is restricted to known Microsoft Graph endpoints.
    - Non-allowlisted hosts must be public addresses; private, loopback,
      link-local, and metadata addresses are rejected.
    - ``PM_GATEWAY_ALLOWED_PROVIDER_HOSTS`` explicitly trusts hosts (including
      private/self-hosted ones) and supports ``*.example.com`` wildcards.
    """
    provider = (provider or "").strip().lower()
    if base_url is None or not base_url.strip():
        if provider == "jira":
            raise ValueError("X-PM-Provider-Base-Url is required for Jira.")
        if provider == "compliant":
            raise ValueError("X-PM-Provider-Base-Url is required for compliant providers.")
        return None

    value = base_url.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("Provider base URL must be an absolute HTTPS URL.")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("Provider base URL has an invalid port.") from exc
    if parsed.username or parsed.password:
        raise ValueError("Provider base URL must not contain embedded credentials.")
    if parsed.query or parsed.fragment:
        raise ValueError("Provider base URL must not contain a query or fragment.")

    if provider == "planner" and parsed.scheme != "https":
        raise ValueError("Microsoft Planner base URL must use HTTPS.")
    if parsed.scheme != "https" and not _env_flag("PM_GATEWAY_ALLOW_INSECURE_HTTP"):
        raise ValueError("Provider base URL must use HTTPS.")

    host = parsed.hostname.lower().rstrip(".")
    allowed = _allowed_hosts()
    default_allowed = set(_DEFAULT_ALLOWED_HOSTS.get(provider, ()))
    if provider == "planner":
        if host not in PLANNER_GRAPH_HOSTS:
            raise ValueError(
                "Microsoft Planner base URL must target a Microsoft Graph endpoint."
            )
    elif not _host_allowed(host, default_allowed | allowed):
        _reject_non_global_literal(host)
        raise ValueError(
            "Provider base URL host is not allowlisted. "
            "Add it to PM_GATEWAY_ALLOWED_PROVIDER_HOSTS."
        )

    return value


async def validate_provider_destination(provider: str, base_url: str | None) -> None:
    """Reject destinations that resolve to non-public addresses.

    Allowlisted hosts and known Planner Graph endpoints are trusted. DNS
    resolution is best-effort SSRF protection; production deployments should
    also enforce egress policy at the network layer to cover DNS rebinding.
    """
    if not base_url:
        return
    parsed = urlsplit(base_url)
    host = parsed.hostname
    if not host:
        return
    host = host.lower().rstrip(".")

    if provider == "planner" and host in PLANNER_GRAPH_HOSTS:
        return
    if _host_allowed(
        host,
        _allowed_hosts() | set(_DEFAULT_ALLOWED_HOSTS.get(provider, ())),
    ):
        return

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = await asyncio.wait_for(
            asyncio.to_thread(
                socket.getaddrinfo,
                host,
                port,
                0,
                socket.SOCK_STREAM,
            ),
            timeout=_DNS_TIMEOUT_SECONDS,
        )
    except (OSError, asyncio.TimeoutError) as exc:
        raise ValueError("Provider base URL host could not be resolved.") from exc

    addresses = {info[4][0] for info in infos if info[4]}
    if not addresses:
        raise ValueError("Provider base URL host did not resolve to an address.")
    for raw in addresses:
        try:
            address = _public_address(raw)
        except ValueError:
            continue
        if not address.is_global:
            raise ValueError("Provider base URL must target a public host.")


def validate_native_id(value: Any, field: str) -> str:
    """Validate an identifier that will be placed in a provider URL path.

    Native provider IDs are ASCII identifiers. Rejecting spaces, slashes,
    percent escapes, query/fragment delimiters, and dot segments prevents
    path traversal and query injection in provider SDKs.
    """
    text = str(value)
    if not text or len(text) > 256 or not _NATIVE_ID_RE.fullmatch(text):
        raise ValueError(
            f"{field} must be a provider-native identifier containing only "
            "letters, digits, dots, underscores, or hyphens."
        )
    return text


def validate_native_identifiers(provider: str, params: Mapping[str, Any]) -> None:
    """Validate all caller-supplied IDs used by native provider adapters."""
    for field in (
        "containerId",
        "container_id",
        "workItemId",
        "work_item_id",
        "board_id",
        "boardId",
    ):
        value = params.get(field)
        if value is not None:
            validate_native_id(value, field)

    iteration_ids = params.get("iteration_ids", params.get("iterationIds"))
    if iteration_ids is not None:
        values = [iteration_ids] if isinstance(iteration_ids, str) else iteration_ids
        for value in values:
            validate_native_id(value, "iteration_ids")

    if provider in {"plane", "planner"}:
        workspace_id = params.get("workspaceId", params.get("workspace_id"))
        if workspace_id is not None:
            validate_native_id(workspace_id, "workspaceId")


def path_segment(value: Any) -> str:
    """Percent-encode a path segment and reject dot-segment traversal."""
    text = str(value)
    if not text or text in {".", ".."}:
        raise ValueError("Path identifiers must not be empty or a dot segment.")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in text):
        raise ValueError("Path identifiers must not contain control characters.")
    return quote(text, safe="")


def safe_url(value: Any) -> str | None:
    """Return an absolute HTTP(S) URL, or None for unsafe/credentialed URLs."""
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = value.strip()
    parsed = urlsplit(candidate)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username or parsed.password:
        return None
    return candidate


def token_matches(supplied: str, configured: str) -> bool:
    """Constant-time bearer-token comparison that accepts non-ASCII input."""
    return hmac.compare_digest(
        supplied.encode("utf-8"),
        configured.encode("utf-8"),
    )
