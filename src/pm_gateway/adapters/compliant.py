"""Passthrough adapter for software that implements the pm-gateway standard API."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import HTTPException

from pm_gateway.security import (
    expose_upstream_errors,
    provider_timeout_seconds,
    validate_provider_base_url,
)

logger = logging.getLogger("pm_gateway.adapters.compliant")


def _normalize_base_url(base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    if value and not value.lower().endswith("/pm/v1"):
        value = f"{value}/pm/v1"
    return value


def _error_detail(response: httpx.Response) -> str:
    """Return a bounded, controller-safe upstream error message."""
    if not expose_upstream_errors():
        return f"The PM provider returned HTTP {response.status_code}."

    detail = ""
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        detail = str(payload.get("message") or payload.get("detail") or "")
    if not detail:
        detail = response.text
    detail = " ".join(detail.split())
    return f"The PM provider returned HTTP {response.status_code}: {detail[:500]}"


class CompliantProvider:
    """HTTP client for a third-party implementation of the standard PM API.

    The remote service is expected to implement the OpenAPI contract published by
    pm-gateway. The gateway therefore only translates transport and auth
    concerns: it calls the remote standard endpoints and returns their payloads
    unchanged.
    """

    provider_key = "compliant"

    def __init__(
        self,
        credential: str,
        base_url: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._credential = credential
        validate_provider_base_url("compliant", base_url)
        self._base_url = _normalize_base_url(base_url or "")
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def invalidate(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as exc:  # pragma: no cover - best effort
                logger.debug("Could not close compliant provider client: %s", type(exc).__name__)
            self._client = None

    def _client_for(self) -> httpx.AsyncClient:
        if self._client is None:
            headers = {}
            if self._credential:
                headers["Authorization"] = f"Bearer {self._credential}"
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers=headers,
                timeout=provider_timeout_seconds(),
                follow_redirects=False,
                transport=self._transport,
            )
        return self._client

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
        headers: dict[str, str] | None = None,
        response_headers: dict[str, str] | None = None,
    ) -> Any:
        cleaned_params = {
            key: value for key, value in (params or {}).items() if value is not None
        }
        client = self._client_for()
        response = await client.request(
            method,
            path,
            params=cleaned_params,
            json=json,
            headers=headers or {},
        )
        if response.status_code >= 400:
            detail = _error_detail(response)
            error_headers = {}
            retry_after = response.headers.get("Retry-After")
            if retry_after is not None:
                error_headers["Retry-After"] = retry_after
            raise HTTPException(
                status_code=response.status_code,
                detail=detail,
                headers=error_headers or None,
            )
        if response_headers is not None:
            for name in ("etag", "retry-after"):
                value = response.headers.get(name)
                if value is not None:
                    response_headers[name] = value
        if not response.content:
            return None
        return response.json()
