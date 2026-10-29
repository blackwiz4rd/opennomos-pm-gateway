# Changelog

All notable changes to `opennomos-pm-gateway` are documented here. The package
follows [Semantic Versioning](https://semver.org/); the HTTP contract is
versioned separately in the OpenAPI document.

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
- No conformance suite, public governance process, or live-provider CI yet.
