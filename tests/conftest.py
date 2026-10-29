from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def allow_test_provider_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unit tests off DNS while exercising the allowlist path.

    Production defaults only trust known SaaS hosts; tests use reserved
    example domains and explicitly allowlist them here.
    """
    monkeypatch.setenv(
        "PM_GATEWAY_ALLOWED_PROVIDER_HOSTS",
        "remote.example,jira.example.test,tools.example.test,example.test",
    )
