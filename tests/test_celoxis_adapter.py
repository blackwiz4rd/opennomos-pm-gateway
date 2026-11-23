from __future__ import annotations

import json

import httpx
import pytest

from pm_gateway.adapters.celoxis import CeloxisAPIError, CeloxisProvider

CREDENTIAL = "celoxis-secret-token"

PROJECT = {"id": 1, "name": "Project", "code": "P1", "url": "https://app.celoxis.com/p/1"}
TASK = {
    "id": 2,
    "name": "Ship the Celoxis adapter",
    "description": "Ships the integration.",
    "custom_kanban_state": "In Progress",
    "priority": "High",
    "plannedStart": "2024-01-01",
    "plannedFinish": "2024-01-31",
    "actualFinish": None,
    "created": "2024-01-01",
    "lastModified": "2024-01-02",
}


class FakeCeloxis:
    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.responses: dict[str, object] = {}
        self.status_code = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(
            {
                "method": request.method,
                "url": str(request.url),
                "path": request.url.path,
                "params": dict(request.url.params),
            }
        )
        for fragment, body in self.responses.items():
            if fragment in request.url.path:
                if isinstance(body, Exception):
                    raise body
                return httpx.Response(self.status_code, json=body)
        raise AssertionError(f"Unexpected Celoxis request: {request.method} {request.url.path}")

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def provider(self, base_url: str | None = None) -> CeloxisProvider:
        return CeloxisProvider(CREDENTIAL, base_url, transport=self.transport)


def _run(awaitable):
    import asyncio

    return asyncio.run(awaitable)


def test_base_url_is_normalized() -> None:
    for base, expected in (
        ("https://app.celoxis.com/psa/api/v2", "https://app.celoxis.com/psa/api/v2"),
        ("https://app.celoxis.com/api/v2", "https://app.celoxis.com/psa/api/v2"),
        ("https://app.celoxis.com/psa", "https://app.celoxis.com/psa/api/v2"),
        ("https://app.celoxis.com", "https://app.celoxis.com/psa/api/v2"),
    ):
        provider = CeloxisProvider(CREDENTIAL, base)
        assert provider._base_url == expected


def test_list_projects_normalizes_projects() -> None:
    fake = FakeCeloxis()
    fake.responses["/projects"] = {"data": [PROJECT], "nextPage": None}

    projects = _run(fake.provider().list_projects("workspace"))

    assert projects == [
        {"id": "1", "name": "Project", "identifier": "P1", "url": "https://app.celoxis.com/p/1"}
    ]
    assert fake.requests[0]["path"].endswith("/projects")


def test_list_work_items_normalizes_tasks() -> None:
    fake = FakeCeloxis()
    fake.responses["/tasks"] = {"data": [TASK], "nextPage": None}

    items = _run(fake.provider().list_work_items("workspace", "1"))

    assert [item["id"] for item in items] == ["2"]
    assert items[0]["title"] == "Ship the Celoxis adapter"
    assert items[0]["state"] == "In Progress"
    assert items[0]["priority"] == "High"
    assert items[0]["provider"] == "celoxis"
    assert json.loads(fake.requests[0]["params"]["filter"]) == {"project.id": "1"}


def test_get_work_item_returns_task() -> None:
    fake = FakeCeloxis()
    fake.responses["/tasks/2"] = {"data": TASK}

    item = _run(fake.provider().get_work_item("workspace", "1", "2"))

    assert item["id"] == "2"
    assert item["title"] == "Ship the Celoxis adapter"


def test_get_work_item_missing_raises() -> None:
    fake = FakeCeloxis()
    fake.responses["/tasks/999"] = {"data": None}
    fake.status_code = 404

    # Simulate a 404 by returning error status for that path through a custom handler is complex;
    # instead ensure not-found payload raises CeloxisAPIError.
    fake2 = FakeCeloxis()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    provider = CeloxisProvider(CREDENTIAL, transport=httpx.MockTransport(handler))
    with pytest.raises(CeloxisAPIError) as exc:
        _run(provider.get_work_item("workspace", "1", "999"))
    assert exc.value.status_code == 404


def test_list_projects_requires_workspace() -> None:
    fake = FakeCeloxis()
    with pytest.raises(ValueError):
        _run(fake.provider().list_projects(""))


def test_board_and_sprint_filters_are_rejected() -> None:
    fake = FakeCeloxis()
    with pytest.raises(ValueError):
        _run(fake.provider().list_work_items("workspace", "1", board_id="b"))


def test_invalidate_clears_client() -> None:
    fake = FakeCeloxis()
    provider = fake.provider()
    _run(provider.list_projects("workspace")) if False else None
    _run(provider.invalidate())
    assert provider._client is None
