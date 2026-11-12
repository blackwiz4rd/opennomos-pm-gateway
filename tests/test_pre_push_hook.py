from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / "hooks" / "pre-push"
INSTALLER = REPO_ROOT / "scripts" / "install-hooks.sh"
ZERO_SHA = "0" * 40

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git is required to exercise the hook"
)

GITHUB_TOKEN = "ghp_" + "0" * 36
MONDAY_JWT = "eyJhbGciOiJIUzI1NiJ9." + "eyJ0aWQiO" + "jcyMTE2OTk3NSwiYWFpIjoxMX0"
PLANE_KEY = "plane_api_" + "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d"
HIGH_ENTROPY_VALUE = "9f8e7d6c5b4a39281706f5e4d3c2b1a0"
PEM_KEY = "-----BEGIN " + "PRIVATE KEY-----\nzzz\n"


def git(repo: Path, *args: str, stdin: str | None = None, check: bool = True):
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        input=stdin,
        text=True,
        capture_output=True,
        check=check,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "GIT_AUTHOR_NAME": "Hook Test",
            "GIT_AUTHOR_EMAIL": "hook-test@example.invalid",
            "GIT_COMMITTER_NAME": "Hook Test",
            "GIT_COMMITTER_EMAIL": "hook-test@example.invalid",
            "GIT_CONFIG_NOSYSTEM": "1",
            "HOME": str(repo),
        },
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "README.md").write_text("seed\n")
    git(tmp_path, "add", "README.md")
    git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def commit_file(repo: Path, name: str, content: str) -> str:
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    git(repo, "add", "-f", name)
    git(repo, "commit", "-qm", f"add {name}")
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def run_hook(repo: Path, sha: str, remote_sha: str = ZERO_SHA, extra_env=None):
    stdin = f"refs/heads/main {sha} refs/heads/main {remote_sha}\n"
    return subprocess.run(
        ["bash", "-c", f'"{HOOK}"', "pre-push"],
        cwd=repo,
        input=stdin,
        text=True,
        capture_output=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(repo),
            **(extra_env or {}),
        },
    )


def test_hook_and_installer_are_executable() -> None:
    assert HOOK.is_file(), "hooks/pre-push is missing"
    assert HOOK.stat().st_mode & 0o111, "hooks/pre-push must be executable"
    assert INSTALLER.is_file(), "scripts/install-hooks.sh is missing"
    assert INSTALLER.stat().st_mode & 0o111, "scripts/install-hooks.sh must be executable"


def test_clean_commit_is_allowed(repo: Path) -> None:
    sha = commit_file(repo, "app.py", "print('hello')\n")
    result = run_hook(repo, sha)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("settings.py", f'TOKEN = "{GITHUB_TOKEN}"\n'),
        ("monday.py", f"MONDAY_API_TOKEN={MONDAY_JWT}\n"),
        ("plane.py", f"PLANE_API_KEY={PLANE_KEY}\n"),
        ("asana.py", f'ASANA_API_TOKEN = "{HIGH_ENTROPY_VALUE}"\n'),
        ("credentials.py", f'client_secret = "{HIGH_ENTROPY_VALUE}"\n'),
        ("keys/server.pem", PEM_KEY),
        (".env", "MONDAY_API_TOKEN=abcdef\n"),
        ("id_rsa", "whatever\n"),
    ],
)
def test_secrets_are_blocked(repo: Path, name: str, content: str) -> None:
    sha = commit_file(repo, name, content)
    result = run_hook(repo, sha)
    assert result.returncode == 1, result.stdout
    assert "Push blocked" in result.stderr


@pytest.mark.parametrize(
    ("name", "content"),
    [
        (".env.example", "MONDAY_API_TOKEN=your_token_here\n"),
        (".env.example", "ASANA_API_TOKEN=your_token_here\n"),
        ("docs/guide.md", "Set `api_key` to your own value in the environment.\n"),
        ("ok.py", 'api_key = "placeholder-value-000000"  # secret-scan:allow\n'),
        ("code.py", "api_key = self._credential\nclient_secret = settings.client_secret\n"),
    ],
)
def test_placeholders_are_allowed(repo: Path, name: str, content: str) -> None:
    sha = commit_file(repo, name, content)
    result = run_hook(repo, sha)
    assert result.returncode == 0, result.stderr


def test_allow_secrets_override(repo: Path) -> None:
    sha = commit_file(repo, "leak.py", f'TOKEN = "{GITHUB_TOKEN}"\n')
    assert run_hook(repo, sha).returncode == 1
    assert run_hook(repo, sha, extra_env={"ALLOW_SECRETS": "1"}).returncode == 0


def test_only_pushed_commits_are_scanned(repo: Path) -> None:
    """A secret in a commit that is already on the remote must not block again."""
    safe = commit_file(repo, "app.py", "print('hello')\n")
    run_hook(repo, safe)  # would pass: nothing sensitive
    secret = commit_file(repo, "leak.py", f'TOKEN = "{GITHUB_TOKEN}"\n')
    # Range safe..secret contains the leaking commit.
    assert run_hook(repo, secret, remote_sha=safe).returncode == 1
    # Range secret..secret is empty: the earlier finding is not re-reported.
    assert run_hook(repo, secret, remote_sha=secret).returncode == 0


def test_installer_round_trip(tmp_path: Path) -> None:
    repo = tmp_path / "work"
    repo.mkdir()
    shutil.copytree(REPO_ROOT / "hooks", repo / "hooks")
    git(repo, "init", "-q", "-b", "main")

    def install(*args: str):
        return subprocess.run(
            ["bash", str(INSTALLER), *args],
            cwd=repo,
            text=True,
            capture_output=True,
            env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(repo)},
        )

    assert install().returncode == 0
    installed = repo / ".git" / "hooks" / "pre-push"
    assert installed.is_file() and installed.stat().st_mode & 0o111
    assert install("--check").returncode == 0
    assert install().returncode == 0  # idempotent
    assert install("--uninstall").returncode == 0
    assert install("--check").returncode == 1


def test_installer_refuses_to_replace_a_foreign_hook(tmp_path: Path) -> None:
    repo = tmp_path / "work"
    (repo / "hooks").mkdir(parents=True)
    shutil.copy(HOOK, repo / "hooks" / "pre-push")
    git(repo, "init", "-q", "-b", "main")
    target = repo / ".git" / "hooks" / "pre-push"
    target.write_text("#!/bin/sh\necho foreign\n")
    target.chmod(0o755)

    def install(*args: str):
        return subprocess.run(
            ["bash", str(INSTALLER), *args],
            cwd=repo,
            text=True,
            capture_output=True,
            env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(repo)},
        )

    assert install().returncode == 1
    assert "foreign" in target.read_text()
    assert install("--force").returncode == 0
    assert "pm-gateway secret scan" in target.read_text()
