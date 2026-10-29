from __future__ import annotations

import hmac
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from pm_gateway._version import __version__
from pm_gateway.generated.apis.discovery_api import router as discovery_router
from pm_gateway.generated.apis.work_items_api import router as work_items_router
from pm_gateway.generated.apis.workflow_api import router as workflow_router
from pm_gateway.generated.models.api_error import ApiError

app = FastAPI(
    title="Project Management Interoperability API",
    description=(
        "Vendor-neutral API for discovering and exchanging work items across "
        "project-management providers."
    ),
    version=__version__,
)
app.include_router(discovery_router, prefix="/pm/v1")
app.include_router(work_items_router, prefix="/pm/v1")
app.include_router(workflow_router, prefix="/pm/v1")


def _error_response(
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    payload = ApiError(code=code, message=message)
    return JSONResponse(
        status_code=status_code,
        content=payload.to_dict(),
        headers=headers,
    )


@app.middleware("http")
async def authenticate_gateway_client(request: Request, call_next):
    if request.url.path == "/healthz":
        return await call_next(request)

    configured_token = os.getenv("PM_GATEWAY_API_TOKEN")
    if not configured_token:
        return _error_response(
            503,
            "internal_error",
            "Gateway authentication is not configured.",
        )

    authorization = request.headers.get("authorization", "")
    scheme, separator, supplied_token = authorization.partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not hmac.compare_digest(supplied_token, configured_token)
    ):
        return _error_response(401, "unauthenticated", "A valid gateway bearer token is required.")

    return await call_next(request)


@app.exception_handler(HTTPException)
async def handle_http_exception(request: Request, exc: HTTPException):
    code = {
        400: "invalid_request",
        401: "unauthenticated",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        412: "precondition_failed",
        422: "validation_error",
        429: "rate_limited",
        501: "not_supported",
        502: "upstream_error",
    }.get(exc.status_code, "internal_error")
    message = exc.detail if isinstance(exc.detail, str) else "The request could not be completed."
    headers = dict(exc.headers) if exc.headers else None
    return _error_response(exc.status_code, code, message, headers=headers)


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(request: Request, exc: RequestValidationError):
    return _error_response(422, "validation_error", "The request contains invalid parameters.")


@app.get("/healthz", include_in_schema=False)
async def health_check() -> dict[str, str]:
    return {"status": "ok"}
