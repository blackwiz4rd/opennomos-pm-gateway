"""In-process PM gateway SDK; no gateway process or network hop is involved."""

from __future__ import annotations

from typing import Any

from pm_gateway.impl.provider_api import PMGateway as _ProviderGateway


class PMGateway:
    """Call PM provider operations directly from Python.

    Provider credentials are supplied per call and are never persisted. The
    returned values are the same generated models serialized by the HTTP API.
    """

    def __init__(self) -> None:
        self._gateway = _ProviderGateway()

    async def execute(
        self,
        operation: str,
        *,
        provider: str,
        credential: str,
        base_url: str | None = None,
        **params: Any,
    ) -> Any:
        """Execute a generated API operation directly against its adapter."""
        return await self._gateway.execute(
            operation,
            provider=provider,
            credential=credential,
            base_url=base_url,
            **params,
        )


__all__ = ["PMGateway"]
