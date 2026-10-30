"""Conformance checker for the draft PM interoperability API.

This module is a standalone, dependency-light verifier for third-party software
that wants to be used through the gateway's ``compliant`` provider mode. It
checks the wire contract published in ``spec/pm-api-openapi-v0.1.yaml``; it does
not import the gateway application, the provider adapters, or the generated
models.

The checker is read-only by default. Mutating checks require ``--write`` or an
explicit ``--transition-to``/``--transition-to-id`` selection.

Usage::

    pm-gateway-conformance --base-url https://tools.example.com \
        --token-env COMPLIANT_TOKEN

Exit codes: ``0`` when every required check passes, ``1`` when a check fails,
``2`` for invalid configuration.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from pm_gateway._version import __version__

CORE_PROFILE = "core-read-0.1"
DEFAULT_TOKEN_ENV = "PM_CONFORMANCE_TOKEN"
DEFAULT_TIMEOUT = 10.0

_MAPPING_QUALITIES = frozenset({"exact", "mapped", "lossy", "inferred", "unmapped"})
_WORKFLOW_CATEGORIES = frozenset(
    {"backlog", "unstarted", "started", "completed", "cancelled"}
)
_CUSTOM_FIELD_TYPES = frozenset(
    {
        "text",
        "number",
        "boolean",
        "date",
        "date-time",
        "single-select",
        "multi-select",
        "user",
        "users",
        "reference",
        "rich-text",
        "json",
        "provider-defined",
    }
)
_CUSTOM_FIELD_SCOPES = frozenset(
    {"workspace", "container", "work-item-type", "project", "board", "table", "provider-defined"}
)
_BOARD_SCOPES = frozenset({"all", "backlog", "sprint"})
_ERROR_CODES = frozenset(
    {
        "invalid_request",
        "unauthenticated",
        "forbidden",
        "not_found",
        "conflict",
        "precondition_failed",
        "validation_error",
        "rate_limited",
        "not_supported",
        "upstream_error",
        "internal_error",
    }
)


class ConformanceError(Exception):
    """A response violates the published contract."""


class SkipCheck(Exception):
    """A check cannot run in the current context."""


@dataclass
class CheckResult:
    """Outcome of one conformance check."""

    check_id: str
    name: str
    status: str
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.check_id,
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass
class Report:
    """Collected results for one target."""

    target: str
    profile: str = CORE_PROFILE
    checks: list[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult) -> None:
        self.checks.append(result)

    @property
    def failed(self) -> list[CheckResult]:
        return [check for check in self.checks if check.status == "fail"]

    @property
    def ok(self) -> bool:
        return not self.failed

    @property
    def complete(self) -> bool:
        required = {"C1", "C2", "C3", "C4", "C5", "C7", "C8"}
        return not any(
            check.status == "skip" and check.check_id in required
            for check in self.checks
        )

    @property
    def status(self) -> str:
        if self.failed:
            return "fail"
        return "pass" if self.complete else "incomplete"

    def counts(self) -> dict[str, int]:
        return {
            "passed": sum(1 for check in self.checks if check.status == "pass"),
            "failed": len(self.failed),
            "skipped": sum(1 for check in self.checks if check.status == "skip"),
        }

    def to_dict(self) -> dict[str, Any]:
        counts = self.counts()
        return {
            "target": self.target,
            "profile": self.profile,
            "status": self.status,
            "counts": counts,
            "checks": [check.to_dict() for check in self.checks],
        }

    def render(self) -> str:
        lines = [
            f"PM gateway conformance — {self.profile}",
            f"Target: {self.target}",
            "",
        ]
        width = max((len(check.name) for check in self.checks), default=0)
        for check in self.checks:
            lines.append(
                f"{check.status.upper():<4}  {check.check_id:<3}  "
                f"{check.name:<{width}}  {check.detail}".rstrip()
            )
        counts = self.counts()
        if self.failed:
            result = "NOT COMPLIANT"
        elif self.complete:
            result = "COMPLIANT"
        else:
            result = "INCOMPLETE — required checks were skipped"
        lines.extend(
            [
                "",
                (
                    f"Summary: {counts['passed']} passed, "
                    f"{counts['failed']} failed, {counts['skipped']} skipped"
                ),
                f"Result: {result}",
            ]
        )
        return "\n".join(lines)


def normalize_base_url(value: str, *, allow_insecure_http: bool = False) -> str:
    """Validate a provider base URL and append the standard ``/pm/v1`` path."""
    value = value.strip()
    if not value:
        raise ValueError("A base URL is required.")
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ValueError("The base URL must be an absolute HTTPS URL.")
    if parsed.username or parsed.password:
        raise ValueError("The base URL must not contain embedded credentials.")
    if parsed.query or parsed.fragment:
        raise ValueError("The base URL must not contain a query or fragment.")
    if parsed.scheme != "https" and not allow_insecure_http:
        raise ValueError(
            "The base URL must use HTTPS. Pass --insecure-http only for local development."
        )
    path = parsed.path.rstrip("/")
    if not path.lower().endswith("/pm/v1"):
        path = f"{path}/pm/v1"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _object(value: Any, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConformanceError(f"{context}: expected a JSON object.")
    return value


def _array(value: Any, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise ConformanceError(f"{context}: expected a JSON array.")
    return value


def _string(obj: dict[str, Any], key: str, context: str, *, nullable: bool = False) -> str | None:
    if key not in obj:
        raise ConformanceError(f"{context}.{key}: required field is missing.")
    value = obj[key]
    if value is None:
        if nullable:
            return None
        raise ConformanceError(f"{context}.{key}: must be a string, not null.")
    if not isinstance(value, str):
        raise ConformanceError(f"{context}.{key}: must be a string.")
    return value


def _enum(
    obj: dict[str, Any], key: str, allowed: frozenset[str], context: str
) -> str:
    value = _string(obj, key, context)
    assert value is not None
    if value not in allowed:
        raise ConformanceError(
            f"{context}.{key}: {value!r} is not one of {sorted(allowed)}."
        )
    return value


def _page(
    payload: Any,
    context: str,
    validate_item: Callable[[Any, str], None],
) -> tuple[dict[str, Any], str | None]:
    page = _object(payload, context)
    items = _array(page.get("items"), f"{context}.items")
    for index, item in enumerate(items):
        validate_item(item, f"{context}.items[{index}]")
    cursor = page.get("nextCursor")
    if cursor is not None and not isinstance(cursor, str):
        raise ConformanceError(f"{context}.nextCursor: must be a string or null.")
    return page, cursor


def _validate_workspace(item: Any, context: str) -> None:
    workspace = _object(item, context)
    _string(workspace, "id", context)
    _string(workspace, "name", context)
    provider = _object(workspace.get("provider"), f"{context}.provider")
    _string(provider, "key", f"{context}.provider")


def _validate_container(item: Any, context: str) -> None:
    container = _object(item, context)
    _string(container, "id", context)
    _string(container, "kind", context)
    _string(container, "name", context)


def _validate_mapping(value: Any, context: str) -> None:
    mapping = _object(value, context)
    _enum(mapping, "quality", _MAPPING_QUALITIES, context)


def _validate_iteration(item: Any, context: str) -> None:
    iteration = _object(item, context)
    _string(iteration, "id", context)
    _string(iteration, "kind", context)


def _validate_milestone(item: Any, context: str) -> None:
    milestone = _object(item, context)
    _string(milestone, "id", context)
    _string(milestone, "name", context)


def _validate_work_item(item: Any, context: str) -> None:
    work_item = _object(item, context)
    _string(work_item, "id", context)
    _string(work_item, "title", context)
    source = _object(work_item.get("source"), f"{context}.source")
    _string(source, "provider", f"{context}.source")
    _string(source, "resourceType", f"{context}.source")
    status = work_item.get("status")
    if status is not None:
        status_obj = _object(status, f"{context}.status")
        _string(status_obj, "name", f"{context}.status")
        _validate_mapping(status_obj.get("mapping"), f"{context}.status.mapping")
        category = status_obj.get("category")
        if category is not None and category not in _WORKFLOW_CATEGORIES:
            raise ConformanceError(
                f"{context}.status.category: {category!r} is not a valid category."
            )


def _validate_workflow_state(item: Any, context: str) -> None:
    state = _object(item, context)
    _string(state, "name", context)
    _validate_mapping(state.get("mapping"), f"{context}.mapping")


def _validate_custom_field(item: Any, context: str) -> None:
    custom_field = _object(item, context)
    _string(custom_field, "id", context)
    _string(custom_field, "name", context)
    _enum(custom_field, "valueType", _CUSTOM_FIELD_TYPES, context)
    _enum(custom_field, "scope", _CUSTOM_FIELD_SCOPES, context)


def _validate_comment(item: Any, context: str) -> None:
    comment = _object(item, context)
    _string(comment, "id", context)
    _string(comment, "body", context)


def _validate_attachment(item: Any, context: str) -> None:
    attachment = _object(item, context)
    _string(attachment, "id", context)
    _string(attachment, "name", context)


def _validate_board_scope(payload: Any, context: str) -> None:
    board_scope = _object(payload, context)
    _string(board_scope, "boardId", context, nullable=True)
    _enum(board_scope, "scope", _BOARD_SCOPES, context)
    sprints = _array(board_scope.get("sprints"), f"{context}.sprints")
    for index, sprint in enumerate(sprints):
        _validate_iteration(sprint, f"{context}.sprints[{index}]")


@dataclass
class _State:
    capabilities: dict[str, Any] | None = None
    workspace_id: str | None = None
    container_id: str | None = None
    work_item_id: str | None = None
    work_item_title: str | None = None
    etag: str | None = None


class ConformanceChecker:
    """Run the conformance checks against one standard API deployment."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        allow_insecure_http: bool = False,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = normalize_base_url(
            base_url, allow_insecure_http=allow_insecure_http
        )
        self._token = token
        self._secrets = [token] if token else []
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        )
        self._anonymous = httpx.Client(
            headers={"Accept": "application/json"},
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()
        self._anonymous.close()

    def __enter__(self) -> ConformanceChecker:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: Any | None = None,
        headers: dict[str, str] | None = None,
        anonymous: bool = False,
    ) -> httpx.Response:
        cleaned = {key: value for key, value in (params or {}).items() if value is not None}
        client = self._anonymous if anonymous else self._client
        try:
            return client.request(
                method,
                self._url(path),
                params=cleaned,
                json=body,
                headers=headers or {},
            )
        except httpx.HTTPError as exc:
            raise ConformanceError(
                f"{method} {path}: request failed ({type(exc).__name__})."
            ) from exc

    def _json(
        self, response: httpx.Response, *, expected: int = 200, context: str
    ) -> Any:
        if response.status_code != expected:
            raise ConformanceError(
                f"{context}: expected HTTP {expected}, got {response.status_code}."
            )
        content_type = response.headers.get("content-type", "")
        if not content_type.lower().startswith("application/json"):
            raise ConformanceError(
                f"{context}: expected application/json, got "
                f"{content_type or 'no content type'}."
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ConformanceError(f"{context}: response is not valid JSON.") from exc

    def _expect_error(
        self, response: httpx.Response, *, status: int, code: str, context: str
    ) -> None:
        if response.status_code != status:
            raise ConformanceError(
                f"{context}: expected HTTP {status}, got {response.status_code}."
            )
        payload = self._json(response, expected=status, context=context)
        error = _object(payload, context)
        actual = _string(error, "code", context)
        if actual not in _ERROR_CODES:
            raise ConformanceError(
                f"{context}.code: {actual!r} is not a standard error code."
            )
        if actual != code:
            raise ConformanceError(
                f"{context}.code: expected {code!r}, got {actual!r}."
            )
        message = _string(error, "message", context)
        if not message or not message.strip():
            raise ConformanceError(f"{context}.message: must not be empty.")

    # ------------------------------------------------------------------
    # Checks
    # ------------------------------------------------------------------

    def _check_capabilities(self, state: _State) -> str:
        response = self._request("GET", "/capabilities")
        payload = self._json(response, context="GET /capabilities")
        capabilities = _object(payload, "capabilities")
        api_version = _string(capabilities, "apiVersion", "capabilities")
        core_profile = _string(capabilities, "coreProfile", "capabilities")
        features = _array(capabilities.get("features"), "capabilities.features")
        for index, feature in enumerate(features):
            context = f"capabilities.features[{index}]"
            item = _object(feature, context)
            _string(item, "name", context)
            supported = item.get("supported")
            if not isinstance(supported, bool):
                raise ConformanceError(f"{context}.supported: must be a boolean.")
        state.capabilities = capabilities
        detail = f"apiVersion={api_version}, coreProfile={core_profile}"
        if core_profile != CORE_PROFILE:
            detail += f" (checker targets {CORE_PROFILE})"
        return detail

    def _check_workspaces(self, state: _State) -> str:
        response = self._request("GET", "/workspaces", params={"limit": 1})
        payload = self._json(response, context="GET /workspaces")
        page, _cursor = _page(payload, "workspaces", _validate_workspace)
        items = page["items"]
        if not items:
            if state.workspace_id:
                return f"using explicit workspace={state.workspace_id}"
            raise SkipCheck("no workspaces are visible to this token.")
        if not state.workspace_id:
            state.workspace_id = items[0]["id"]
        return f"using workspace={state.workspace_id}"

    def _check_containers(self, state: _State) -> str:
        if not state.workspace_id:
            raise SkipCheck("no workspace available.")
        workspace = quote(state.workspace_id, safe="")
        response = self._request(
            "GET", f"/workspaces/{workspace}/containers", params={"limit": 1}
        )
        payload = self._json(response, context="GET containers")
        page, _cursor = _page(payload, "containers", _validate_container)
        items = page["items"]
        if not items:
            if state.container_id:
                return f"using explicit container={state.container_id}"
            raise SkipCheck("no containers are visible in the first workspace.")
        if not state.container_id:
            state.container_id = items[0]["id"]
        return f"using container={state.container_id}"

    def _check_work_items(self, state: _State) -> str:
        if not state.workspace_id or not state.container_id:
            raise SkipCheck("no workspace or container available.")
        workspace = quote(state.workspace_id, safe="")
        container = quote(state.container_id, safe="")
        path = f"/workspaces/{workspace}/containers/{container}/work-items"
        response = self._request("GET", path, params={"limit": 1})
        payload = self._json(response, context="GET work items")
        page, cursor = _page(payload, "workItems", _validate_work_item)
        items = page["items"]
        if not items:
            if state.work_item_id:
                return f"using explicit work item={state.work_item_id}"
            raise SkipCheck("the container contains no work items.")
        if not state.work_item_id:
            state.work_item_id = items[0]["id"]
            state.work_item_title = items[0]["title"]

        if cursor:
            first_id = items[0]["id"]
            second = self._request("GET", path, params={"limit": 1, "cursor": cursor})
            second_payload = self._json(second, context="GET work items (page 2)")
            second_page, second_cursor = _page(
                second_payload, "workItems", _validate_work_item
            )
            if second_cursor == cursor:
                raise ConformanceError(
                    "pagination: the second page returned the same nextCursor "
                    "(cursor loop)."
                )
            second_items = second_page["items"]
            if second_items and second_items[0]["id"] == first_id:
                raise ConformanceError(
                    "pagination: the second page repeated the first item, "
                    "so the cursor was ignored."
                )
        return f"using work item={state.work_item_id}"

    def _check_work_item(self, state: _State) -> str:
        if not state.workspace_id or not state.work_item_id:
            raise SkipCheck("no work item available.")
        workspace = quote(state.workspace_id, safe="")
        work_item = quote(state.work_item_id, safe="")
        response = self._request(
            "GET",
            f"/workspaces/{workspace}/work-items/{work_item}",
            params={"containerId": state.container_id},
        )
        payload = self._json(response, context="GET work item")
        _validate_work_item(payload, "workItem")
        if payload.get("id") != state.work_item_id:
            raise ConformanceError(
                "workItem.id: the response ID does not match the requested ID."
            )
        state.work_item_title = payload.get("title")
        state.etag = response.headers.get("etag")
        return f"id={state.work_item_id}"

    def _check_etag(self, state: _State) -> str:
        if not state.etag:
            raise SkipCheck("the provider does not advertise ETag (optional).")
        return f"ETag={state.etag!r}"

    def _check_auth(self, _state: _State) -> str:
        if not self._token:
            raise SkipCheck("no token supplied; cannot test the anonymous path.")
        response = self._request("GET", "/workspaces", params={"limit": 1}, anonymous=True)
        self._expect_error(
            response,
            status=401,
            code="unauthenticated",
            context="anonymous GET /workspaces",
        )
        return "anonymous request rejected with 401 unauthenticated"

    def _check_not_found(self, state: _State) -> str:
        if not state.workspace_id:
            raise SkipCheck("no workspace available.")
        workspace = quote(state.workspace_id, safe="")
        missing = f"conformance-missing-{uuid.uuid4()}"
        response = self._request(
            "GET",
            f"/workspaces/{workspace}/work-items/{missing}",
            params={"containerId": state.container_id},
        )
        self._expect_error(
            response,
            status=404,
            code="not_found",
            context="GET missing work item",
        )
        return "missing resource returns 404 not_found"

    def _check_patch(self, state: _State, *, write: bool) -> str:
        if not write:
            raise SkipCheck("read-only mode; use --write to test updates.")
        if not state.workspace_id or not state.work_item_id or not state.work_item_title:
            raise SkipCheck("no work item available.")
        workspace = quote(state.workspace_id, safe="")
        work_item = quote(state.work_item_id, safe="")
        headers = {"If-Match": state.etag} if state.etag else {}
        response = self._request(
            "PATCH",
            f"/workspaces/{workspace}/work-items/{work_item}",
            params={"containerId": state.container_id},
            body={"title": state.work_item_title},
            headers=headers,
        )
        payload = self._json(response, context="PATCH work item")
        _validate_work_item(payload, "workItem")
        if payload.get("id") != state.work_item_id:
            raise ConformanceError(
                "workItem.id: the PATCH response ID does not match the requested ID."
            )
        return "unchanged title accepted with a valid work-item response"

    def _check_conditional_patch(self, state: _State, *, write: bool) -> str:
        if not write:
            raise SkipCheck("read-only mode; use --write to test updates.")
        if not state.etag:
            raise SkipCheck("no ETag advertised; conditional updates are optional.")
        if not state.workspace_id or not state.work_item_id or not state.work_item_title:
            raise SkipCheck("no work item available.")
        workspace = quote(state.workspace_id, safe="")
        work_item = quote(state.work_item_id, safe="")
        response = self._request(
            "PATCH",
            f"/workspaces/{workspace}/work-items/{work_item}",
            params={"containerId": state.container_id},
            body={"title": state.work_item_title},
            headers={"If-Match": '"conformance-stale"'},
        )
        self._expect_error(
            response,
            status=412,
            code="precondition_failed",
            context="conditional PATCH with stale ETag",
        )
        return "stale If-Match rejected with 412 precondition_failed"

    def _check_transition(
        self,
        state: _State,
        *,
        target_state_id: str | None,
        target_state_name: str | None,
    ) -> str:
        if not target_state_id and not target_state_name:
            raise SkipCheck(
                "transitions are opt-in; pass --transition-to or --transition-to-id."
            )
        if not state.workspace_id or not state.work_item_id:
            raise SkipCheck("no work item available.")
        workspace = quote(state.workspace_id, safe="")
        work_item = quote(state.work_item_id, safe="")
        body: dict[str, str] = {}
        if target_state_id:
            body["targetStateId"] = target_state_id
        else:
            body["targetStateName"] = target_state_name or ""
        headers = {"If-Match": state.etag} if state.etag else {}
        response = self._request(
            "POST",
            f"/workspaces/{workspace}/work-items/{work_item}/transitions",
            params={"containerId": state.container_id},
            body=body,
            headers=headers,
        )
        payload = self._json(response, context="POST transition")
        _validate_work_item(payload, "workItem")
        if payload.get("id") != state.work_item_id:
            raise ConformanceError(
                "workItem.id: the transition response ID does not match the requested ID."
            )
        return "transition returned a valid work item"

    def _capability_supported(self, state: _State, name: str) -> bool | None:
        if not state.capabilities:
            return None
        for feature in state.capabilities.get("features", []):
            if isinstance(feature, dict) and feature.get("name") == name:
                value = feature.get("supported")
                return value if isinstance(value, bool) else None
        return None

    def _check_optional_page(
        self,
        path: str,
        check_id: str,
        name: str,
        validate_item: Callable[[Any, str], None],
        *,
        state: _State,
        feature_name: str,
        params: dict[str, Any] | None = None,
        include_limit: bool = True,
    ) -> CheckResult:
        query = dict(params or {})
        if include_limit:
            query["limit"] = 1
        response = self._request("GET", path, params=query)
        if response.status_code == 501:
            self._expect_error(
                response,
                status=501,
                code="not_supported",
                context=f"GET {name}",
            )
            if self._capability_supported(state, feature_name) is True:
                raise ConformanceError(
                    f"capabilities advertise {feature_name!r} as supported, "
                    f"but GET {name} returned 501 not_supported."
                )
            return CheckResult(check_id, name, "pass", "reported as not supported (501)")
        if response.status_code == 404:
            return CheckResult(
                check_id, name, "skip", "not applicable for the discovered resource (404)"
            )
        payload = self._json(response, context=f"GET {name}")
        _page(payload, name, validate_item)
        detail = "page validated"
        if self._capability_supported(state, feature_name) is False:
            detail += f" (capabilities report {feature_name!r} unsupported)"
        return CheckResult(check_id, name, "pass", detail)

    def _check_optional_board_scope(
        self, state: _State, workspace: str, container: str
    ) -> CheckResult:
        response = self._request(
            "GET", f"/workspaces/{workspace}/containers/{container}/board-scope"
        )
        if response.status_code == 501:
            self._expect_error(
                response,
                status=501,
                code="not_supported",
                context="GET board scope",
            )
            if self._capability_supported(state, "board-scope.read") is True:
                raise ConformanceError(
                    "capabilities advertise 'board-scope.read' as supported, "
                    "but GET board scope returned 501 not_supported."
                )
            return CheckResult("O2", "board scope", "pass", "reported as not supported (501)")
        if response.status_code == 404:
            return CheckResult("O2", "board scope", "skip", "not applicable (404)")
        payload = self._json(response, context="GET board scope")
        _validate_board_scope(payload, "boardScope")
        return CheckResult("O2", "board scope", "pass", "scope document validated")

    def _optional_result(
        self, check_id: str, name: str, func: Callable[[], CheckResult]
    ) -> CheckResult:
        try:
            return func()
        except ConformanceError as exc:
            return CheckResult(check_id, name, "fail", self._redact(str(exc)))
        except Exception as exc:  # pragma: no cover - unexpected programming error
            return CheckResult(
                check_id,
                name,
                "fail",
                self._redact(f"unexpected error ({type(exc).__name__}): {exc}"),
            )

    def _optional_checks(self, state: _State) -> list[CheckResult]:
        if not state.workspace_id or not state.container_id:
            return []
        workspace = quote(state.workspace_id, safe="")
        container = quote(state.container_id, safe="")
        base = f"/workspaces/{workspace}/containers/{container}"
        work_item = (
            f"/workspaces/{workspace}/work-items/{quote(state.work_item_id, safe='')}"
            if state.work_item_id
            else None
        )
        checks: list[CheckResult] = [
            self._optional_result(
                "O1",
                "boards",
                lambda: self._check_optional_page(
                    f"{base}/boards",
                    "O1",
                    "boards",
                    _validate_container,
                    state=state,
                    feature_name="boards.list",
                ),
            ),
            self._optional_result(
                "O2",
                "board scope",
                lambda: self._check_optional_board_scope(state, workspace, container),
            ),
            self._optional_result(
                "O3",
                "workflow states",
                lambda: self._check_optional_page(
                    f"{base}/workflow-states",
                    "O3",
                    "workflow states",
                    _validate_workflow_state,
                    state=state,
                    feature_name="workflow-states.list",
                    include_limit=False,
                ),
            ),
            self._optional_result(
                "O4",
                "iterations",
                lambda: self._check_optional_page(
                    f"{base}/iterations",
                    "O4",
                    "iterations",
                    _validate_iteration,
                    state=state,
                    feature_name="iterations.list",
                ),
            ),
            self._optional_result(
                "O5",
                "milestones",
                lambda: self._check_optional_page(
                    f"{base}/milestones",
                    "O5",
                    "milestones",
                    _validate_milestone,
                    state=state,
                    feature_name="milestones.list",
                ),
            ),
            self._optional_result(
                "O6",
                "custom fields",
                lambda: self._check_optional_page(
                    f"{base}/custom-fields",
                    "O6",
                    "custom fields",
                    _validate_custom_field,
                    state=state,
                    feature_name="custom-fields.discovery",
                ),
            ),
        ]
        if work_item:
            checks.append(
                self._optional_result(
                    "O7",
                    "comments",
                    lambda: self._check_optional_page(
                        f"{work_item}/comments",
                        "O7",
                        "comments",
                        _validate_comment,
                        state=state,
                        feature_name="comments.read",
                    ),
                )
            )
            checks.append(
                self._optional_result(
                    "O8",
                    "attachments",
                    lambda: self._check_optional_page(
                        f"{work_item}/attachments",
                        "O8",
                        "attachments",
                        _validate_attachment,
                        state=state,
                        feature_name="attachments.read",
                    ),
                )
            )
        return checks

    # ------------------------------------------------------------------
    # Runner
    # ------------------------------------------------------------------

    def _run(
        self,
        report: Report,
        check_id: str,
        name: str,
        func: Callable[[], str],
        *,
        fail_fast: bool,
    ) -> None:
        try:
            detail = func()
        except SkipCheck as exc:
            report.add(CheckResult(check_id, name, "skip", str(exc)))
            return
        except ConformanceError as exc:
            report.add(CheckResult(check_id, name, "fail", self._redact(str(exc))))
        except Exception as exc:  # pragma: no cover - unexpected programming error
            report.add(
                CheckResult(
                    check_id,
                    name,
                    "fail",
                    self._redact(f"unexpected error ({type(exc).__name__}): {exc}"),
                )
            )
        else:
            report.add(CheckResult(check_id, name, "pass", self._redact(detail)))
        finally:
            if fail_fast and report.failed:
                raise _FailFast

    def _redact(self, text: str) -> str:
        for secret in self._secrets:
            if secret:
                text = text.replace(secret, "***")
        return text

    def run(
        self,
        *,
        workspace_id: str | None = None,
        container_id: str | None = None,
        work_item_id: str | None = None,
        write: bool = False,
        full: bool = False,
        auth_check: bool = True,
        target_state_id: str | None = None,
        target_state_name: str | None = None,
        fail_fast: bool = False,
    ) -> Report:
        """Run all checks and return the collected report."""
        state = _State(
            workspace_id=workspace_id,
            container_id=container_id,
            work_item_id=work_item_id,
        )
        report = Report(target=self.base_url)
        checks: list[tuple[str, str, Callable[[], str]]] = [
            ("C1", "capabilities", lambda: self._check_capabilities(state)),
            ("C2", "workspaces", lambda: self._check_workspaces(state)),
            ("C3", "containers", lambda: self._check_containers(state)),
            ("C4", "work items list", lambda: self._check_work_items(state)),
            ("C5", "work item get", lambda: self._check_work_item(state)),
            ("C6", "ETag", lambda: self._check_etag(state)),
            ("C7", "auth required", lambda: self._check_auth(state)),
            ("C8", "not found", lambda: self._check_not_found(state)),
            ("C9", "work item update", lambda: self._check_patch(state, write=write)),
            (
                "C10",
                "conditional update",
                lambda: self._check_conditional_patch(state, write=write),
            ),
            (
                "C11",
                "workflow transition",
                lambda: self._check_transition(
                    state,
                    target_state_id=target_state_id,
                    target_state_name=target_state_name,
                ),
            ),
        ]
        try:
            for check_id, name, func in checks:
                if check_id == "C7" and not auth_check:
                    report.add(
                        CheckResult(check_id, name, "skip", "auth check disabled.")
                    )
                    continue
                self._run(report, check_id, name, func, fail_fast=fail_fast)
        except _FailFast:
            return report
        if full:
            for result in self._optional_checks(state):
                report.add(
                    CheckResult(
                        result.check_id,
                        result.name,
                        result.status,
                        self._redact(result.detail),
                    )
                )
        return report


class _FailFast(Exception):
    """Internal marker used to stop the runner early."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pm-gateway-conformance",
        description=(
            "Check a third-party service against the draft PM interoperability "
            "API used by the gateway's compliant provider mode."
        ),
    )
    parser.add_argument("--base-url", required=True, help="Standard API base URL.")
    parser.add_argument(
        "--token",
        help="Provider bearer token. Prefer --token-env or PM_CONFORMANCE_TOKEN.",
    )
    parser.add_argument(
        "--token-env",
        default=DEFAULT_TOKEN_ENV,
        help=f"Environment variable holding the bearer token (default: {DEFAULT_TOKEN_ENV}).",
    )
    parser.add_argument("--workspace-id", help="Workspace ID to test instead of discovery.")
    parser.add_argument("--container-id", help="Container ID to test instead of discovery.")
    parser.add_argument("--work-item-id", help="Work item ID to test instead of discovery.")
    parser.add_argument(
        "--write",
        action="store_true",
        help="Enable mutating checks (PATCH with unchanged values and stale If-Match).",
    )
    parser.add_argument(
        "--transition-to",
        metavar="STATE_NAME",
        help="Transition the selected work item to this state name (mutating).",
    )
    parser.add_argument(
        "--transition-to-id",
        metavar="STATE_ID",
        help="Transition the selected work item to this state ID (mutating).",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Also probe optional read operations (boards, workflows, comments, ...).",
    )
    parser.add_argument(
        "--no-auth-check",
        action="store_true",
        help="Skip the anonymous 401 check (for example behind mTLS).",
    )
    parser.add_argument(
        "--insecure-http",
        action="store_true",
        help="Allow an http:// base URL for local development only.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Request timeout in seconds (default: {DEFAULT_TIMEOUT:g}).",
    )
    parser.add_argument(
        "--json", action="store_true", help="Print a machine-readable report."
    )
    parser.add_argument(
        "--fail-fast", action="store_true", help="Stop after the first failed check."
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    token = args.token or os.getenv(args.token_env) or None
    if args.transition_to and args.transition_to_id:
        parser.error("use only one of --transition-to and --transition-to-id")
    try:
        checker = ConformanceChecker(
            args.base_url,
            token=token,
            timeout=args.timeout,
            allow_insecure_http=args.insecure_http,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        report = checker.run(
            workspace_id=args.workspace_id,
            container_id=args.container_id,
            work_item_id=args.work_item_id,
            write=args.write,
            full=args.full,
            auth_check=not args.no_auth_check,
            target_state_id=args.transition_to_id,
            target_state_name=args.transition_to,
            fail_fast=args.fail_fast,
        )
    finally:
        checker.close()
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.render())
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
