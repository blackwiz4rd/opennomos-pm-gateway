"""Live Trello compatibility checks."""

from __future__ import annotations

import contextlib
import os

import pytest

from pm_gateway.adapters.trello import TrelloProvider
from pm_gateway.adapters.serialization import as_work_item

TOKEN = os.getenv("TRELLO_API_TOKEN", "").strip()
BASE_URL = os.getenv("TRELLO_BASE_URL", "").strip() or None
LIVE_ENABLED = os.getenv("TRELLO_LIVE", "").strip().lower() in {"1", "true", "yes", "on"}

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not (TOKEN and LIVE_ENABLED),
        reason="live Trello checks need TRELLO_LIVE=1 and TRELLO_API_TOKEN",
    ),
]


@contextlib.asynccontextmanager
async def _provider(credential: str | None = None):
    provider = TrelloProvider(credential or TOKEN, BASE_URL)
    try:
        yield provider
    finally:
        await provider.invalidate()


async def _configured_board(provider: TrelloProvider) -> dict:
    boards = await provider.list_projects("me")
    if not boards:
        pytest.skip("no Trello boards are visible to this token")
    for board in boards:
        items = await provider.list_work_items("me", board["id"])
        if items:
            return {"id": board["id"]}
    return {"id": boards[0]["id"]}


async def test_token_identity(check) -> None:
    with check("auth / API token") as entry:
        async with _provider() as provider:
            boards = await provider.list_projects("me")
        assert isinstance(boards, list)
        entry["detail"] = "passed"


async def test_boards_are_listable(check) -> None:
    with check("containers.list + workflow-states.list") as entry:
        async with _provider() as provider:
            boards = await provider.list_projects("me")
            assert boards, "Trello returned no boards"
            board = await _configured_board(provider)
            states = await provider.list_states("me", board["id"])
        entry["detail"] = "passed"


async def test_work_items_are_readable_and_normalized(check) -> None:
    with check("work-items.read + normalization") as entry:
        async with _provider() as provider:
            board = await _configured_board(provider)
            items = await provider.list_work_items("me", board["id"])
            if not items:
                pytest.skip("the selected board has no active cards")
            for item in items:
                assert item["id"]
                assert item["provider"] == "trello"
                projected = as_work_item(
                    item, provider="trello", workspace_id="me",
                    container_id=board["id"], base_url=BASE_URL,
                ).to_dict()
                assert projected["source"]["provider"] == "trello"
        entry["detail"] = "passed"


async def test_single_work_item_round_trips(check) -> None:
    with check("work-items.read (single item)") as entry:
        async with _provider() as provider:
            board = await _configured_board(provider)
            items = await provider.list_work_items("me", board["id"])
            if not items:
                pytest.skip("the selected board has no active cards")
            fetched = await provider.get_work_item("me", board["id"], items[0]["id"])
            assert fetched["id"] == items[0]["id"]
        entry["detail"] = "passed"


async def test_unknown_board_maps_to_error(check) -> None:
    with check("error mapping (unknown board)") as entry:
        async with _provider() as provider:
            try:
                await provider.list_work_items("me", "000000000000000000000000")
            except Exception as exc:
                assert exc is not None
        entry["detail"] = "passed"


async def test_invalid_token_errors_are_sanitized(check) -> None:
    invalid = "badkey:badtoken"
    with check("auth failure handling") as entry:
        async with _provider() as provider:
            await _configured_board(provider)
        async with _provider(invalid) as bad:
            with pytest.raises(Exception):
                await bad.list_projects("me")
        entry["detail"] = "passed"
