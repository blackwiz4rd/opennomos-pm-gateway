# PM Gateway

Standalone, provider-neutral project-management interoperability SDK and
optional HTTP gateway. The Python package provides an in-process SDK and an HTTP
service for the draft PM interoperability API. It does not import OpenNomos
backend code or share its database, and is designed to be usable independently
of OpenNomos. The package lives in its own repository,
[`opennomos-pm-gateway`](https://github.com/blackwiz4rd/opennomos-pm-gateway).

> **Status:** `0.1.0a1` alpha technical preview. The Python API and the draft
> contract may change before 1.0. Microsoft Planner has not been validated
> against a live tenant.

## Install

Requires Python 3.11 or newer.

> Published on PyPI as
> [`opennomos-pm-gateway`](https://pypi.org/project/opennomos-pm-gateway/).

```sh
# SDK only
pip install opennomos-pm-gateway

# SDK plus the optional HTTP service
pip install 'opennomos-pm-gateway[server]'
```

OpenNomos is one possible consumer, not the boundary of the gateway's product
direction.

## Roadmap

The gateway is built in the following order of priority:

| # | Phase | Goal | Status |
| --- | --- | --- | --- |
| 1 | Standard | Provide a global standard terminology and API for project management tools (provider-neutral vocabulary + single OpenAPI contract). | In progress (v0.1 draft) |
| 2 | Provider support | Test and support different PM tools via adapters, validated live where possible; capabilities reported per provider. | In progress (Plane, Jira, Planner) |
| 3 | Compliance & routing | Enable unknown/other software to become compliant with the standard, with the gateway simply re-routing requests to them (conformance + passthrough). | In progress (compliant provider routing) |
| 4 | SDKs | Provide SDK support for different programming languages beyond Python. | Planned |

### 1. Global standard terminology and API

Establish and stabilize a provider-neutral vocabulary (workspaces, containers, work items,
workflow states, iterations, milestones, custom fields, comments, attachments, …) and a
single OpenAPI contract
([`pm-api-openapi-v0.1.yaml`](https://github.com/blackwiz4rd/opennomos-pm-gateway/blob/main/spec/pm-api-openapi-v0.1.yaml))
that every adapter implements. This is the foundation; adapter and SDK work depend on it.
Generated server code and Pydantic models derive from this spec.

### 2. Test & support different PM tools

Implement adapters for the providers in the field-mapping inventory and validate them
against live APIs where credentials allow. Report per-provider capabilities via
`get_capabilities` so callers know what is supported. Broad, tested coverage is the
immediate priority after the standard stabilizes.

### 3. Compliance for unknown software, gateway as router

Make it possible for any PM tool to become compliant with the standard on its own, so
the gateway can simply re-route requests to it instead of maintaining a bespoke adapter.
This means publishing a conformance specification and tests, and supporting a
passthrough/router mode for compliant providers.

#### Example: a small compliant PM tool ("TinyTasks")

To make the compliance/routing idea concrete, consider `TinyTasks`, a deliberately small
project-management app that implements the standard API directly. It is documentation
only here (no shipped code); it shows the shape a third-party tool must expose so the
gateway can treat it as a compliant provider and just re-route.

- **Data model (minimal):** one workspace → many containers (projects) → work items.
  Each work item carries the standard fields: `id`, `title`, `status`, `containerId`,
  `workspaceId`, plus optional `assignee`, `priority`, `createdAt`, `updatedAt`.
- **Implemented standard endpoints (subset):**
  - `GET  /pm/v1/workspaces`
  - `GET  /pm/v1/containers?workspaceId=...`
  - `GET  /pm/v1/work-items?workspaceId=...&containerId=...`
  - `GET  /pm/v1/work-items/{workItemId}`
  - `PATCH /pm/v1/work-items/{workItemId}`
- **Workflow states:** a fixed, small set exposed via `GET /pm/v1/workflow-states`,
  e.g. `todo`, `in_progress`, `done`; transitions go through
  `POST /pm/v1/work-items/{workItemId}/transitions`.
- **Capabilities:** `GET /pm/v1/capabilities` reports only what TinyTasks supports
  (e.g. `list_work_items`, `get_work_item`, `update_work_item`, `transition_work_item`;
  no comments/attachments/custom fields), so callers degrade gracefully.
- **Auth:** a single API token per workspace, passed by the gateway per request; TinyTasks
  is stateless about gateway credentials and never stores them.

Because TinyTasks speaks the standard contract, the gateway does not need a
TinyTasks-specific adapter: it validates the tool against the conformance suite, then
re-routes incoming requests to TinyTasks' base URL, translating only transport/auth
concerns. Any other small PM tool can follow the same pattern to become compliant.
### 4. SDK support for multiple languages

Ship first-party SDKs beyond Python (e.g. TypeScript/JavaScript, Go, …) so consumers
can use the standard API ergonomically in their own stack. The REST API remains callable
from any language via HTTP in the meantime.

## Current support and roadmap

### Languages

| Status | Support |
| --- | --- |
| Supported | Python 3.11+ for the in-process SDK and gateway service. |
| HTTP clients | The REST API can be called from any language with an HTTP client; this package does not provide non-Python SDKs. |
| Roadmap relationship | The [OpenNomos roadmap](https://github.com/blackwiz4rd/opennomos/blob/main/docs/product-requirements/roadmap.md#month-5--january-2027--architecture--microservices--acp-finishing) discusses Go for other OpenNomos services; it does not set this standalone tool's language direction. |

### PM providers

The table explicitly lists every product in the
[field-mapping inventory](https://github.com/blackwiz4rd/opennomos/blob/main/docs/reference/pm-tool-openapi-field-mapping.md),
plus Azure DevOps from the OpenNomos product roadmap. “Implemented” means an
adapter is present in this package; it does not imply full feature parity. The
provider test column means live/provider validation, not unit-test coverage.
The roadmap-priority column records OpenNomos's current priorities for one
consumer; it is not a commitment or prioritization for the standalone gateway.

| Provider / product | Adapter status | Live/provider-tested | OpenNomos roadmap priority | Est. PM-category adoption (Ramp, Oct 2026) | Free-account API access / credential | Native model / qualification |
| --- | --- | --- | --- | ---: | --- | --- |
| Jira | Implemented | Yes | Implemented | [47%](https://ramp.com/vendors/jira) | [Yes — API token on Free Cloud](https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/) | Issue |
| Confluence | Future candidate | — | Unsequenced | Not reported | [Yes — API token](https://support.atlassian.com/atlassian-account/docs/manage-api-tokens-for-your-atlassian-account/) on Free Cloud | Page/content by default; inline tasks can use a work-item profile |
| Trello | Future candidate | — | Unsequenced | [7%](https://ramp.com/vendors/trello) | [Yes — API key + user token](https://developer.atlassian.com/cloud/trello/guides/rest-api/api-introduction/) | Card |
| Plane | Implemented | Yes | Implemented | Not reported | [Yes — personal API key on Free](https://developers.plane.so/api-reference/introduction) | Work Item |
| Smartsheet | Future candidate | — | Unsequenced | [9%](https://ramp.com/vendors/smartsheet) | [No — trial cannot issue API tokens](https://help.smartsheet.com/articles/529590-smartsheet-free-trial-faqs); Business/Enterprise required | Row in an explicitly selected Sheet |
| Microsoft Planner | Implemented | **No** | Implemented | Not reported | [Conditional — OAuth, work/school tenant; personal accounts unsupported](https://learn.microsoft.com/en-us/graph/api/plannerplan-list-tasks?view=graph-rest-1.0) | `plannerTask`; unit normalization tests exist, but no live/provider validation |
| Asana | Planned | — | Could | [21%](https://ramp.com/vendors/asana) | [Yes — personal access token on Personal](https://developers.asana.com/docs/personal-access-token) | Task; preserve multi-project memberships |
| monday.com | Planned | — | Could | [19%](https://ramp.com/vendors/monday) | [Yes — API token in free developer sandbox](https://developer.monday.com/api-reference/docs/developer-sandbox-account) | Item with runtime board columns |
| Airtable | Future candidate | — | Unsequenced | [16%](https://ramp.com/vendors/airtable) | [Yes — personal access token on Free](https://support.airtable.com/articles/9934989703-creating-personal-access-tokens); Free API quota applies | Record only after selecting/configuring a table |
| Linear | Planned | — | Should | [15%](https://ramp.com/vendors/linear) | [Yes — personal API key on Free](https://linear.app/developers/graphql) | Issue |
| ClickUp | Planned | — | Could | [9%](https://ramp.com/vendors/clickup) | [Yes — personal API token on Free Forever](https://developer.clickup.com/docs/authentication) | Task |
| Notion | Future candidate | — | Unsequenced | [32%](https://ramp.com/vendors/notion) | [Yes — internal integration token on Free](https://developers.notion.com/guides/get-started/internal-integrations); share pages with it | Page/data-source item only by configuration |
| Microsoft Project | Conditional | — | Unsequenced | Not reported | Conditional — no generic Project API; choose a product/API family first | Must choose a specific Project product/API family |
| Wrike | Future candidate | — | Unsequenced | [1%](https://ramp.com/vendors/wrike) | [Yes — permanent token or OAuth; API is free on Free plan](https://developers.wrike.com/docs/faq) | Task / Work Item |
| Basecamp | Future candidate | — | Unsequenced | [4%](https://ramp.com/vendors/basecamp) | Free plan; [OAuth token, no API key](https://github.com/basecamp/bc-api/blob/master/sections/authentication.md); API docs don’t state a paid-plan requirement | To-do/Card; Message is a separate content profile |
| Zoho Projects | Planned | — | Could | Not reported | [Yes — OAuth with a free Zoho account](https://www.zoho.com/projects/help/rest-api/get-tickets-api.html) | Task; preserve API generation/layout differences |
| GitHub Issues | Planned | — | Should | Not reported | [Yes — personal access token on GitHub Free](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens) | Issue; pull-request semantics are an extension |
| GitLab Issues | Future candidate | — | Unsequenced | Not reported | [Yes — personal access token on GitLab Free](https://docs.gitlab.com/user/profile/personal_access_tokens/) | Issue / Work Item; model varies by GitLab version |
| Azure DevOps | Roadmap candidate | — | Could | Not reported | [Yes — PAT; Basic is free for the first five users](https://learn.microsoft.com/en-us/azure/devops/organizations/billing/buy-basic-access-add-users?view=azure-devops) | Product-roadmap target; not included in the 18-product field inventory |

The adoption estimates are Ramp Rate's share of businesses in its U.S. business
dataset that purchased from the vendor among businesses purchasing in the
Project Management category during the trailing 12 months. Ramp reports spend
data from more than 85,000 businesses; this is a directional adoption proxy,
**not global market share or revenue share**. Vendors can overlap, so percentages
do not sum to 100%. “Not reported” means no comparable Ramp Project Management
category estimate was found; do not substitute another category's figure or
infer a value from the ETR market tiers. Snapshot checked 2026-10-28 against the
linked [Ramp Rate Project Management category](https://ramp.com/vendors/categories/project-management)
and vendor pages.

Free-tier access describes whether a developer can obtain credentials and make
test API calls without buying a plan; it does not guarantee every endpoint or
feature is included. “API credential” includes personal access tokens and OAuth
tokens, not just static API keys. Microsoft Planner requires a work/school
tenant (Graph does not support personal Microsoft accounts); Smartsheet trial
accounts cannot create API tokens; and Microsoft Project has no single API to
test until its product family is selected. For GitHub/GitLab issues, use the
vendor's Issues API and a test repository/project; the listed vendors may
classify those products under DevOps rather than PM.

The OpenNomos Should/Could priorities come from the
[PM integrations roadmap](https://github.com/blackwiz4rd/opennomos/blob/main/docs/product-requirements/roadmap.md#month-3--november-2026--pm-tool-integrations-scaling-source)
and may change. They do not constrain the standalone gateway's future provider
support. The 18 mapped products come from the
[PM tools comparison](https://github.com/blackwiz4rd/opennomos/blob/main/docs/reference/pm-tools-comparison.md)
and
[field-mapping inventory](https://github.com/blackwiz4rd/opennomos/blob/main/docs/reference/pm-tool-openapi-field-mapping.md);
“Future candidate” means mapped for possible support but not sequenced as an
integration in that roadmap. Microsoft Project stays conditional until its API
family is selected. Provider support is operation-specific; use
`get_capabilities` to check the operations available for a particular provider.

### Future interoperability scope

The field-mapping inventory describes the intended direction for future provider
adapters and richer operations. It covers 18 product entries; Azure DevOps is an
additional product-roadmap candidate, not part of that field inventory. These
are mapping proposals—not claims that every product or feature is implemented.
Adapter work should prioritize:

- **Discoverable custom fields:** fetch definitions, stable native IDs, types,
  options, scope, and writability before mapping values. This is essential for
  systems whose work-item shape is configured per project, board, sheet, table,
  or data source.
- **Faithful work-item semantics:** preserve native workflow states separately
  from completion/progress; support multiple container memberships; and keep
  parent/subtask structure distinct from typed relationships such as blocks,
  duplicates, and dependencies.
- **Optional capability profiles:** lifecycle/archive state, followers,
  components and versions, cycles/iterations and milestones, time tracking,
  comments, and attachments. Expose these only where the provider supports them
  and report availability through capabilities.
- **Native-value preservation:** retain provider-scoped identities, native
  values and scales, and mapping quality. Do not infer priority from labels or
  workflow status from percentages/list placement; use namespaced extensions
  where no safe common field exists.

Some sources require explicit configuration before they can be treated as work
items: for example, Confluence pages are content by default, and Airtable or
Notion records need a selected table/data source and field mapping. Microsoft
Project also needs an explicitly named API family. Feature availability and
field definitions can vary by connection or container, so provider-wide
capability flags alone may be too coarse. See the
[field-mapping inventory](https://github.com/blackwiz4rd/opennomos/blob/main/docs/reference/pm-tool-openapi-field-mapping.md)
for the product-by-product crosswalk and mapping constraints.

## In-process SDK

Install the package from PyPI:

```sh
pip install opennomos-pm-gateway
```

For development inside the OpenNomos monorepo, use the editable install in
[Development](#development). The SDK is asynchronous and calls the provider
adapter in-process: you do not need to start the gateway service, configure a
gateway bearer token, or make an HTTP request to the gateway.

Create a `PMGateway` and pass the provider credentials on each `execute` call.
Supported provider keys are `plane`, `jira`, `planner`, and `compliant`. Jira and
compliant providers require their site URL as `base_url`; Plane and Planner use
their documented defaults unless an allowlisted URL is supplied. `base_url` is
security-sensitive because it decides where the provider credential is sent:
HTTPS is required, known SaaS endpoints (Jira Cloud, Plane Cloud, and Microsoft
Graph) are trusted by default, and every other host must be explicitly
allowlisted with `PM_GATEWAY_ALLOWED_PROVIDER_HOSTS`. Planner is restricted to
Microsoft Graph endpoints. Never pair a stored credential with an untrusted base
URL.
Credentials use the provider's expected format: Plane API token, Jira
`email:api-token`, Planner `tenant_id:client_id:client_secret`, or, for a
compliant provider, the bearer token accepted by the remote service. Keep
credentials in a secret store or environment variables, not in source control.

```python
import asyncio
import os

from pm_gateway import Operation, PMGateway, Provider

async def main() -> None:
    gateway = PMGateway()
    provider = Provider.JIRA
    credential = os.environ["JIRA_CREDENTIAL"]  # email:api-token
    base_url = os.environ["JIRA_BASE_URL"]      # e.g. https://example.atlassian.net

    # Check what this provider supports before relying on optional operations.
    capabilities = await gateway.execute(
        Operation.GET_CAPABILITIES,
        provider=provider,
        credential=credential,
        base_url=base_url,
    )
    print(capabilities)

    # IDs are provider-native. Find the workspace and container (project, plan,
    # board, etc.) to use from your provider or the discovery operations.
    workspace_id = "your-workspace-id"
    containers = await gateway.execute(
        Operation.LIST_CONTAINERS,
        provider=provider,
        credential=credential,
        base_url=base_url,
        workspaceId=workspace_id,
        limit=50,
    )
    for container in containers.items:
        print(container.id, container.name)

asyncio.run(main())
```

`execute(operation, *, provider, credential, base_url=None, **params)` takes an
`Operation` enum member and that operation's parameters. The `provider`
argument takes a `Provider` enum member: `Provider.PLANE`, `Provider.JIRA`,
`Provider.PLANNER`, or `Provider.COMPLIANT`. Use operation members such as
`Operation.GET_CAPABILITIES`, `Operation.LIST_CONTAINERS`, or
`Operation.LIST_WORK_ITEMS`; the enum is exported from `pm_gateway`. Parameter
names follow the generated Python API signatures (for example, `workspaceId`,
`containerId`, `workItemId`, and `changed_since`). Results are generated
Pydantic model instances, not dictionaries: page results expose `items` and
`next_cursor`, and individual resources expose their model fields.
`list_workspaces` returns configured workspace slugs; pass them as
`x_pm_workspace_slugs="team-a,team-b"`.
To fetch work items and handle pagination, add this inside `main()` above,
using the same `gateway`, `provider`, `credential`, `base_url`, and
`workspace_id` values:

```python
    page = await gateway.execute(
        Operation.LIST_WORK_ITEMS,
        provider=provider,
        credential=credential,
        base_url=base_url,
        workspaceId=workspace_id,
        containerId="your-container-id",
        limit=50,
    )
    for item in page.items:
        print(item.id, item.title, item.status)

    # Pass the opaque cursor back with the same query to fetch the next page.
    if page.next_cursor:
        next_page = await gateway.execute(
            Operation.LIST_WORK_ITEMS,
            provider=provider,
            credential=credential,
            base_url=base_url,
            workspaceId=workspace_id,
            containerId="your-container-id",
            limit=50,
            cursor=page.next_cursor,
        )
```

Available operation names are `get_capabilities`, `list_workspaces`,
`list_containers`, `list_boards`, `get_board_scope`,
`list_custom_field_definitions`, `list_iterations`, `list_milestones`,
`list_work_items`, `get_work_item`, `update_work_item`,
`list_work_item_comments`, `list_work_item_attachments`,
`list_workflow_states`, and `transition_work_item`. Optional operations may
not be supported by every provider; consult `get_capabilities` and expect an
unsupported-operation error when a feature is unavailable. Page sizes are at
most 200. Provider/API errors are raised as `fastapi.HTTPException` with a
status code and detail; handle these at your application boundary.

Credentials are passed for each call and are not persisted by the SDK. Do not
log credentials or include them in exception output. In the OpenNomos monorepo,
the backend is one optional SDK consumer: it resolves and decrypts stored
credentials, then supplies them per operation. The gateway itself does not
depend on that backend.

### Compliant providers (passthrough)

A `compliant` provider is third-party software that implements the standard
OpenAPI contract directly. The gateway does not map that software's native model
through an adapter. Instead, it forwards the standard operation to the remote
service and returns the remote standard payload unchanged (parsed into the
generated models). This lets an unknown PM tool become usable without a
bespoke adapter. The example host below is not a built-in default; allowlist it
with `PM_GATEWAY_ALLOWED_PROVIDER_HOSTS=tools.example.com` before calling.

```python
gateway = PMGateway()
page = await gateway.execute(
    Operation.LIST_WORK_ITEMS,
    provider=Provider.COMPLIANT,
    credential=os.environ["COMPLIANT_TOKEN"],  # sent as Authorization: Bearer
    base_url="https://tools.example.com",      # /pm/v1 appended if absent
    workspaceId="workspace-1",
    containerId="project-1",
)
```

Rules for compliant providers:

- `base_url` is required and must be an absolute HTTPS URL without embedded
  credentials, a query, or a fragment. Plain HTTP is rejected unless
  `PM_GATEWAY_ALLOW_INSECURE_HTTP=1` is set for local development. If the path
  does not already end with `/pm/v1` (case-insensitive), the gateway appends it.
  The remote service must therefore expose the standard operations under that
  base path. The provider credential is sent to whatever host is configured, so
  treat the host as part of the credential boundary. There is no default
  compliant host: add it to `PM_GATEWAY_ALLOWED_PROVIDER_HOSTS` before use.
- The provider credential is forwarded only as `Authorization: Bearer <token>`.
  Gateway-side headers such as `X-PM-Provider`, `X-PM-Provider-Credential`, and
  `X-PM-Provider-Base-Url` are not sent upstream. When `list_workspaces` is
  called with `x_pm_workspace_slugs`, that value is forwarded as
  `X-PM-Workspace-Slugs`.
- All standard operations are forwarded, including query filters such as
  `changedSince`, `stateId`, `assigneeId`, `boardId`, and `iterationIds`.
  Path IDs (`workspaceId`, `containerId`, `workItemId`) are percent-encoded
  before being placed in the upstream URL, so a client-supplied value cannot
  escape the standard route template on the remote host.
- `update_work_item` forwards standard merge-patch semantics: fields the caller
  omitted are left out of the upstream body (unchanged remotely) while explicit
  `null` values clear the field. This holds for plain-dict payloads and for
  generated `WorkItemPatch` models alike.
- Work-item reads and writes propagate the upstream `ETag` on responses, and
  `If-Match` is forwarded on `update_work_item` and `transition_work_item`, so
  conditional updates work end-to-end through the gateway.
- Upstream HTTP errors are surfaced as `fastapi.HTTPException` with the remote
  status code. The remote body is replaced with a generic message by default;
  set `PM_GATEWAY_EXPOSE_UPSTREAM_ERRORS=1` to include a bounded remote
  `message`/`detail` for debugging. A remote `Retry-After` header (for example
  on `429`) is always forwarded to the caller. A remote implementation that does
  not support an optional operation should return the appropriate standard error
  itself.
- The remote implementation is assumed to be a standalone standard API, not
  another pm-gateway instance behind the same auth middleware.

The same behavior is available over HTTP: send `X-PM-Provider: compliant`,
`X-PM-Provider-Credential`, and `X-PM-Provider-Base-Url` with each request.

## Generated server code

The OpenAPI document at
[`spec/pm-api-openapi-v0.1.yaml`](https://github.com/blackwiz4rd/opennomos-pm-gateway/blob/main/spec/pm-api-openapi-v0.1.yaml)
is the contract source. Generated API routes and models are grouped under
`src/pm_gateway/generated/` (APIs, models, and `security_api.py`). **Do not edit
those generated files by hand.** Implementations belong under
`src/pm_gateway/impl/` and other handwritten modules. Regenerate with:

```sh
./scripts/generate-server.sh
# Verify generated files without changing the package:
./scripts/generate-server.sh --check
```

Run these commands from the package root. In the OpenNomos monorepo, prefix them
with `pm-gateway/`.

The script requires OpenAPI Generator CLI `7.24.0` and Java. Override the
specification or generated output directory with `--spec` and `--package-dir`,
or pin a different installed CLI version with `OPENAPI_GENERATOR_VERSION`.

The Python FastAPI server generator is currently marked beta by OpenAPI
Generator, and OpenAPI 3.1 support is marked beta. Review generated diffs and
run the service tests whenever the generator or specification changes. The
`--check` mode compares only generated API/model/security files and never writes
to the package. A test also fails if a generated API operation is missing from
the gateway dispatch registry.

## Run

```sh
pip install 'opennomos-pm-gateway[server]'
PM_GATEWAY_API_TOKEN='replace-with-a-long-random-secret' pm-gateway
```

`PM_GATEWAY_HOST` (default `127.0.0.1`) and `PM_GATEWAY_PORT` (default `8080`)
configure the listener.

The service mounts the draft OpenAPI API at `/pm/v1`. `/healthz` is an
unauthenticated liveness endpoint; API operations require the gateway bearer token.

Plane, Jira, and Microsoft Planner adapters are implemented in
`src/pm_gateway/adapters/`. They support workspace/container discovery, work-item
reads, workflow-state reads and transitions; Jira and Planner also expose their
current board/bucket behavior. Capabilities mark custom fields, milestones,
comments, attachments, and general field updates unsupported until implemented.
The `compliant` provider is a passthrough to third-party software that implements
the standard API directly.

The bearer token protects the optional HTTP service and is distinct from the PM
vendor credential. HTTP consumers send provider identity, provider credential,
and optional provider base URL per request. The service uses request-scoped
clients and does not persist credentials. The OpenNomos backend does not use or
start this HTTP service. To run it as a container:

```sh
docker build -t opennomos-pm-gateway .
docker run --rm -p 127.0.0.1:8080:8080 \
  -e PM_GATEWAY_API_TOKEN='replace-with-a-long-random-secret' \
  opennomos-pm-gateway
```

Bind published ports to localhost or a private interface, and terminate TLS in
front of the service.

## Development

From the repository root:

```sh
python -m pip install -e '.[server,dev]'
python -m pytest
python -m build
python -m twine check dist/*
```

When working from the OpenNomos monorepo, run these from `pm-gateway/`.

Release steps are documented in
[RELEASING.md](https://github.com/blackwiz4rd/opennomos-pm-gateway/blob/main/RELEASING.md).

## Security

Provider credentials and the gateway token are bearer secrets. Treat the service
as a trusted backend component, not as an authorization boundary.

- Terminate TLS in front of the service. Do not expose the gateway token or
  `X-PM-Provider-Credential` over plaintext.
- Use a long random `PM_GATEWAY_API_TOKEN`. The service fails closed when it is
  unset. Apply rate limiting at the reverse proxy.
- `base_url` is part of the credential boundary. HTTPS is required; only known
  SaaS endpoints are trusted by default, and every other host must be
  explicitly allowlisted. Planner is limited to Graph endpoints. Never let an
  untrusted user choose the base URL for a stored credential. The built-in
  `*.atlassian.net` wildcard trusts any Atlassian Cloud site; for stored
  credentials paired with user-controlled base URLs, prefer an exact-host
  allowlist.
- Any caller with the gateway token can use any provider credential it supplies.
  The OpenNomos backend must scope credentials to the user or integration and
  validate integration base URLs.
- Provider credentials are request-scoped and not persisted or logged. Configure
  the reverse proxy not to log `Authorization` or `X-PM-Provider-Credential`.
- Upstream error bodies are hidden by default. Set
  `PM_GATEWAY_EXPOSE_UPSTREAM_ERRORS=1` only for debugging.
- Descriptions may be `text/plain` or `text/html`. Sanitize HTML before rendering
  it in a browser.
- Apply request-size and rate limits at the proxy. Provider requests time out
  after `PM_GATEWAY_PROVIDER_TIMEOUT_SECONDS` (default 30).
- Run the container with a read-only filesystem, no added capabilities, and
  `no-new-privileges`; the image runs as uid 10001.
- Report vulnerabilities using the process in `SECURITY.md`.

### Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `PM_GATEWAY_API_TOKEN` | required | Bearer token for the HTTP service. |
| `PM_GATEWAY_HOST` | `127.0.0.1` | HTTP listen address. |
| `PM_GATEWAY_PORT` | `8080` | HTTP listen port. |
| `PM_GATEWAY_ALLOWED_PROVIDER_HOSTS` | empty | Comma-separated host allowlist; supports `*.example.com`. Required for any host not covered by the built-in Jira Cloud, Plane Cloud, and Microsoft Graph defaults. |
| `PM_GATEWAY_ALLOW_INSECURE_HTTP` | off | Allow `http://` provider URLs. Use only for local development. |
| `PM_GATEWAY_PROVIDER_TIMEOUT_SECONDS` | `30` | Timeout for provider HTTP requests. |
| `PM_GATEWAY_MAX_BODY_BYTES` | `1048576` | Maximum request body accepted by the HTTP service. |
| `PM_GATEWAY_EXPOSE_UPSTREAM_ERRORS` | off | Include bounded upstream error text in responses. |

## License

Apache License 2.0. See
[LICENSE](https://github.com/blackwiz4rd/opennomos-pm-gateway/blob/main/LICENSE).
