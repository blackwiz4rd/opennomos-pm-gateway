from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "pm_gateway.app:app",
        host=os.getenv("PM_GATEWAY_HOST", "127.0.0.1"),
        port=int(os.getenv("PM_GATEWAY_PORT", "8080")),
    )


if __name__ == "__main__":
    main()
