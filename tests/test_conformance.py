from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from pm_gateway.conformance import (
    ConformanceChecker,
    main,
    normalize_base_url,
)

_PROVIDER = {"key": "compliant", "productName": "TinyTasks"}


def _error(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _json_response(
    status: int, payload: Any, headers: dict[str, str] | None = None
) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        headers={"content-type": "application/json", **(headers or {})},
    )


def _workspace(workspace_id: str = "ws-1") -> dict[str, Any]:
    return {"id": workspace_id, "name": "Workspace 1", "provider": _PROVIDER}


def _container(container_id: str = "c-1") -> dict[str, Any]:
    return {"id": container_id, "kind": "project", "name": "Container 1"}


def _work_item(work_item_id: str = "wi-1", title: str = "Fix login") -> dict[str, Any]:
    return {
        "id": work_item_id,
        "title": title,
        "source": {"provider": "compliant", "resourceType": "work-item"},
    }


def _page(items: list[Any], cursor: str | None = None) -> dict[str, Any]:
    return {"items": items, "nextCursor": cursor}


class FakeServer:
    """Small in-memory standard API used by the conformance tests."""

    def __init__(self) -> None:
        self.capabilities: dict[str, Any] = {
            "apiVersion": "1.0",
            "coreProfile": "core-read-0.1",
            "features": [
                {"name": "workspaces.list", "supported": True},
                {"name": "containers.list", "supported": True},
                {"name": "work-items.read", "supported": True},
            ],
        }
        self.workspaces: list[dict[str, Any]] = [_workspace()]
        self.containers: list[dict[str, Any]] = [_container()]
        self.work_items: list[dict[str, Any]] = [
            _work_item("wi-1"),
            _work_item("wi-2", "Second item"),
        ]
        self.require_auth = True
        self.etag: str | None = '"v1"'
        self.cursor_loop = False
        self.missing_status = 404
        self.patch_status = 200
        self.stale_status = 412
        self.optional_status = 501
        self.items_page_override: dict[str, Any] | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.require_auth and not request.headers.get("authorization"):
            return _json_response(401, _error("unauthenticated", "A token is required."))

        path = request.url.path
        method = request.method
        prefix = "/pm/v1/workspaces"

        if method == "GET" and path == "/pm/v1/capabilities":
            return _json_response(200, self.capabilities)

        if method == "GET" and path == prefix:
            return _json_response(200, _page(self.workspaces))

        if method == "GET" and path == f"{prefix}/ws-1/containers":
            return _json_response(200, _page(self.containers))

        if method == "GET" and path == f"{prefix}/ws-1/containers/c-1/work-items":
            if self.items_page_override is not None:
                return _json_response(200, self.items_page_override)
            cursor = request.url.params.get("cursor")
            if self.cursor_loop:
                return _json_response(200, _page([self.work_items[0]], "cursor-loop"))
            if cursor == "cursor-2":
                return _json_response(200, _page([self.work_items[1]], None))
            return _json_response(200, _page([self.work_items[0]], "cursor-2"))

        work_item_prefix = f"{prefix}/ws-1/work-items/"
        if path.startswith(work_item_prefix):
            work_item_id = path[len(work_item_prefix) :]
            if "/" not in work_item_id and work_item_id.startswith("conformance-missing-"):
                if self.missing_status == 404:
                    return _json_response(404, _error("not_found", "No such item."))
                return _json_response(
                    self.missing_status, _error("internal_error", "Unexpected failure.")
                )
            if "/" not in work_item_id:
                if method == "GET":
                    headers = {"ETag": self.etag} if self.etag else {}
                    return _json_response(200, self.work_items[0], headers=headers)
                if method == "PATCH":
                    if request.headers.get("if-match") == '"conformance-stale"':
                        if self.stale_status == 412:
                            return _json_response(
                                412,
                                _error("precondition_failed", "Revision mismatch."),
                            )
                        return _json_response(
                            self.stale_status, _error("conflict", "Revision mismatch.")
                        )
                    if self.patch_status != 200:
                        return _json_response(
                            self.patch_status, _error("validation_error", "Rejected.")
                        )
                    return _json_response(200, self.work_items[0])

        if method == "POST" and path.endswith("/transitions"):
            return _json_response(200, self.work_items[0])

        optional_prefixes = (
            f"{prefix}/ws-1/containers/c-1/boards",
            f"{prefix}/ws-1/containers/c-1/board-scope",
            f"{prefix}/ws-1/containers/c-1/workflow-states",
            f"{prefix}/ws-1/containers/c-1/iterations",
            f"{prefix}/ws-1/containers/c-1/milestones",
            f"{prefix}/ws-1/containers/c-1/custom-fields",
            f"{prefix}/ws-1/work-items/wi-1/comments",
            f"{prefix}/ws-1/work-items/wi-1/attachments",
        )
        if method == "GET" and path in optional_prefixes:
            if self.optional_status == 501:
                return _json_response(
                    501, _error("not_supported", "Operation not supported.")
                )
            if path.endswith("/board-scope"):
                return _json_response(
                    200, {"boardId": None, "scope": "all", "sprints": []}
                )
            return _json_response(200, _page([]))

        return _json_response(404, _error("not_found", f"No route for {method} {path}."))


def _checker(server: FakeServer, token: str = "secret-token") -> ConformanceChecker:
    return ConformanceChecker(
        "https://tools.example.test",
        token=token,
        transport=httpx.MockTransport(server),
    )


def _get(report: Any, check_id: str) -> Any:
    return next(check for check in report.checks if check.check_id == check_id)


def test_read_only_checks_pass() -> None:
    server = FakeServer()
    with _checker(server) as checker:
        report = checker.run()
    assert report.ok, report.render()
    assert report.complete
    assert _get(report, "C1").status == "pass"
    assert _get(report, "C2").status == "pass"
    assert _get(report, "C3").status == "pass"
    assert _get(report, "C4").status == "pass"
    assert _get(report, "C5").status == "pass"
    assert _get(report, "C6").status == "pass"
    assert _get(report, "C7").status == "pass"
    assert _get(report, "C8").status == "pass"
    assert _get(report, "C9").status == "skip"
    assert _get(report, "C10").status == "skip"
    assert _get(report, "C11").status == "skip"


def test_write_checks_pass_with_etag() -> None:
    server = FakeServer()
    with _checker(server) as checker:
        report = checker.run(write=True)
    assert report.ok, report.render()
    assert _get(report, "C9").status == "pass"
    assert _get(report, "C10").status == "pass"


def test_explicit_ids_are_used_when_discovery_is_empty() -> None:
    server = FakeServer()
    server.workspaces = []
    server.containers = []
    server.items_page_override = {"items": [], "nextCursor": None}
    with _checker(server) as checker:
        report = checker.run(
            workspace_id="ws-1", container_id="c-1", work_item_id="wi-1"
        )
    assert report.ok, report.render()
    assert "explicit workspace" in _get(report, "C2").detail
    assert "explicit container" in _get(report, "C3").detail
    assert "explicit work item" in _get(report, "C4").detail


def test_missing_workspace_is_incomplete() -> None:
    server = FakeServer()
    server.workspaces = []
    with _checker(server) as checker:
        report = checker.run()
    assert report.ok
    assert not report.complete
    assert report.status == "incomplete"
    assert "INCOMPLETE" in report.render()


def test_missing_capabilities_field_fails() -> None:
    server = FakeServer()
    server.capabilities = {"apiVersion": "1.0", "features": []}
    with _checker(server) as checker:
        report = checker.run()
    assert not report.ok
    assert _get(report, "C1").status == "fail"
    assert "coreProfile" in _get(report, "C1").detail


def test_malformed_work_item_page_fails() -> None:
    server = FakeServer()
    server.items_page_override = {"items": "not-a-list", "nextCursor": None}
    with _checker(server) as checker:
        report = checker.run()
    assert not report.ok
    assert _get(report, "C4").status == "fail"
    assert "items" in _get(report, "C4").detail


def test_anonymous_access_fails_auth_check() -> None:
    server = FakeServer()
    server.require_auth = False
    with _checker(server) as checker:
        report = checker.run()
    assert not report.ok
    assert _get(report, "C7").status == "fail"
    assert "401" in _get(report, "C7").detail


def test_missing_resource_wrong_status_fails() -> None:
    server = FakeServer()
    server.missing_status = 500
    with _checker(server) as checker:
        report = checker.run()
    assert not report.ok
    assert _get(report, "C8").status == "fail"


def test_cursor_loop_fails() -> None:
    server = FakeServer()
    server.cursor_loop = True
    with _checker(server) as checker:
        report = checker.run()
    assert not report.ok
    assert _get(report, "C4").status == "fail"
    assert "cursor" in _get(report, "C4").detail.lower()


def test_missing_etag_skips_conditional_checks() -> None:
    server = FakeServer()
    server.etag = None
    with _checker(server) as checker:
        report = checker.run(write=True)
    assert report.ok, report.render()
    assert _get(report, "C6").status == "skip"
    assert _get(report, "C10").status == "skip"


def test_full_reports_unsupported_optional_operations() -> None:
    server = FakeServer()
    with _checker(server) as checker:
        report = checker.run(full=True)
    assert report.ok, report.render()
    for check_id in ("O1", "O2", "O3", "O4", "O5", "O6", "O7", "O8"):
        assert _get(report, check_id).status == "pass"


def test_full_validates_optional_pages() -> None:
    server = FakeServer()
    server.optional_status = 200
    with _checker(server) as checker:
        report = checker.run(full=True)
    assert report.ok, report.render()
    assert _get(report, "O1").status == "pass"


def test_capability_claim_mismatch_fails() -> None:
    server = FakeServer()
    server.capabilities["features"].append(
        {"name": "comments.read", "supported": True}
    )
    with _checker(server) as checker:
        report = checker.run(full=True)
    assert not report.ok
    assert _get(report, "O7").status == "fail"
    assert "comments.read" in _get(report, "O7").detail


def test_optional_operation_reported_unsupported_passes() -> None:
    server = FakeServer()
    server.capabilities["features"].append(
        {"name": "comments.read", "supported": False}
    )
    with _checker(server) as checker:
        report = checker.run(full=True)
    assert report.ok, report.render()
    assert _get(report, "O7").status == "pass"


def test_token_is_redacted_from_report() -> None:
    server = FakeServer()
    with _checker(server, token="super-secret-token") as checker:
        report = checker.run()
        assert checker._redact("value super-secret-token") == "value ***"
    assert "super-secret-token" not in report.render()
    assert "super-secret-token" not in json.dumps(report.to_dict())


def test_base_url_normalization() -> None:
    assert normalize_base_url("https://tools.example.test") == (
        "https://tools.example.test/pm/v1"
    )
    assert normalize_base_url("https://tools.example.test/pm/v1/") == (
        "https://tools.example.test/pm/v1"
    )
    assert normalize_base_url("https://tools.example.test/custom") == (
        "https://tools.example.test/custom/pm/v1"
    )
    with pytest.raises(ValueError):
        normalize_base_url("http://tools.example.test")
    assert normalize_base_url(
        "http://tools.example.test", allow_insecure_http=True
    ) == "http://tools.example.test/pm/v1"


def test_cli_rejects_insecure_base_url() -> None:
    assert main(["--base-url", "http://tools.example.test"]) == 2
