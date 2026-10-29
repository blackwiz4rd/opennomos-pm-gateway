from __future__ import annotations

import os


def main() -> None:
    try:
        import uvicorn
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on install extras
        raise SystemExit(
            "The pm-gateway HTTP service requires the 'server' extra. "
            "Install it with: pip install 'opennomos-pm-gateway[server]'"
        ) from exc

    uvicorn.run(
        "pm_gateway.app:app",
        host=os.getenv("PM_GATEWAY_HOST", "127.0.0.1"),
        port=int(os.getenv("PM_GATEWAY_PORT", "8080")),
    )


if __name__ == "__main__":
    main()
