"""Jira provider — implements the generic PMProvider interface.

Uses the ``jira`` package (pycontribs/jira, https://jira.readthedocs.io).
Jira concepts map onto the interface as follows:

* workspace_slug -> Jira site (the credential's base_url, e.g.
  ``https://<site>.atlassian.net``)
* project_id     -> Jira project key (e.g. ``SIDUS``)
* work_item_id   -> Jira issue key (e.g. ``SIDUS-13``)
* state          -> Jira status (matched by name or id via transitions)

Authentication: the credential ``api_key`` is either ``email:token``
(basic auth — Jira Cloud) or a raw token sent as a Bearer token
(Jira Server/Data Center personal access tokens).
"""

import asyncio
import logging
import re

from jira import JIRA
from jira.exceptions import JIRAError
from jira.resources import Issue

from pm_gateway.adapters.models import NormalizedWorkItem
from pm_gateway.adapters.normalization import (
    format_date,
    format_datetime,
    normalize_priority_value,
    normalize_state_category,
)
from pm_gateway.security import provider_timeout_seconds

logger = logging.getLogger("pm_gateway.adapters.jira")

# Expands understood by the Jira API; anything else (e.g. Plane's "state")
# is dropped so calls stay valid.
_VALID_EXPANDS = {
    "transitions",
    "renderedFields",
    "changelog",
    "names",
    "schema",
    "editmeta",
}

_ISSUE_KEY_RE = re.compile(r"^(?P<identifier>[A-Za-z][A-Za-z0-9]*)-(?P<sequence>\d+)$")
_JIRA_PROJECT_ID_RE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9_]*|\d+)$")
_JIRA_ISSUE_ID_RE = re.compile(r"^(?:[A-Za-z][A-Za-z0-9_]*-\d+|\d+)$")
_JIRA_NUMERIC_ID_RE = re.compile(r"^\d+$")


def _validate_project_id(project_id: str) -> str:
    value = str(project_id)
    if not _JIRA_PROJECT_ID_RE.fullmatch(value):
        raise ValueError("Invalid Jira project key or ID.")
    return value


def _validate_issue_id(work_item_id: str) -> str:
    value = str(work_item_id)
    if not _JIRA_ISSUE_ID_RE.fullmatch(value):
        raise ValueError("Invalid Jira issue key or ID.")
    return value


def _validate_numeric_id(value: str, field: str) -> str:
    text = str(value)
    if not _JIRA_NUMERIC_ID_RE.fullmatch(text):
        raise ValueError(f"Invalid Jira {field}.")
    return text

# Jira answers HTTP 400 with these messages when a board cannot serve the
# requested agile scope (e.g. Kanban boards with no sprints or backlog).
_UNSUPPORTED_BOARD_MESSAGES = (
    "does not support sprints",
    "Backlogs are not supported",
)


def _is_unsupported_board_error(exc: JIRAError) -> bool:
    text = str(getattr(exc, "text", "") or "")
    return getattr(exc, "status_code", None) == 400 and any(
        message in text for message in _UNSUPPORTED_BOARD_MESSAGES
    )


class JiraProvider:
    provider_key = "jira"

    def __init__(self, credential: str, base_url: str | None = None) -> None:
        self._credential = credential
        self._base_url = (base_url or "").strip().rstrip("/")
        self._clients: dict[str, JIRA] = {}
        self._lock = asyncio.Lock()

    async def invalidate(self) -> None:
        """Drop all cached clients so the next call rebuilds from current settings."""
        for client in self._clients.values():
            close = getattr(client, "close", None)
            if close is not None:
                try:
                    await asyncio.to_thread(close)
                except Exception as exc:  # pragma: no cover - best-effort cleanup
                    logger.debug("Could not close Jira client: %s", type(exc).__name__)
        self._clients.clear()

    async def _ensure(self, workspace_slug: str) -> JIRA:
        if workspace_slug in self._clients:
            return self._clients[workspace_slug]

        async with self._lock:
            if workspace_slug in self._clients:
                return self._clients[workspace_slug]

            if not self._credential:
                raise RuntimeError(
                    f"Jira credential for workspace '{workspace_slug}' has an empty key."
                )

            if not self._base_url:
                raise RuntimeError(
                    f"Jira credential for workspace '{workspace_slug}' has no base URL."
                )

            client = self._build_client(self._base_url, self._credential)
            self._clients[workspace_slug] = client
            return client

    @staticmethod
    def _build_client(base_url: str, api_key: str) -> JIRA:
        timeout = provider_timeout_seconds()
        if ":" in api_key:
            email, token = api_key.split(":", 1)
            client = JIRA(
                server=base_url,
                basic_auth=(email.strip(), token.strip()),
                timeout=timeout,
            )
        else:
            client = JIRA(server=base_url, token_auth=api_key, timeout=timeout)
        # Jira requests carry the provider credential. Do not follow redirects:
        # a hostile or compromised site could otherwise receive the credential.
        client._session.max_redirects = 0
        return client

    # ── normalization ──

    @staticmethod
    def _project_to_dict(project) -> dict:
        return {
            "id": project.key,
            "key": project.key,
            "identifier": project.key,
            "name": project.name,
        }

    @staticmethod
    def _epic_to_dict(candidate) -> dict | None:
        """Return ``{id, key, name}`` when ``candidate`` is an Epic, else ``None``."""
        if candidate is None:
            return None
        candidate_fields = getattr(candidate, "fields", None)
        candidate_type = (
            getattr(candidate_fields, "issuetype", None)
            if candidate_fields is not None
            else None
        )
        type_name = (getattr(candidate_type, "name", "") or "").strip().lower()
        key = getattr(candidate, "key", None)
        if type_name != "epic" or not key:
            return None
        return {
            "id": getattr(candidate, "id", None),
            "key": key,
            "name": getattr(candidate_fields, "summary", None),
        }

    @staticmethod
    def _epic_from_fields(fields) -> dict | None:
        """Resolve the issue's epic via the ``parent`` field or a custom epic-link field.

        Modern Jira Cloud (team-managed and company-managed) exposes the epic as
        the issue's ``parent``. Older company-managed projects use an "Epic Link"
        custom field, whose id varies per instance — so scan all custom fields for
        an issue-like value whose type is an Epic.
        """
        epic = JiraProvider._epic_to_dict(getattr(fields, "parent", None))
        if epic is not None:
            return epic
        for name, value in vars(fields).items():
            if not name.startswith("customfield_"):
                continue
            epic = JiraProvider._epic_to_dict(value)
            if epic is not None:
                return epic
        return None

    @staticmethod
    def _issue_to_dict(issue) -> dict:
        """Convert a Jira issue to the normalized work item model."""
        fields = issue.fields
        sequence_id = None
        match = _ISSUE_KEY_RE.match(issue.key or "")
        if match:
            sequence_id = int(match.group("sequence"))

        status = getattr(fields, "status", None)
        status_name = getattr(status, "name", None)
        status_id = getattr(status, "id", None)
        status_category_obj = getattr(status, "statusCategory", None)
        status_category = None
        if status_category_obj:
            status_category = normalize_state_category(
                getattr(status_category_obj, "name", None)
            )

        issuetype = getattr(fields, "issuetype", None)
        type_name = getattr(issuetype, "name", None)

        assignee = getattr(fields, "assignee", None)
        assignee_name = getattr(assignee, "displayName", None) if assignee else None
        assignee_id = None
        if assignee:
            assignee_id = getattr(assignee, "accountId", None) or getattr(assignee, "key", None)

        priority = getattr(fields, "priority", None)
        priority_name = getattr(priority, "name", None) if priority else None

        fix_versions = getattr(fields, "fixVersions", None) or []
        versions = [
            getattr(version, "name", None)
            for version in fix_versions
            if getattr(version, "name", None)
        ]

        components = getattr(fields, "components", None) or []
        component_names = [
            getattr(c, "name", None) for c in components if getattr(c, "name", None)
        ]

        labels = getattr(fields, "labels", None) or []

        reporter = getattr(fields, "reporter", None)
        creator_name = getattr(reporter, "displayName", None) if reporter else None
        creator_id = None
        if reporter:
            creator_id = getattr(reporter, "accountId", None) or getattr(reporter, "key", None)

        project = getattr(fields, "project", None)
        project_id = getattr(project, "key", None) if project else None
        project_name = getattr(project, "name", None) if project else None

        parent = getattr(fields, "parent", None)
        parent_id = getattr(parent, "id", None) if parent else None

        epic_info = JiraProvider._epic_from_fields(fields)
        epic_id = epic_info.get("id") if epic_info else None
        epic_name = epic_info.get("name") if epic_info else None

        estimate = None
        for name, value in vars(fields).items():
            if name.startswith("customfield_") and isinstance(value, (int, float)):
                estimate = float(value)
                break

        return NormalizedWorkItem(
            id=issue.key,
            identifier=issue.key,
            title=getattr(fields, "summary", "") or "",
            description=getattr(fields, "description", None),
            state=status_name,
            state_id=status_id,
            state_category=status_category,
            priority=priority_name,
            priority_value=normalize_priority_value(priority_name, "jira"),
            estimate=estimate,
            start_date=format_date(getattr(fields, "startdate", None)),
            due_date=format_date(getattr(fields, "duedate", None)),
            completed_at=format_datetime(getattr(fields, "resolutiondate", None)),
            created_at=format_datetime(getattr(fields, "created", None)),
            updated_at=format_datetime(getattr(fields, "updated", None)),
            assignee=assignee_name,
            assignee_id=assignee_id,
            creator=creator_name,
            creator_id=creator_id,
            parent_id=parent_id,
            epic_id=epic_id,
            epic_name=epic_name,
            project_id=project_id,
            project_name=project_name,
            labels=list(labels),
            item_type=type_name,
            components=component_names,
            fix_versions=versions,
            provider="jira",
            raw={},
        ).model_dump()

    @staticmethod
    def _status_to_dict(status) -> dict:
        return {
            "id": status.id,
            "name": status.name,
            "description": getattr(status, "description", None),
        }

    # ── PMProvider ──

    async def validate_slug(self, slug: str) -> dict | None:
        try:
            projects = await self.list_projects(slug)
            return {"slug": slug, "valid": True, "projects_count": len(projects)}
        except Exception:
            return None

    async def list_projects(self, workspace_slug: str) -> list[dict]:
        client = await self._ensure(workspace_slug)
        result = await asyncio.to_thread(client.projects)
        return [self._project_to_dict(p) for p in result]

    async def list_work_items(
        self,
        workspace_slug: str,
        project_id: str,
        expand: str | None = None,
        board_id: str | None = None,
        sprint_ids: list[str] | None = None,
    ) -> list[dict]:
        client = await self._ensure(workspace_slug)
        valid_expand = expand if expand in _VALID_EXPANDS else None

        resolved_board = await self._resolve_board_id(client, project_id, board_id)
        if resolved_board is None:
            return await self._project_issues(client, project_id, valid_expand)

        issues = await self._board_issues(
            client, resolved_board, sprint_ids=sprint_ids, project_id=project_id
        )
        if issues is None:
            # The board supports neither sprints nor a backlog (e.g. some
            # team-managed boards): fall back to the flat project search.
            return await self._project_issues(client, project_id, valid_expand)
        return [self._issue_to_dict(i) for i in issues]

    async def _project_issues(
        self, client: JIRA, project_id: str, expand: str | None
    ) -> list[dict]:
        project_id = _validate_project_id(project_id)
        jql = f"project = {project_id} ORDER BY created DESC"
        result = await asyncio.to_thread(
            client.search_issues,
            jql,
            maxResults=0,
            expand=expand,
        )
        return [self._issue_to_dict(i) for i in result]

    async def list_boards(self, workspace_slug: str, project_id: str) -> list[dict]:
        client = await self._ensure(workspace_slug)
        boards = await asyncio.to_thread(
            client.boards,
            projectKeyOrID=project_id,
            maxResults=0,
        )
        return [
            {
                "id": str(getattr(board, "id", "")),
                "name": getattr(board, "name", "") or str(getattr(board, "id", "")),
                "type": getattr(board, "type", None),
            }
            for board in boards
        ]

    async def list_sprints(
        self, workspace_slug: str, project_id: str, board_id: str | None = None
    ) -> list[dict]:
        client = await self._ensure(workspace_slug)
        resolved_board = await self._resolve_board_id(client, project_id, board_id)
        if resolved_board is None:
            return []
        sprints = await self._board_sprints(
            client, resolved_board, maxResults=0, state="active"
        )
        result = [
            {
                "id": str(getattr(sprint, "id", "")),
                "name": getattr(sprint, "name", None),
                "state": "active",
            }
            for sprint in sprints
        ]
        result.sort(key=lambda s: int(s["id"]), reverse=True)
        result.append({"id": "backlog", "name": "Backlog", "state": "backlog"})
        return result

    async def get_board_scope(
        self,
        workspace_slug: str,
        project_id: str,
        board_id: str | None = None,
        sprint_ids: list[str] | None = None,
    ) -> dict:
        client = await self._ensure(workspace_slug)
        resolved_board = await self._resolve_board_id(client, project_id, board_id)
        if resolved_board is None:
            return {"board_id": None, "scope": "all", "sprint": None, "sprints": []}
        if sprint_ids:
            sprints = []
            has_backlog = False
            for sid in sprint_ids:
                if sid == "backlog":
                    has_backlog = True
                    sprints.append({"id": "backlog", "name": "Backlog"})
                    continue
                sid = _validate_numeric_id(sid, "sprint ID")
                sprint = await self._sprint_by_id(client, resolved_board, sid)
                if sprint is not None:
                    sprints.append({
                        "id": str(getattr(sprint, "id", "")),
                        "name": getattr(sprint, "name", None),
                    })
            if sprints:
                only_backlog = has_backlog and len(sprints) == 1
                return {
                    "board_id": resolved_board,
                    "scope": "backlog" if only_backlog else "sprint",
                    "sprint": None if only_backlog else (sprints[0] if len(sprints) == 1 else None),
                    "sprints": sprints,
                }
        sprint = await self._active_sprint(client, resolved_board)
        if sprint is not None:
            return {
                "board_id": resolved_board,
                "scope": "sprint",
                "sprint": {
                    "id": str(getattr(sprint, "id", "")),
                    "name": getattr(sprint, "name", None),
                },
                "sprints": [],
            }
        return {"board_id": resolved_board, "scope": "backlog", "sprint": None, "sprints": []}

    async def _resolve_board_id(
        self, client: JIRA, project_id: str, board_id: str | None
    ) -> str | None:
        """Return the requested board, or a sensible default for the project.

        When the caller does not pin a board, Jira returns the project's boards
        in an arbitrary order that frequently starts with a kanban board (which
        has no sprints). Prefer a scrum board so sprint-aware features work out
        of the box, falling back to the first board otherwise.
        """
        if board_id:
            return _validate_numeric_id(board_id, "board ID")
        boards = list(
            await asyncio.to_thread(
                client.boards,
                projectKeyOrID=project_id,
                maxResults=0,
            )
        )
        if not boards:
            return None
        scrum = next(
            (b for b in boards if str(getattr(b, "type", "") or "").lower() == "scrum"),
            None,
        )
        chosen = scrum or boards[0]
        return str(getattr(chosen, "id", "")) or None

    async def _board_sprints(
        self, client: JIRA, board_id: str, **kwargs
    ) -> list:
        """Return a board's sprints, tolerating boards without sprint support.

        Kanban (and some team-managed) boards have no sprints; Jira answers
        HTTP 400 "The board does not support sprints". Treat that as an empty
        sprint list so the caller falls back to the board backlog.
        """
        try:
            return await asyncio.to_thread(client.sprints, board_id, **kwargs)
        except JIRAError as exc:
            if _is_unsupported_board_error(exc):
                logger.debug("Board %s does not support sprints", board_id)
                return []
            raise

    async def _active_sprint(self, client: JIRA, board_id: str):
        sprints = await self._board_sprints(
            client, board_id, maxResults=0, state="active"
        )
        return next(iter(sprints), None)

    async def _sprint_by_id(self, client: JIRA, board_id: str, sprint_id: str):
        sprints = await self._board_sprints(client, board_id, maxResults=0)
        for sprint in sprints:
            if str(getattr(sprint, "id", "")) == str(sprint_id):
                return sprint
        return None

    async def _board_issues(
        self,
        client: JIRA,
        board_id: str,
        sprint_ids: list[str] | None = None,
        project_id: str | None = None,
    ) -> list | None:
        """Load every issue of the board's active sprint, else its backlog.

        When ``sprint_ids`` is provided, fetch issues from each of those sprints.
        The special id ``"backlog"`` fetches the board's backlog. Returns ``None``
        when the board supports neither sprints nor a backlog.

        A board can be shared across projects (its filter spans several), so the
        result is narrowed to ``project_id`` when given.
        """
        board_id = _validate_numeric_id(board_id, "board ID")
        if sprint_ids:
            all_issues = []
            seen = set()
            for sid in sprint_ids:
                if sid == "backlog":
                    path = f"board/{board_id}/backlog"
                else:
                    sid = _validate_numeric_id(sid, "sprint ID")
                    path = f"board/{board_id}/sprint/{sid}/issue"
                result = await self._fetch_board_issues(client, path)
                if result is None:
                    return None
                for issue in result:
                    key = getattr(issue, "key", None)
                    if key and key not in seen:
                        seen.add(key)
                        all_issues.append(issue)
            issues = all_issues
        else:
            active_sprint = await self._active_sprint(client, board_id)
            if active_sprint is not None:
                path = f"board/{board_id}/sprint/{active_sprint.id}/issue"
            else:
                path = f"board/{board_id}/backlog"
            result = await self._fetch_board_issues(client, path)
            if result is None:
                return None
            issues = list(result)

        if project_id:
            issues = [i for i in issues if self._issue_project_key(i) == project_id]
        return issues

    @staticmethod
    def _issue_project_key(issue) -> str | None:
        project = getattr(getattr(issue, "fields", None), "project", None)
        return getattr(project, "key", None)

    async def _fetch_board_issues(self, client: JIRA, path: str) -> list | None:
        # maxResults=0 makes the library follow pagination until every issue is fetched.
        try:
            return await asyncio.to_thread(
                client._fetch_pages,
                Issue,
                "issues",
                path,
                0,
                0,
                {},
                client.AGILE_BASE_URL,
            )
        except JIRAError as exc:
            if _is_unsupported_board_error(exc):
                logger.debug("Agile scope %s is not supported by the board", path)
                return None
            raise

    async def get_work_item(
        self,
        workspace_slug: str,
        project_id: str,
        work_item_id: str,
    ) -> dict:
        client = await self._ensure(workspace_slug)
        work_item_id = _validate_issue_id(work_item_id)
        issue = await asyncio.to_thread(client.issue, work_item_id)
        return self._issue_to_dict(issue)

    async def _board_statuses(self, client: JIRA, board_id: str) -> list[dict] | None:
        """Return the statuses of a board's workflow, or None if unavailable.

        Jira boards are backed by a workflow whose columns map to statuses. The
        board configuration endpoint exposes those status ids; we resolve them
        against the site's status list. Returns None when the board config
        cannot be read so the caller can fall back to project-wide statuses.
        """
        board_id = _validate_numeric_id(board_id, "board ID")
        try:
            config = await asyncio.to_thread(
                client._get_json, f"agile/1.0/board/{board_id}/configuration"
            )
        except Exception:
            return None

        status_ids: set[str] = set()
        for column in (config or {}).get("columnConfig", {}).get("columns", []) or []:
            for sid in column.get("statuses", []) or []:
                status_ids.add(str(sid))
        if not status_ids:
            return None

        try:
            all_statuses = await asyncio.to_thread(client.statuses)
        except Exception:
            return None

        by_id = {str(s.id): s for s in all_statuses}
        out: list[dict] = []
        for sid in status_ids:
            status = by_id.get(sid)
            if status is not None:
                out.append(self._status_to_dict(status))
        return out or None

    async def list_states(
        self, workspace_slug: str, project_id: str, board_id: str | None = None
    ) -> list[dict]:
        client = await self._ensure(workspace_slug)
        if board_id:
            board_states = await self._board_statuses(client, board_id)
            if board_states is not None:
                return board_states
        issue_types = await asyncio.to_thread(client.issue_types_for_project, project_id)
        seen: dict[str, dict] = {}
        for issue_type in issue_types:
            for status in getattr(issue_type, "statuses", None) or []:
                key = (getattr(status, "name", "") or "").strip().lower() or str(
                    getattr(status, "id", "")
                )
                if key and key not in seen:
                    seen[key] = self._status_to_dict(status)
        return list(seen.values())

    async def update_work_item(
        self,
        workspace_slug: str,
        project_id: str,
        work_item_id: str,
        data: dict,
    ) -> dict:
        client = await self._ensure(workspace_slug)
        work_item_id = _validate_issue_id(work_item_id)
        target = (data or {}).get("state")
        if target:
            await asyncio.to_thread(self._transition, client, work_item_id, str(target))
        issue = await asyncio.to_thread(client.issue, work_item_id)
        return self._issue_to_dict(issue)

    @staticmethod
    def _transition(client: JIRA, issue_key: str, target: str) -> None:
        transitions = client.transitions(issue_key)
        for t in transitions:
            to = t.get("to") or {}
            if target in (t.get("id"), t.get("name"), to.get("id"), to.get("name")):
                client.transition_issue(issue_key, t["id"])
                return
        raise RuntimeError(f"No transition found on {issue_key} to status '{target}'.")
