"""monday.com provider — implements the generic PMProvider interface.

Uses the monday.com GraphQL API (``https://api.monday.com/v2``) with a personal
API token. monday.com concepts map onto the interface as follows:

* workspace_slug -> monday workspace id (numeric)
* project_id     -> board id
* work_item_id   -> item id
* state          -> board group (groups are the board's workflow stages)
* state_category -> derived from the group title (``Done``, ``Working on it``, ...)

Authentication: the credential is a monday.com personal API token, sent in the
``Authorization`` header exactly as monday.com documents it (a ``Bearer``
prefix supplied by the caller is preserved). The token mirrors the permissions
of the user that created it.

monday.com is column-driven: a board defines its own columns, so work item
fields such as priority, assignee, dates, or labels are discovered at runtime
from the column schema. Columns that have no common representation are exposed
through the normalized ``custom_fields`` map.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

import httpx

from pm_gateway.adapters.models import NormalizedWorkItem
from pm_gateway.adapters.normalization import (
    format_date,
    format_datetime,
    normalize_priority_value,
    normalize_state_category,
)
from pm_gateway.security import provider_timeout_seconds

logger = logging.getLogger("pm_gateway.adapters.monday")

DEFAULT_BASE_URL = "https://api.monday.com"
GRAPHQL_PATH = "/v2"

# items_page caps a page at 500 items; boards accepts smaller pages and is
# paginated with the ``page`` argument.
_ITEMS_PAGE_LIMIT = 500
_BOARDS_PAGE_LIMIT = 100
_DEFAULT_MAX_ITEMS = 10_000

_ITEM_FIELDS = """
  id
  name
  url
  created_at
  updated_at
  creator_id
  creator { id name }
  group { id title }
  board { id name workspace { id name } }
  parent_item { id }
  column_values {
    id
    text
    value
    column { id title type }
  }
"""

_BOARDS_QUERY = """
query Boards($workspaceIds: [ID!], $limit: Int!, $page: Int!) {
  boards(workspace_ids: $workspaceIds, limit: $limit, page: $page) {
    id
    name
    url
    workspace { id name }
  }
}
"""

_BOARD_ITEMS_QUERY = """
query BoardItems($boardIds: [ID!], $limit: Int!) {
  boards(ids: $boardIds) {
    id
    name
    url
    workspace { id name }
    groups { id title }
    columns { id title type }
    items_page(limit: $limit) {
      cursor
      items { %s }
    }
  }
}
""" % _ITEM_FIELDS

_NEXT_ITEMS_PAGE_QUERY = """
query NextItemsPage($cursor: String!, $limit: Int!) {
  next_items_page(cursor: $cursor, limit: $limit) {
    cursor
    items { %s }
  }
}
""" % _ITEM_FIELDS

_BOARD_GROUPS_QUERY = """
query BoardGroups($boardIds: [ID!]) {
  boards(ids: $boardIds) {
    groups { id title }
  }
}
"""

_ITEMS_BY_ID_QUERY = """
query ItemsById($ids: [ID!]!) {
  items(ids: $ids) { %s }
}
""" % _ITEM_FIELDS

_MOVE_ITEM_MUTATION = """
mutation MoveItemToGroup($itemId: ID!, $groupId: String!) {
  move_item_to_group(item_id: $itemId, group_id: $groupId) {
    id
  }
}
"""


class MondayAPIError(RuntimeError):
    """A sanitized monday.com API failure.

    ``status_code`` is read by the gateway dispatcher to translate provider
    failures (429/404) into the matching standard API response.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _max_items() -> int:
    """Maximum number of items fetched for one listing (0 disables the cap)."""
    value = _env_int("PM_GATEWAY_MONDAY_MAX_ITEMS", _DEFAULT_MAX_ITEMS)
    return value if value >= 0 else _DEFAULT_MAX_ITEMS


def _column(cv: dict[str, Any]) -> dict[str, Any]:
    value = cv.get("column")
    return value if isinstance(value, dict) else {}


def _column_type(cv: dict[str, Any]) -> str:
    return str(_column(cv).get("type") or cv.get("type") or "").strip().lower()


def _column_title(cv: dict[str, Any]) -> str:
    return str(_column(cv).get("title") or cv.get("id") or "")


def _json_value(cv: dict[str, Any]) -> Any:
    raw = cv.get("value")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _split_labels(text: str | None) -> list[str]:
    if not text:
        return []
    return [part.strip() for part in text.split(",") if part.strip()]


class MondayProvider:
    provider_key = "monday"

    def __init__(
        self,
        credential: str,
        base_url: str | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._credential = credential
        self._base_url = (base_url or DEFAULT_BASE_URL).strip().rstrip("/")
        # Accept both the API root and an already-qualified GraphQL endpoint so
        # callers can point at monday.com or a compatible proxy.
        if self._base_url.endswith(GRAPHQL_PATH):
            self._base_url = self._base_url[: -len(GRAPHQL_PATH)]
        self._endpoint = f"{self._base_url}{GRAPHQL_PATH}"
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    # ── transport ──

    def _build_client(self) -> httpx.AsyncClient:
        # Redirects are not followed: the request carries the provider token.
        return httpx.AsyncClient(
            timeout=provider_timeout_seconds(),
            follow_redirects=False,
            transport=self._transport,
        )

    async def _ensure(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        async with self._lock:
            if self._client is None:
                self._client = self._build_client()
            return self._client

    async def invalidate(self) -> None:
        """Close the credential-bearing HTTP client."""
        client, self._client = self._client, None
        if client is not None:
            try:
                await client.aclose()
            except Exception as exc:  # pragma: no cover - best-effort cleanup
                logger.debug(
                    "Could not close monday.com client: %s", type(exc).__name__
                )

    def _sanitize(self, text: str) -> str:
        value = str(text or "")
        for secret in {self._credential, self._credential.strip()}:
            if secret:
                value = value.replace(secret, "[redacted]")
        return value[:500]

    def _headers(self) -> dict[str, str]:
        token = self._credential.strip()
        headers = {
            "Authorization": token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        version = os.getenv("PM_GATEWAY_MONDAY_API_VERSION", "").strip()
        if version:
            headers["API-Version"] = version
        return headers

    async def _request(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        client = await self._ensure()
        try:
            response = await client.post(
                self._endpoint,
                json={"query": query, "variables": variables},
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            raise MondayAPIError(
                "The monday.com request failed.",
                status_code=getattr(exc, "status_code", None),
            ) from exc

        if response.status_code >= 400:
            raise MondayAPIError(
                "The monday.com request failed.",
                status_code=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise MondayAPIError(
                "monday.com returned an invalid response."
            ) from exc
        if not isinstance(payload, dict):
            raise MondayAPIError("monday.com returned an invalid response.")

        errors = payload.get("errors")
        if errors:
            self._raise_graphql_errors(errors)
        data = payload.get("data")
        return data if isinstance(data, dict) else {}

    def _raise_graphql_errors(self, errors: Any) -> None:
        error = errors[0] if isinstance(errors, list) and errors else {}
        if not isinstance(error, dict):
            error = {}
        code = str((error.get("extensions") or {}).get("code") or "") or None
        status_code = {
            "ResourceNotFound": 404,
            "RateLimitExceeded": 429,
            "IPRateLimitExceeded": 429,
        }.get(code or "")
        message = error.get("message") or "monday.com rejected the request."
        raise MondayAPIError(
            f"monday.com API error: {self._sanitize(message)}",
            status_code=status_code,
            code=code,
        )

    async def _boards(
        self, workspace_id: str | None, limit: int, page: int
    ) -> list[dict]:
        variables: dict[str, Any] = {"limit": limit, "page": page}
        if workspace_id is not None:
            variables["workspaceIds"] = [str(workspace_id)]
        data = await self._request(_BOARDS_QUERY, variables)
        boards = data.get("boards") or []
        return [board for board in boards if isinstance(board, dict)]

    # ── normalization ──

    @staticmethod
    def _group_payload(group: Any) -> dict | None:
        if not isinstance(group, dict):
            return None
        group_id = group.get("id")
        title = group.get("title")
        if group_id is None and not title:
            return None
        return {"id": group_id, "title": title}

    @staticmethod
    def _board_payload(board: dict[str, Any] | None) -> dict:
        board = board or {}
        workspace = board.get("workspace")
        return {
            "id": str(board.get("id") or "") or None,
            "name": board.get("name"),
            "url": board.get("url"),
            "workspace": workspace if isinstance(workspace, dict) else {},
        }

    @classmethod
    def _item_to_dict(
        cls, item: dict[str, Any], board: dict[str, Any] | None = None
    ) -> dict:
        """Convert a monday.com item to the normalized work item model."""
        board_payload = cls._board_payload(board or item.get("board"))
        group = cls._group_payload(item.get("group"))

        state_name = group.get("title") if group else None
        state_id = group.get("id") if group else None
        state_category = normalize_state_category(state_name)

        description = None
        priority_text = None
        assignee_name = None
        assignee_id = None
        due_date = None
        start_date = None
        estimate = None
        labels: list[str] = []
        custom_fields: dict[str, Any] = {}
        consumed: set[int] = set()

        column_values = item.get("column_values") or []
        for index, raw_cv in enumerate(column_values):
            if not isinstance(raw_cv, dict):
                continue
            title = _column_title(raw_cv)
            lowered = title.strip().lower()
            column_type = _column_type(raw_cv)
            text = raw_cv.get("text")
            value = _json_value(raw_cv)
            used = False

            if column_type == "long_text" and description is None:
                if any(word in lowered for word in ("description", "notes")):
                    description = text or None
                    used = True

            if priority_text is None and column_type in {"status", "priority"}:
                if "priority" in lowered:
                    priority_text = text or None
                    used = True

            if assignee_name is None and column_type in {"people", "multiple-person"}:
                names = text or None
                first_person = None
                if isinstance(value, dict):
                    persons = value.get("personsAndTeams") or value.get("persons") or []
                    if isinstance(persons, list):
                        for person in persons:
                            if isinstance(person, dict) and person.get("id") is not None:
                                first_person = person
                                break
                assignee_name = names
                assignee_id = (
                    str(first_person["id"]) if first_person is not None else None
                )
                used = True

            if column_type == "timeline" and isinstance(value, dict):
                start_date = start_date or format_date(value.get("from"))
                due_date = due_date or format_date(value.get("to"))
                used = True

            if column_type == "date" and isinstance(value, dict):
                parsed = format_date(value.get("date"))
                if parsed:
                    if any(word in lowered for word in ("start", "begin")):
                        start_date = start_date or parsed
                    else:
                        due_date = due_date or parsed
                    used = True

            if column_type in {"dropdown", "tags"} and not labels:
                labels = _split_labels(text)
                used = bool(labels)

            if column_type == "numbers" and estimate is None and text:
                if any(word in lowered for word in ("estimate", "point", "score")):
                    try:
                        estimate = float(str(text).replace(",", ""))
                    except (TypeError, ValueError):
                        estimate = None
                    used = estimate is not None

            if used:
                consumed.add(index)

        for index, raw_cv in enumerate(column_values):
            if index in consumed or not isinstance(raw_cv, dict):
                continue
            text = raw_cv.get("text")
            if not text:
                continue
            custom_fields[_column_title(raw_cv) or str(raw_cv.get("id"))] = text

        creator = item.get("creator")
        creator_name = None
        if isinstance(creator, dict):
            creator_name = creator.get("name")

        creator_id = item.get("creator_id")
        if creator_id is None and isinstance(creator, dict):
            creator_id = creator.get("id")

        parent = item.get("parent_item")
        parent_id = None
        if isinstance(parent, dict) and parent.get("id") is not None:
            parent_id = str(parent["id"])

        workspace = board_payload.get("workspace") or {}
        raw = dict(item)
        # Keep provider metadata out of the normalized projection; ``raw`` is
        # never serialized by the gateway contract.
        raw["board"] = {
            key: value for key, value in board_payload.items() if key != "workspace"
        }

        return NormalizedWorkItem(
            id=str(item.get("id") or ""),
            identifier=str(item.get("id")) if item.get("id") is not None else None,
            title=item.get("name") or "",
            description=description,
            description_content_type="text/plain" if description else None,
            url=item.get("url"),
            state=state_name,
            state_id=str(state_id) if state_id is not None else None,
            state_category=state_category,
            priority=priority_text,
            priority_value=normalize_priority_value(priority_text, "monday"),
            estimate=estimate,
            start_date=start_date,
            due_date=due_date,
            created_at=format_datetime(item.get("created_at")),
            updated_at=format_datetime(item.get("updated_at")),
            assignee=assignee_name,
            assignee_id=assignee_id,
            creator=creator_name,
            creator_id=str(creator_id) if creator_id is not None else None,
            parent_id=parent_id,
            project_id=board_payload.get("id"),
            project_name=board_payload.get("name"),
            team_id=str(workspace["id"]) if workspace.get("id") is not None else None,
            team_name=workspace.get("name"),
            labels=labels,
            item_type="subitem" if parent_id else "item",
            custom_fields=custom_fields,
            provider="monday",
            raw=raw,
        ).model_dump()

    @staticmethod
    def _normalize_board(raw: dict) -> dict:
        workspace = raw.get("workspace") if isinstance(raw, dict) else None
        workspace_id = (
            str(workspace["id"])
            if isinstance(workspace, dict) and workspace.get("id") is not None
            else None
        )
        return {
            "id": str(raw.get("id") or ""),
            "name": raw.get("name") or str(raw.get("id") or ""),
            "identifier": None,
            "url": raw.get("url"),
            "workspace_id": workspace_id,
        }

    # ── PMProvider ──

    async def validate_slug(self, slug: str) -> dict | None:
        try:
            projects = await self.list_projects(slug)
            return {"slug": slug, "valid": True, "projects_count": len(projects)}
        except Exception:
            return None

    async def list_projects(self, workspace_slug: str) -> list[dict]:
        workspace_id = str(workspace_slug or "").strip()
        if not workspace_id.isdigit():
            raise ValueError("The monday.com workspace id must be numeric.")
        boards: list[dict] = []
        page = 1
        while True:
            batch = await self._boards(workspace_id, _BOARDS_PAGE_LIMIT, page)
            for board in batch:
                normalized = self._normalize_board(board)
                if normalized["id"]:
                    boards.append(normalized)
            if len(batch) < _BOARDS_PAGE_LIMIT:
                break
            page += 1
        return boards

    async def _board_meta(self, project_id: str) -> dict:
        """Fetch a board with its groups, columns, and the first item page."""
        data = await self._request(
            _BOARD_ITEMS_QUERY,
            {"boardIds": [str(project_id)], "limit": _ITEMS_PAGE_LIMIT},
        )
        boards = [b for b in (data.get("boards") or []) if isinstance(b, dict)]
        if not boards:
            raise MondayAPIError(
                "The requested monday.com board was not found.",
                status_code=404,
            )
        return boards[0]

    async def _iter_items(self, board: dict) -> list[dict]:
        page = board.get("items_page") if isinstance(board, dict) else None
        page = page if isinstance(page, dict) else {}
        items = [item for item in (page.get("items") or []) if isinstance(item, dict)]
        cursor = page.get("cursor")
        cap = _max_items()
        truncated = False
        while cursor:
            if cap and len(items) >= cap:
                truncated = True
                break
            data = await self._request(
                _NEXT_ITEMS_PAGE_QUERY,
                {"cursor": str(cursor), "limit": _ITEMS_PAGE_LIMIT},
            )
            page = data.get("next_items_page")
            page = page if isinstance(page, dict) else {}
            batch = [i for i in (page.get("items") or []) if isinstance(i, dict)]
            if not batch:
                break
            items.extend(batch)
            cursor = page.get("cursor")
        if cap and len(items) > cap:
            items = items[:cap]
            truncated = True
        if truncated:
            logger.warning(
                "monday.com board %s was truncated at %s items; set "
                "PM_GATEWAY_MONDAY_MAX_ITEMS to raise the cap.",
                board.get("id"),
                cap,
            )
        return items

    async def list_work_items(
        self,
        workspace_slug: str,
        project_id: str,
        expand: str | None = None,
        board_id: str | None = None,
        sprint_ids: list[str] | None = None,
    ) -> list[dict]:
        if board_id or sprint_ids:
            raise ValueError("monday.com does not support board or iteration filters.")
        board = await self._board_meta(project_id)
        items = await self._iter_items(board)
        return [self._item_to_dict(item, board) for item in items]

    async def get_work_item(
        self, workspace_slug: str, project_id: str, work_item_id: str
    ) -> dict:
        data = await self._request(_ITEMS_BY_ID_QUERY, {"ids": [str(work_item_id)]})
        items = [i for i in (data.get("items") or []) if isinstance(i, dict)]
        if not items:
            raise MondayAPIError(
                "The requested monday.com item was not found.",
                status_code=404,
            )
        item = items[0]
        board = item.get("board") if isinstance(item.get("board"), dict) else None
        if board is None and project_id:
            board = await self._board_meta(project_id)
        return self._item_to_dict(item, board)

    async def list_states(
        self, workspace_slug: str, project_id: str, board_id: str | None = None
    ) -> list[dict]:
        data = await self._request(
            _BOARD_GROUPS_QUERY, {"boardIds": [str(project_id)]}
        )
        boards = [b for b in (data.get("boards") or []) if isinstance(b, dict)]
        if not boards:
            raise MondayAPIError(
                "The requested monday.com board was not found.",
                status_code=404,
            )
        groups = [g for g in (boards[0].get("groups") or []) if isinstance(g, dict)]
        return [
            {
                "id": str(group.get("id") or ""),
                "name": group.get("title") or str(group.get("id") or ""),
                # The gateway derives state categories from ``group``.
                "group": group.get("title"),
            }
            for group in groups
            if group.get("id") is not None or group.get("title")
        ]

    async def update_work_item(
        self, workspace_slug: str, project_id: str, work_item_id: str, data: dict
    ) -> dict:
        target = (data or {}).get("state")
        if not target:
            raise ValueError("A target monday.com group is required.")
        states = await self.list_states(workspace_slug, project_id)
        needle = str(target).strip().casefold()
        match = next(
            (
                state
                for state in states
                if needle in {state["id"].casefold(), state["name"].casefold()}
            ),
            None,
        )
        if match is None:
            raise ValueError(
                f"No monday.com group matches '{target}' on board {project_id}."
            )
        await self._request(
            _MOVE_ITEM_MUTATION,
            {"itemId": str(work_item_id), "groupId": match["id"]},
        )
        return await self.get_work_item(workspace_slug, project_id, work_item_id)

    async def list_boards(self, workspace_slug: str, project_id: str) -> list[dict]:
        """monday.com boards are the containers themselves; there is no nested board."""
        return []

    async def list_sprints(
        self, workspace_slug: str, project_id: str, board_id: str | None = None
    ) -> list[dict]:
        """monday.com has no native iteration resource."""
        return []

    async def get_board_scope(
        self,
        workspace_slug: str,
        project_id: str,
        board_id: str | None = None,
        sprint_ids: list[str] | None = None,
    ) -> dict:
        return {"board_id": None, "scope": "all", "sprint": None, "sprints": []}
