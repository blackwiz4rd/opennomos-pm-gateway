from __future__ import annotations

import socket

import pytest

from pm_gateway.adapters import jira as jira_adapter
from pm_gateway.adapters.factory import validate_provider_context
from pm_gateway.adapters.jira import JiraProvider
from pm_gateway.adapters.planner import PlannerProvider
from pm_gateway.security import (
    path_segment,
    safe_url,
    token_matches,
    validate_native_id,
    validate_native_identifiers,
    validate_provider_destination,
)


def test_http_provider_url_is_rejected_by_default() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        validate_provider_context("compliant", "secret", "http://remote.example")


def test_http_provider_url_requires_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PM_GATEWAY_ALLOW_INSECURE_HTTP", "1")
    validate_provider_context("compliant", "secret", "http://remote.example")


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]",
        "https://10.0.0.1",
    ],
)
def test_private_and_metadata_addresses_are_rejected(url: str) -> None:
    with pytest.raises(ValueError, match="public host"):
        validate_provider_context("jira", "secret", url)


def test_explicit_allowlist_can_trust_a_private_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PM_GATEWAY_ALLOWED_PROVIDER_HOSTS", "127.0.0.1,*.internal.test")
    validate_provider_context("jira", "secret", "https://127.0.0.1")
    validate_provider_context("jira", "secret", "https://jira.internal.test")


def test_known_saas_hosts_are_allowed_by_default() -> None:
    validate_provider_context("jira", "secret", "https://example.atlassian.net")
    validate_provider_context("plane", "secret", "https://api.plane.so")


def test_unknown_public_host_requires_an_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PM_GATEWAY_ALLOWED_PROVIDER_HOSTS", raising=False)
    with pytest.raises(ValueError, match="not allowlisted"):
        validate_provider_context("compliant", "secret", "https://tools.example.com")


def test_planner_is_restricted_to_graph_hosts() -> None:
    validate_provider_context(
        "planner", "tenant:client:secret", "https://graph.microsoft.com/v1.0"
    )
    with pytest.raises(ValueError, match="Microsoft Graph"):
        validate_provider_context("planner", "tenant:client:secret", "https://attacker.example")
    with pytest.raises(ValueError, match="HTTPS"):
        validate_provider_context("planner", "tenant:client:secret", "http://graph.microsoft.com")


def test_planner_adapter_rejects_a_custom_host() -> None:
    with pytest.raises(ValueError, match="Microsoft Graph"):
        PlannerProvider("tenant:client:secret", "https://attacker.example")


@pytest.mark.asyncio
async def test_destination_validation_rejects_private_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PM_GATEWAY_ALLOWED_PROVIDER_HOSTS", raising=False)

    def fake_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.10", port))]

    monkeypatch.setattr("pm_gateway.security.socket.getaddrinfo", fake_getaddrinfo)
    with pytest.raises(ValueError, match="public host"):
        await validate_provider_destination("jira", "https://jira.example.test")


@pytest.mark.asyncio
async def test_destination_validation_allows_public_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PM_GATEWAY_ALLOWED_PROVIDER_HOSTS", raising=False)

    def fake_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]

    monkeypatch.setattr("pm_gateway.security.socket.getaddrinfo", fake_getaddrinfo)
    await validate_provider_destination("jira", "https://jira.example.test")


@pytest.mark.asyncio
async def test_allowlisted_hosts_skip_dns_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PM_GATEWAY_ALLOWED_PROVIDER_HOSTS", "jira.internal.test")

    def fail_getaddrinfo(*args, **kwargs):
        raise AssertionError("DNS resolution should not run for an allowlisted host")

    monkeypatch.setattr("pm_gateway.security.socket.getaddrinfo", fail_getaddrinfo)
    await validate_provider_destination("jira", "https://jira.internal.test")


@pytest.mark.parametrize(
    "value",
    [
        "ABC OR project = SECRET",
        "../admin",
        "a/b",
        ".",
        "..",
        "a%2f",
        "project?x=1",
        "project#fragment",
    ],
)
def test_native_ids_reject_path_and_query_injection(value: str) -> None:
    with pytest.raises(ValueError, match="provider-native identifier"):
        validate_native_id(value, "containerId")


@pytest.mark.parametrize("value", ["ABC", "ABC_1", "ABC-123", "0f8fad5b-d9cb-469f-a165-70867728950e"])
def test_native_ids_accept_provider_identifiers(value: str) -> None:
    assert validate_native_id(value, "containerId") == value


def test_native_identifier_validation_rejects_jql_injection() -> None:
    with pytest.raises(ValueError, match="containerId"):
        validate_native_identifiers("jira", {"containerId": "ABC OR project = SECRET"})
    validate_native_identifiers("jira", {"containerId": "ABC"})


def test_path_segment_rejects_dot_segments() -> None:
    for value in ("", ".", "..", "a\x00b"):
        with pytest.raises(ValueError):
            path_segment(value)
    assert "/" not in path_segment("../../admin")
    assert path_segment("a/b") == "a%2Fb"


def test_safe_url_rejects_non_http_and_credentialed_urls() -> None:
    assert safe_url("javascript:alert(1)") is None
    assert safe_url("data:text/html,boom") is None
    assert safe_url("https://user:password@example.com") is None
    assert safe_url("https://example.com/path?x=1#frag") == "https://example.com/path?x=1#frag"


def test_token_comparison_handles_non_ascii() -> None:
    assert token_matches("café", "café")
    assert not token_matches("café", "cafe")


def test_jira_client_sets_timeout_and_disables_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSession:
        max_redirects = 30

    class FakeJira:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self._session = FakeSession()

    monkeypatch.setattr(jira_adapter, "JIRA", FakeJira)
    client = JiraProvider._build_client("https://jira.example.test", "token")
    assert client.kwargs["timeout"] == 30.0
    assert client._session.max_redirects == 0
