"""Live-provider test configuration.

Credentials are read from the process environment or from the repository's
``.env`` file. Only provider-prefixed keys are loaded, and only if the same key
is not already set. Values are never printed or written to the report.
"""

from __future__ import annotations

import contextlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Each live suite writes its own compatibility matrix. The directory and the
# report marker are derived from the provider prefix so the monday.com report
# keeps its historical location.
_PROVIDERS = {
    "monday": {"prefix": "MONDAY_", "slug": "monday", "title": "monday.com"},
    "asana": {"prefix": "ASANA_", "slug": "asana", "title": "Asana"},
    "clickup": {"prefix": "CLICKUP_", "slug": "clickup", "title": "ClickUp"},
}


def _provider_prefix() -> str:
    """Return the credential prefix for the suite currently being collected.

    A run can target one provider explicitly with ``PM_GATEWAY_LIVE_PROVIDER``.
    Otherwise the prefix is inferred from the live suites that are enabled, so
    the monday.com report keeps its historical directory and marker.
    """
    selected = os.getenv("PM_GATEWAY_LIVE_PROVIDER", "").strip().lower()
    if selected in _PROVIDERS:
        return _PROVIDERS[selected]["prefix"]
    if os.getenv("CLICKUP_LIVE", "").strip() and not os.getenv("MONDAY_LIVE", "").strip():
        return _PROVIDERS["clickup"]["prefix"]
    if os.getenv("ASANA_LIVE", "").strip() and not os.getenv("MONDAY_LIVE", "").strip():
        return _PROVIDERS["asana"]["prefix"]
    return _PROVIDERS["monday"]["prefix"]


_PREFIX = _provider_prefix()
_SLUG = next(
    (config["slug"] for config in _PROVIDERS.values() if config["prefix"] == _PREFIX),
    "monday",
)
_TITLE = next(
    (config["title"] for config in _PROVIDERS.values() if config["prefix"] == _PREFIX),
    "monday.com",
)

REPORT_DIR = Path(
    os.getenv(
        f"{_PREFIX}LIVE_REPORT_DIR",
        str(REPO_ROOT / "reports" / f"{_SLUG}-live"),
    )
)

_REPORT_MARKER = f"<!-- {_SLUG}-live-compatibility -->"


def _load_dotenv(path: Path) -> None:
    """Load provider-prefixed keys from a dotenv file without exposing values."""
    if not path.is_file():
        return
    prefixes = tuple(config["prefix"] for config in _PROVIDERS.values())
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        if not key.startswith(prefixes):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_dotenv(REPO_ROOT / ".env")

_RESULTS: list[dict[str, str]] = []


@pytest.fixture
def check():
    """Record one compatibility-matrix row for the enclosing test body."""

    @contextlib.contextmanager
    def _check(capability: str):
        entry = {"capability": capability, "status": "PENDING", "detail": ""}
        _RESULTS.append(entry)
        try:
            yield entry
        except pytest.skip.Exception as exc:
            entry["status"] = "SKIP"
            entry["detail"] = str(exc)
            raise
        except BaseException as exc:
            entry["status"] = "FAIL"
            # Exception text can contain provider identifiers; keep it short.
            entry["detail"] = type(exc).__name__
            raise
        else:
            if entry["status"] == "PENDING":
                entry["status"] = "PASS"
        return entry

    return _check


def _write_report(exitstatus: int) -> None:
    if not _RESULTS:
        return
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        _REPORT_MARKER,
        f"# {_TITLE} compatibility report",
        "",
        f"- Generated: {generated}",
        f"- pytest exit status: {exitstatus}",
        "",
        "| Capability | Result | Detail |",
        "| --- | --- | --- |",
    ]
    for entry in _RESULTS:
        detail = entry["detail"].replace("|", "\\|")
        lines.append(
            f"| {entry['capability']} | {entry['status']} | {detail} |"
        )
    markdown = "\n".join(lines) + "\n"

    (REPORT_DIR / "compatibility.md").write_text(markdown, encoding="utf-8")
    (REPORT_DIR / "compatibility.json").write_text(
        json.dumps(
            {
                "generated": generated,
                "exitStatus": exitstatus,
                "results": _RESULTS,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    summary_path = os.getenv("GITHUB_STEP_SUMMARY", "").strip()
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(markdown + "\n")


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    _write_report(int(exitstatus))
