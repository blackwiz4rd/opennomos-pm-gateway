from __future__ import annotations

from pm_gateway.adapters.plane import PlaneProvider
from pm_gateway.adapters.serialization import _description, as_work_item


def test_adf_descriptions_are_labeled_plain_text() -> None:
    text, content_type = _description(
        {
            "type": "doc",
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "hello"}]}
            ],
        }
    )
    assert text == "hello"
    assert content_type == "text/plain"


def test_plane_html_descriptions_are_labeled_html() -> None:
    normalized = PlaneProvider("secret")._normalize_work_item(
        {
            "id": "issue-1",
            "name": "Fix login",
            "description_html": "<b>Fix login</b>",
        }
    )
    assert normalized["description_content_type"] == "text/html"

    item = as_work_item(
        normalized,
        provider="plane",
        workspace_id="ws-1",
        container_id="project-1",
        base_url="https://api.plane.so",
    )
    assert item.description_content_type == "text/html"


def test_work_item_urls_reject_unsafe_schemes() -> None:
    item = as_work_item(
        {"id": "1", "title": "Fix login", "url": "javascript:alert(1)"},
        provider="jira",
        workspace_id="ws-1",
        container_id="ABC",
        base_url="https://jira.example.test",
    )

    assert item.url != "javascript:alert(1)"
    assert item.url.startswith("https://jira.example.test/")
