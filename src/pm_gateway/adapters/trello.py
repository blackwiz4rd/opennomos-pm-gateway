"""Trello provider — implements the generic PMProvider interface.

Uses the generated Trello OpenAPI client.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from pm_gateway.adapters.models import NormalizedWorkItem
from pm_gateway.generated.providers.trello.api.default_api import DefaultApi
from pm_gateway.generated.providers.trello.api_client import ApiClient
from pm_gateway.generated.providers.trello.configuration import Configuration

logger = logging.getLogger("pm_gateway.adapters.trello")

DEFAULT_BASE_URL = "https://api.trello.com/1"
API_PATH = "/1"

_DEFAULT_MAX_ITEMS = 10_000


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _max_items() -> int:
    value = _env_int("PM_GATEWAY_TRELLO_MAX_ITEMS", _DEFAULT_MAX_ITEMS)
    return value if value >= 0 else _DEFAULT_MAX_ITEMS


def _as_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if isinstance(value, dict):
        return dict(value)
    return {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _identifier(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


class TrelloAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class TrelloProvider:
    provider_key = "trello"

    def __init__(self, credential: str, base_url: str | None = None, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._credential = credential
        self._key, self._token = self._parse_credential(credential)
        base = (base_url or "https://api.trello.com").strip().rstrip("/")
        base = base.removesuffix(API_PATH)
        self._base_url = f"{base}{API_PATH}"
        self._transport = transport
        self._client: ApiClient | None = None
        self._api: DefaultApi | None = None
        self._lock = asyncio.Lock()

    @staticmethod
    def _parse_credential(credential: str) -> tuple[str, str]:
        key, separator, token = str(credential or "").partition(":")
        key, token = key.strip(), token.strip()
        if not separator or not key or not token:
            raise ValueError("Trello credential must be in format: api_key:token")
        return key, token

    async def _ensure(self) -> DefaultApi:
        if self._api is not None:
            return self._api
        async with self._lock:
            if self._api is None:
                config = Configuration()
                config.host = self._base_url
                config.api_key = {"APIKey": self._key, "APIToken": self._token}
                client = ApiClient(config)
                if self._transport is not None:
                    client.rest_client.pool_manager = httpx.AsyncClient(transport=self._transport)
                self._client = client
                self._api = DefaultApi(client)
            return self._api

    async def invalidate(self) -> None:
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
        self._client = None
        self._api = None

    def _sanitize(self, text: str) -> str:
        value = str(text or "")
        for secret in {self._credential, self._token, self._key}:
            if secret:
                value = value.replace(secret, "[redacted]")
        return value[:500]

    async def _request_error(self, exc: Exception) -> TrelloAPIError:
        status_code = getattr(exc, "status", None) or getattr(getattr(exc, "response", None), "status_code", None)
        return TrelloAPIError(self._sanitize(str(exc)), status_code=status_code)

    @classmethod
    def _card_to_dict(cls, card: Any, project_id: str = "", list_names: dict[str, str] | None = None) -> dict[str, Any]:
        raw = _as_dict(card)
        list_id = _identifier(raw.get("idList"))
        board_id = _identifier(raw.get("idBoard")) or (str(project_id) if project_id else None)
        labels: list[str] = []
        for label in _as_list(raw.get("labels")):
            name = _as_dict(label).get("name")
            if name:
                labels.append(str(name))
        members = [m for m in _as_list(raw.get("idMembers")) if isinstance(m, str)]
        completed = bool(raw.get("dueComplete")) or bool(raw.get("closed"))
        state_category = "completed" if completed else None
        if state_category is None:
            state_category = None
        list_names = list_names or {}
        state_name = list_names.get(str(list_id)) if list_id else None
        if completed:
            state_category = "completed"
        else:
            state_category = None
        return NormalizedWorkItem(
            id=str(raw.get("id") or ""),
            identifier=_identifier(raw.get("idShort")) or _identifier(raw.get("id")),
            title=raw.get("name") or "",
            description=raw.get("desc") or None,
            description_content_type="text/plain" if raw.get("desc") else None,
            url=raw.get("url") or raw.get("shortUrl"),
            state=state_name,
            state_id=list_id,
            state_category=state_category if state_category else None,
            start_date=None,
            due_date=None,
            completed_at=None,
            created_at=None,
            updated_at=None,
            assignee=None,
            assignee_id=members[0] if members else None,
            creator_id=_identifier(raw.get("idMemberCreator")),
            parent_id=None,
            project_id=board_id,
            project_name=None,
            labels=labels,
            item_type="card",
            custom_fields={},
            memberships=[{"project_id": board_id, "project_name": None, "list_id": list_id, "list_name": state_name}] if board_id else [],
            provider="trello",
            raw=raw,
        ).model_dump()

    @staticmethod
    def _normalize_board(raw: dict[str, Any]) -> dict[str, Any]:
        return {"id": str(raw.get("id") or ""), "name": raw.get("name") or str(raw.get("id") or ""), "identifier": None, "url": raw.get("url") or raw.get("shortUrl")}

    @staticmethod
    def _normalize_list(raw: dict[str, Any]) -> dict[str, Any]:
        name = raw.get("name") or str(raw.get("id") or "")
        return {"id": str(raw.get("id") or ""), "name": name, "group": name}

    async def validate_slug(self, slug: str) -> dict | None:
        try:
            boards = await self.list_projects(slug)
            return {"slug": slug, "valid": True, "projects_count": len(boards)}
        except Exception:
            return None

    async def list_projects(self, workspace_slug: str) -> list[dict]:
        member = str(workspace_slug or "").strip() or "me"
        api = await self._ensure()
        try:
            boards = await api.get_members_id_boards(id=member, filter="open")
        except Exception as exc:
            raise TrelloAPIError(self._sanitize(str(exc)), status_code=getattr(exc, "status", None)) from exc
        raws = [_as_dict(b) for b in (boards or [])]
        return [self._normalize_board(b) for b in raws if b.get("id") is not None]

    async def list_work_items(self, workspace_slug: str, project_id: str, expand: str | None = None, board_id: str | None = None, sprint_ids: list[str] | None = None) -> list[dict]:
        if board_id or sprint_ids:
            raise ValueError("Trello does not support board or iteration filters.")
        api = await self._ensure()
        try:
            board = await api.get_boards_id(id=project_id, fields="id,name,desc,closed,url,shortUrl,dateLastActivity,idOrganization")
            cards = await api.get_boards_id_cards(id=project_id)
            lists = await api.get_boards_id_lists(id=project_id, fields="id,name,closed,pos,idBoard", filter="open")
        except Exception as exc:
            raise TrelloAPIError(self._sanitize(str(exc)), status_code=getattr(exc, "status", None)) from exc
        board_raw = _as_dict(board)
        list_names = {str(_as_dict(x).get("id")): _as_dict(x).get("name") for x in (lists or []) if _as_dict(x).get("id") is not None}
        cap = _max_items()
        cards_list = list(cards or [])
        truncated = False
        if cap and len(cards_list) > cap:
            cards_list = cards_list[:cap]
            truncated = True
        if truncated:
            logger.warning("Trello board %s truncated at %s cards", project_id, cap)
        items = []
        for card in cards_list:
            normalized = self._card_to_dict(card, project_id, list_names)
            normalized["project_name"] = board_raw.get("name")
            for membership in normalized.get("memberships") or []:
                membership["project_name"] = board_raw.get("name")
                membership["list_name"] = list_names.get(str(membership.get("list_id")))
            items.append(normalized)
        return items

    async def get_work_item(self, workspace_slug: str, project_id: str, work_item_id: str) -> dict:
        api = await self._ensure()
        try:
            card = await api.get_cards_id(id=work_item_id, fields="id,name,desc,closed,due,dueComplete,idList,idBoard,idMembers,idLabels,labels,pos,url,shortUrl,dateLastActivity,start,idMemberCreator", list=True, board=True)
        except Exception as exc:
            raise TrelloAPIError(self._sanitize(str(exc)), status_code=getattr(exc, "status", None)) from exc
        card_raw = _as_dict(card)
        if not card_raw.get("id"):
            raise TrelloAPIError("The requested Trello card was not found.", status_code=404)
        list_meta = _as_dict(card_raw.get("list"))
        board_meta = _as_dict(card_raw.get("board"))
        list_names = {str(list_meta.get("id")): list_meta.get("name")} if list_meta.get("id") else {}
        normalized = self._card_to_dict(card, project_id, list_names)
        normalized["state"] = list_meta.get("name")
        normalized["project_name"] = board_meta.get("name")
        for membership in normalized.get("memberships") or []:
            membership["list_name"] = list_meta.get("name")
            membership["project_name"] = board_meta.get("name")
        return normalized

    async def list_states(self, workspace_slug: str, project_id: str, board_id: str | None = None) -> list[dict]:
        api = await self._ensure()
        try:
            raw = await api.get_boards_id_lists(id=project_id, fields="id,name,closed,pos,idBoard", filter="open")
        except Exception as exc:
            raise TrelloAPIError(self._sanitize(str(exc)), status_code=getattr(exc, "status", None)) from exc
        return [self._normalize_list(_as_dict(x)) for x in (raw or []) if _as_dict(x).get("id") is not None or _as_dict(x).get("name")]

    async def update_work_item(self, workspace_slug: str, project_id: str, work_item_id: str, data: dict) -> dict:
        target = (data or {}).get("state")
        if not target:
            raise ValueError("A target Trello list is required.")
        states = await self.list_states(workspace_slug, project_id)
        needle = str(target).strip().casefold()
        match = next((s for s in states if needle in {s["id"].casefold(), s["name"].casefold()}), None)
        if match is None:
            raise ValueError(f"No Trello list matches '{target}' on board {project_id}.")
        api = await self._ensure()
        try:
            await api.put_cards_id(id=work_item_id, id_list=match["id"])
        except Exception as exc:
            raise TrelloAPIError(self._sanitize(str(exc)), status_code=getattr(exc, "status", None)) from exc
        return await self.get_work_item(workspace_slug, project_id, work_item_id)

    async def list_boards(self, workspace_slug: str, project_id: str) -> list[dict]:
        return []

    async def list_sprints(self, workspace_slug: str, project_id: str, board_id: str | None = None) -> list[dict]:
        return []

    async def get_board_scope(self, workspace_slug: str, project_id: str, board_id: str | None = None, sprint_ids: list[str] | None = None) -> dict:
        return {"board_id": None, "scope": "all", "sprint": None, "sprints": []}
