# Changelog

All notable changes to `opennomos-pm-gateway` are documented here. The package
follows [Semantic Versioning](https://semver.org/); the HTTP contract is
versioned separately in the OpenAPI document.

## [Unreleased]

### Added

- monday.com provider adapter (`Provider.MONDAY` / `X-PM-Provider: monday`).
  Uses the monday.com GraphQL API with a personal API token; boards map to
  containers, board groups to workflow states, and board columns to normalized
  work-item fields and custom fields. Reads paginate through `items_page`,
  transitions move items between groups, and `api.monday.com` is allowlisted by
  default.
- Asana provider adapter (`Provider.ASANA` / `X-PM-Provider: asana`).
  Uses the Asana REST API with a personal access token; projects map to
  containers, project sections to workflow states, and task custom fields to
  normalized custom fields. Reads paginate through `next_page.offset`,
  transitions move tasks between sections, multi-project memberships are
  preserved, and `app.asana.com` is allowlisted by default.
- Live Asana compatibility suite (`tests/live/test_asana_live.py`) gated on
  `ASANA_LIVE=1`, with an opt-in create/move/delete lifecycle on a disposable
  project and a generated compatibility report (`reports/asana-live/`). CI runs
  it when the `ASANA_API_TOKEN` secret is present and publishes the report as an
  artifact and workflow summary.
- ClickUp provider adapter (`Provider.CLICKUP` / `X-PM-Provider: clickup`).
  Uses the ClickUp REST API v2 with a personal API token; Lists map to
  containers, List statuses to workflow states, and task custom fields to
  normalized custom fields. Reads paginate through the `last_page` flag,
  transitions move tasks between statuses, and `api.clickup.com` is allowlisted
  by default.
- Trello provider adapter (`Provider.TRELLO` / `X-PM-Provider: trello`).
- Basecamp provider adapter (`Provider.BASECAMP` / `X-PM-Provider: basecamp`).
- GitHub Issues provider adapter (`Provider.GITHUB` / `X-PM-Provider: github`).
- Jira HTTP mode (`X-PM-Provider-Mode: http-client`, now the default) backed by
  the generated Jira OpenAPI client, alongside the existing `sdk` mode.
- `scripts/generate-provider-clients.sh`, which generates filtered provider HTTP
  clients from downloaded OpenAPI documents.
- Provider spec freshness test (`tests/test_provider_spec_freshness.py`, live).
- `scripts/download-provider-specs.sh`, which downloads the OpenAPI documents
  that PM providers publish for their own APIs into one directory for reference
  during adapter work. It never invokes OpenAPI Generator; generated gateway code
  still comes only from `spec/pm-api-openapi-v0.1.yaml`.
- Live monday.com compatibility suite (`tests/live/`) gated on `MONDAY_LIVE=1`,
  with an opt-in create/move/delete lifecycle on a disposable board and a
  generated compatibility report (`reports/monday-live/`). CI runs it when the
  `MONDAY_API_TOKEN` secret is present and publishes the report as an artifact
  and workflow summary.
- `pm-gateway-conformance` checker and `pm_gateway.conformance` module for
  verifying third-party implementations of the standard API. Read-only by
  default; `--write`, `--transition-to`, and `--full` enable optional checks.
- Compliance implementation guide
  (`docs/compliance-implementation-guide.md`) with OpenAPI scaffold generation,
  per-endpoint behavior, authentication, pagination, merge-patch, and ETag
  guidance.

### Changed

- Package versions are derived from git tags with `setuptools-scm`.
- Tagged `v*` releases publish the exact tag version to PyPI. TestPyPI
  publishing is disabled in CI for now.

### Security

- Pre-push secret scanning (`hooks/pre-push`, installed with
  `./scripts/install-hooks.sh`, also usable as a `pre-commit` pre-push stage).
  Blocks pushes whose commits add credential files (`.env`, `*.pem`, `id_rsa`, …)
  or provider token patterns for monday.com, Plane, GitHub, Atlassian, AWS,
  OpenAI, and Slack. Asana personal access tokens have no distinguishing prefix,
  so provider-prefixed assignments such as `ASANA_API_TOKEN=...` are covered by
  the generic credential-assignment rule. Scoped to the pushed range, overridable
  per push with `ALLOW_SECRETS=1`, and allowlistable per line with
  `# secret-scan:allow`.
- CI `secret-scan` job runs gitleaks over the pushed range with a pinned,
  checksum-verified binary; publishing a release now depends on it.

## [0.1.0a1] - 2026-10-29

First alpha technical preview.

### Added

- In-process Python SDK (`PMGateway`, `Operation`, `Provider`) that calls provider
  adapters without starting the gateway service.
- Optional FastAPI HTTP service for the draft PM interoperability API, mounted at
  `/pm/v1`, with a fail-closed bearer-token check and an unauthenticated
  `/healthz` liveness endpoint.
- Provider adapters for Plane, Jira, and Microsoft Planner, plus a `compliant`
  passthrough for third-party software that implements the standard API.
- `get_capabilities` reporting per-provider feature support.
- Workspace, container, board, iteration, workflow-state, and work-item reads;
  workflow transitions; and conditional work-item updates for compliant
  providers.
- ETag/`If-Match` propagation, RFC 7386 merge-patch semantics, and `Retry-After`
  forwarding for compliant providers.
- Apache-2.0 licensing, PEP 561 `py.typed` marker, and complete PyPI metadata.

### Security

- Provider base URLs must use HTTPS and target public hosts unless explicitly
  allowlisted; private, loopback, link-local, and metadata addresses are
  rejected. Planner is restricted to Microsoft Graph endpoints.
- Native provider identifiers are validated before use in Jira, Plane, and
  Planner paths; Jira project/issue/board/sprint IDs are validated to prevent
  JQL and path injection. Compliant path segments reject dot-segment traversal.
- Upstream error bodies are hidden by default and bounded when explicitly
  enabled with `PM_GATEWAY_EXPOSE_UPSTREAM_ERRORS`.
- Jira requests now have a timeout and do not follow redirects.
- Request bodies are size-limited, security headers are added, and HTTP
  provider URLs require an explicit development opt-in.
- Docker and CI hardening: pinned base image/actions, non-root runtime with
  dropped capabilities, image scanning, dependency auditing, and a single
  checksum-verified OpenAPI generator download.

### Known limitations

- Comments, attachments, custom-field writes, milestones, and general work-item
  updates are not implemented for native providers.
- Pagination is computed over a full provider fetch rather than provider-native
  cursors.
- Microsoft Planner has not been validated against a live tenant.
- The SDK raises `fastapi.HTTPException` for provider errors; a framework-neutral
  exception hierarchy is planned before 1.0.
- No live-provider CI or semantic conformance suite yet; the
  `pm-gateway-conformance` checker covers wire-level behavior only.
