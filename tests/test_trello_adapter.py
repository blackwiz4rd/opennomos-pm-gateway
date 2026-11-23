from __future__ import annotations

import asyncio

import httpx
import pytest

from pm_gateway.adapters.trello import TrelloAPIError, TrelloProvider

CREDENTIAL = "key:token"

BOARD = {"id": "b1", "name": "Board", "url": "https://trello.com/b/b1"}
LISTS = [{"id": "l1", "name": "To Do"}, {"id": "l2", "name": "Done"}]
CARD = {
    "id": "c1",
    "idShort": 42,
    "name": "Ship the Trello adapter",
    "desc": "Ships the integration.",
    "idList": "l1",
    "idBoard": "b1",
    "labels": [{"name": "backend"}],
    "idMembers": ["m1"],
    "idMemberCreator": "creator",
    "closed": False,
    "url": "https://trello.com/c/c1",
    "shortUrl": "https://trello.com/c/c1",
}


class FakeTrelloApi:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def get_members_id_boards(self, id, filter=None):
        self.calls.append(("boards", {"id": id, "filter": filter}))
        return [BOARD]

    async def get_boards_id(self, id, fields=None):
        self.calls.append(("board", {"id": id}))
        return BOARD

    async def get_boards_id_cards(self, id):
        self.calls.append(("cards", {"id": id}))
        return [CARD]

    async def get_boards_id_lists(self, id, fields=None, filter=None):
        self.calls.append(("lists", {"id": id}))
        return [dict(item) for item in LISTS]

    async def get_cards_id(self, id, fields=None, list=False, board=False):
        self.calls.append(("card", {"id": id}))
        return {**CARD, "list": LISTS[0], "board": BOARD}

    async def put_cards_id(self, id, id_list=None):
        self.calls.append(("move", {"id": id, "id_list": id_list}))
        return None


def _provider() -> TrelloProvider:
    provider = TrelloProvider(CREDENTIAL)
    provider._api = FakeTrelloApi()
    return provider


def _run(awaitable):
    return asyncio.run(awaitable)


def test_credential_must_use_key_token_format() -> None:
    with pytest.raises(ValueError):
        TrelloProvider("only-token")


def test_list_projects_normalizes_boards() -> None:
    provider = _provider()
    projects = _run(provider.list_projects("me"))

    assert projects == [
        {"id": "b1", "name": "Board", "identifier": None, "url": "https://trello.com/b/b1"}
    ]


def test_list_work_items_normalizes_cards() -> None:
    provider = _provider()
    items = _run(provider.list_work_items("me", "b1"))

    assert [item["id"] for item in items] == ["c1"]
    assert items[0]["title"] == "Ship the Trello adapter"
    assert items[0]["state"] == "To Do"
    assert items[0]["state_id"] == "l1"
    assert items[0]["labels"] == ["backend"]
    assert items[0]["assignee_id"] == "m1"
    assert items[0]["provider"] == "trello"


def test_get_work_item_uses_card_metadata() -> None:
    provider = _provider()
    item = _run(provider.get_work_item("me", "b1", "c1"))

    assert item["id"] == "c1"
    assert item["state"] == "To Do"
    assert item["project_name"] == "Board"


def test_list_states_normalizes_lists() -> None:
    provider = _provider()
    states = _run(provider.list_states("me", "b1"))

    assert states == [
        {"id": "l1", "name": "To Do", "group": "To Do"},
        {"id": "l2", "name": "Done", "group": "Done"},
    ]


def test_update_work_item_moves_card_between_lists() -> None:
    provider = _provider()
    item = _run(provider.update_work_item("me", "b1", "c1", {"state": "Done"}))

    assert item["id"] == "c1"
    assert ("move", {"id": "c1", "id_list": "l2"}) in provider._api.calls


def test_update_work_item_requires_target() -> None:
    provider = _provider()
    with pytest.raises(ValueError):
        _run(provider.update_work_item("me", "b1", "c1", {}))


def test_update_work_item_unknown_target_raises() -> None:
    provider = _provider()
    with pytest.raises(ValueError):
        _run(provider.update_work_item("me", "b1", "c1", {"state": "Nope"}))


def test_base_url_is_normalized() -> None:
    provider = TrelloProvider(CREDENTIAL, "https://trello-proxy.example/1")
    assert provider._base_url == "https://trello-proxy.example/1"


def test_invalidate_clears_client() -> None:
    provider = _provider()
    _run(provider.invalidate())
    assert provider._api is None
