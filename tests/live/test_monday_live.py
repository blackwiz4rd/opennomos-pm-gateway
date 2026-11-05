"""Live monday.com compatibility checks.

These tests call the real monday.com GraphQL API. They are skipped unless
``MONDAY_LIVE=1`` is set and ``MONDAY_API_TOKEN`` is available in the
environment or the repository's ``.env`` file (loaded by ``conftest.py``; the
value is never printed).

Environment:
    MONDAY_LIVE=1                enable these checks explicitly
    MONDAY_API_TOKEN             personal API token (required to run)
    MONDAY_TEST_WORKSPACE_ID     workspace to inspect (default: discovered)
    MONDAY_TEST_BOARD_ID         board to inspect (default: first visible board)
    MONDAY_LIVE_WRITE_BOARD_ID   disposable board for the create/move/delete
                                 lifecycle; write checks are skipped without it
"""

from __future__ import annotations

import contextlib
import os
import uuid

import pytest

from pm_gateway.adapters.monday import MondayAPIError, MondayProvider
from pm_gateway.adapters.serialization import as_work_item

TOKEN = os.getenv("MONDAY_API_TOKEN", "").strip()
LIVE_ENABLED = os.getenv("MONDAY_LIVE", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
TEST_WORKSPACE_ID = os.getenv("MONDAY_TEST_WORKSPACE_ID", "").strip()
TEST_BOARD_ID = os.getenv("MONDAY_TEST_BOARD_ID", "").strip()
WRITE_BOARD_ID = os.getenv("MONDAY_LIVE_WRITE_BOARD_ID", "").strip()

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (TOKEN and LIVE_ENABLED),
        reason=(
            "live monday.com checks need MONDAY_LIVE=1 and MONDAY_API_TOKEN "
            "(environment or .env)"
        ),
    ),
]

_CREATE_ITEM = """
mutation CreateItem($boardId: ID!, $groupId: String!, $itemName: String!) {
  create_item(board_id: $boardId, group_id: $groupId, item_name: $itemName) {
    id
  }
}
"""

_DELETE_ITEM = """
mutation DeleteItem($itemId: ID!) {
  delete_item(item_id: $itemId) {
    id
  }
}
"""

_DISCOVER_BOARDS = """
query DiscoverBoards {
  boards(limit: 50) {
    id
    name
    workspace { id name }
    items_page(limit: 1) {
      items { id }
    }
  }
}
"""

_ITEMS_PAGE_HEAD = """
query ItemsPageHead($boardIds: [ID!]) {
  boards(ids: $boardIds) {
    items_page(limit: 1) {
      cursor
      items { id }
    }
  }
}
"""

_NEXT_ITEMS_PAGE = """
query NextItemsPage($cursor: String!) {
  next_items_page(cursor: $cursor, limit: 1) {
    cursor
    items { id }
  }
}
"""


@contextlib.asynccontextmanager
async def _provider(credential: str | None = None):
    provider = MondayProvider(credential or TOKEN)
    try:
        yield provider
    finally:
        await provider.invalidate()


async def _configured_board(provider: MondayProvider) -> dict:
    """Return the board under test, preferring one that already has items."""
    if TEST_BOARD_ID:
        boards = await provider._boards(TEST_WORKSPACE_ID or None, 1, 1)
        workspace_id = TEST_WORKSPACE_ID
        if not workspace_id and boards:
            workspace = boards[0].get("workspace") or {}
            workspace_id = str(workspace.get("id") or "")
        return {"id": TEST_BOARD_ID, "name": None, "workspace": {"id": workspace_id}}
    data = await provider._request(_DISCOVER_BOARDS, {})
    boards = [board for board in (data.get("boards") or []) if isinstance(board, dict)]
    if not boards:
        pytest.skip("no monday.com boards are visible to this token")
    populated = [
        board
        for board in boards
        if ((board.get("items_page") or {}).get("items") or [])
    ]
    board = (populated or boards)[0]
    workspace = board.get("workspace") or {}
    return {
        "id": str(board.get("id")),
        "name": board.get("name"),
        "workspace": {"id": str(workspace.get("id") or "")},
    }


def _workspace_id(board: dict) -> str:
    value = TEST_WORKSPACE_ID or str((board.get("workspace") or {}).get("id") or "")
    if not value:
        pytest.skip("could not determine a monday.com workspace id")
    return value


def _first_item(items: list[dict]) -> dict | None:
    return items[0] if items else None


async def test_token_identity(check) -> None:
    with check("auth / personal API token") as entry:
        async with _provider() as provider:
            data = await provider._request("query { me { id name } }", {})
        me = data.get("me") or {}
        assert me.get("id"), "monday.com did not return an identity for the token"
        entry["detail"] = f"user {me.get('name') or me.get('id')}"


async def test_workspaces_are_listable(check) -> None:
    with check("workspaces.list") as entry:
        async with _provider() as provider:
            data = await provider._request("query { workspaces { id name } }", {})
        workspaces = data.get("workspaces")
        assert isinstance(workspaces, list) and workspaces
        entry["detail"] = f"{len(workspaces)} workspace(s)"


async def test_containers_and_groups_resolve(check) -> None:
    with check("containers.list + workflow-states.list") as entry:
        async with _provider() as provider:
            board = await _configured_board(provider)
            workspace_id = _workspace_id(board)
            containers = await provider.list_projects(workspace_id)
            assert containers, "workspace returned no boards"
            assert any(container["id"] == board["id"] for container in containers)
            states = await provider.list_states(workspace_id, board["id"])
            assert states, "board returned no groups"
            assert all(state["id"] for state in states)
        entry["detail"] = (
            f"{len(containers)} board(s), {len(states)} group(s) on "
            f"board {board['id']}"
        )


async def test_work_items_are_readable_and_normalized(check) -> None:
    with check("work-items.read + normalization") as entry:
        async with _provider() as provider:
            board = await _configured_board(provider)
            workspace_id = _workspace_id(board)
            items = await provider.list_work_items(workspace_id, board["id"])
            if not items:
                pytest.skip("the selected board has no active items")
            for item in items:
                assert item["id"]
                assert item["provider"] == "monday"
                assert item["project_id"] == board["id"]
                projected = as_work_item(
                    item,
                    provider="monday",
                    workspace_id=workspace_id,
                    container_id=board["id"],
                    base_url=None,
                ).to_dict()
                assert projected["source"]["provider"] == "monday"
                assert projected["source"]["resourceType"] == "item"
                assert projected["title"] is not None
        entry["detail"] = f"{len(items)} item(s) on board {board['id']}"


async def test_single_work_item_round_trips(check) -> None:
    with check("work-items.read (single item)") as entry:
        async with _provider() as provider:
            board = await _configured_board(provider)
            workspace_id = _workspace_id(board)
            items = await provider.list_work_items(workspace_id, board["id"])
            item = _first_item(items)
            if item is None:
                pytest.skip("the selected board has no active items")
            fetched = await provider.get_work_item(
                workspace_id, board["id"], item["id"]
            )
            assert fetched["id"] == item["id"]
            assert fetched["project_id"] == board["id"]
        entry["detail"] = f"item {item['id']}"


async def test_cursor_pagination_is_supported(check) -> None:
    with check("work-items pagination (cursor)") as entry:
        async with _provider() as provider:
            board = await _configured_board(provider)
            workspace_id = _workspace_id(board)
            items = await provider.list_work_items(workspace_id, board["id"])
            if len(items) < 2:
                pytest.skip("the selected board needs at least two items")
            head = await provider._request(
                _ITEMS_PAGE_HEAD, {"boardIds": [board["id"]]}
            )
            page = ((head.get("boards") or [{}])[0]).get("items_page") or {}
            cursor = page.get("cursor")
            if not cursor:
                pytest.skip("monday.com returned no cursor for the first page")
            following = await provider._request(
                _NEXT_ITEMS_PAGE, {"cursor": cursor}
            )
            next_page = following.get("next_items_page") or {}
            first_ids = {item["id"] for item in page.get("items") or []}
            next_ids = {item["id"] for item in next_page.get("items") or []}
            assert next_ids
            assert not (first_ids & next_ids)
        entry["detail"] = f"cursor pagination on board {board['id']}"


async def test_unknown_board_maps_to_not_found(check) -> None:
    with check("error mapping (unknown board)") as entry:
        async with _provider() as provider:
            board = await _configured_board(provider)
            workspace_id = _workspace_id(board)
            with pytest.raises(MondayAPIError) as excinfo:
                await provider.list_work_items(workspace_id, "9999999999999")
        assert excinfo.value.status_code == 404
        entry["detail"] = "MondayAPIError -> 404"


async def test_invalid_token_errors_are_sanitized(check) -> None:
    invalid = "pm-gateway-invalid-live-test-token"
    with check("auth failure handling") as entry:
        async with _provider() as provider:
            board = await _configured_board(provider)
            workspace_id = _workspace_id(board)
        async with _provider(invalid) as bad:
            with pytest.raises(MondayAPIError) as excinfo:
                await bad.list_projects(workspace_id)
        message = str(excinfo.value)
        assert invalid not in message
        entry["detail"] = f"sanitized error (status {excinfo.value.status_code})"


async def test_create_move_delete_lifecycle(check) -> None:
    if not WRITE_BOARD_ID:
        pytest.skip("MONDAY_LIVE_WRITE_BOARD_ID is not configured")
    with check("work-items.transition (create/move/delete)") as entry:
        async with _provider() as provider:
            states = await provider.list_states(TEST_WORKSPACE_ID, WRITE_BOARD_ID)
            if len(states) < 2:
                pytest.skip("the write board needs at least two groups")
            created = await provider._request(
                _CREATE_ITEM,
                {
                    "boardId": WRITE_BOARD_ID,
                    "groupId": states[0]["id"],
                    "itemName": f"pm-gateway live check {uuid.uuid4().hex[:8]}",
                },
            )
            item_id = str((created.get("create_item") or {}).get("id") or "")
            assert item_id
            try:
                fetched = await provider.get_work_item(
                    TEST_WORKSPACE_ID, WRITE_BOARD_ID, item_id
                )
                assert fetched["state_id"] == states[0]["id"]

                moved = await provider.update_work_item(
                    TEST_WORKSPACE_ID,
                    WRITE_BOARD_ID,
                    item_id,
                    {"state": states[1]["id"]},
                )
                assert moved["state_id"] == states[1]["id"]

                moved_back = await provider.update_work_item(
                    TEST_WORKSPACE_ID,
                    WRITE_BOARD_ID,
                    item_id,
                    {"state": states[0]["name"]},
                )
                assert moved_back["state_id"] == states[0]["id"]
            finally:
                await provider._request(_DELETE_ITEM, {"itemId": item_id})
        entry["detail"] = (
            f"item {item_id} moved {states[0]['id']} -> {states[1]['id']} -> "
            f"{states[0]['id']} and deleted"
        )


def test_contract_gaps_are_reported(check) -> None:
    """Record resources monday.com has but the standard contract does not expose."""
    gaps = {
        "comments.read": "monday 'updates' exist; the gateway contract does not expose comments for any provider yet",
        "attachments.read": "monday assets exist; attachments are not implemented for any provider yet",
        "custom-fields.discovery": "monday column schema is used internally; discovery is not exposed by the contract",
        "milestones.list": "no monday.com milestone resource",
        "iterations.list": "no native monday.com iteration/sprint resource",
        "boards.list": "monday.com boards are the containers; there is no nested board level",
        "work-items.update": "only workflow transitions are exposed; arbitrary column updates are not",
    }
    for capability, reason in gaps.items():
        with check(capability) as entry:
            entry["status"] = "N/A"
            entry["detail"] = reason
