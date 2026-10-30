# PM Gateway Compliance Implementation Guide

**Audience:** AI coding agents and developers implementing the standard PM API
so that a tool can be used through the PM Gateway's `compliant` provider mode.

**Status:** draft, aligned with `spec/pm-api-openapi-v0.1.yaml`
(`0.1.0-draft`). The contract is an alpha technical preview and may change.

**How to use this document:** read it end to end before writing code. Then work
through the ordered task list in [§12](#12-implementation-task-list-for-ai-agents).
The normative source of truth is the OpenAPI document. If this guide and the
specification disagree, the specification wins, with the gateway compatibility
notes in [§4.3](#43-headers-and-the-gateway-boundary) as the documented
exceptions.

> **Do not** modify `spec/pm-api-openapi-v0.1.yaml`, the gateway's generated
> code, or the gateway's conformance checker to make an implementation pass.
> Fix the implementation.

---

## 1. What "compliant" means

The gateway has two ways to support a PM tool:

| Mode | Who translates | When to use |
|---|---|---|
| Native adapter (`plane`, `jira`, `planner`) | The gateway maps the vendor API into the standard model. | The tool has its own API and you want the gateway to adapt it. |
| `compliant` provider | The tool itself implements the standard API. The gateway only routes requests, injects the bearer token, and parses responses into its models. | You control the tool and can expose `/pm/v1` directly. |

In `compliant` mode the gateway:

- does **not** map fields, rename concepts, or normalize values;
- **does** rebuild the upstream path from the standard route, percent-encode
  path IDs, forward query filters and the request body, forward `If-Match`,
  parse the response into generated models, and propagate `ETag`/`Retry-After`;
- requires the tool to be reachable over HTTPS (unless explicitly allowlisted),
  and requires the host to be on `PM_GATEWAY_ALLOWED_PROVIDER_HOSTS`.

Compliance is therefore **wire-level compatibility with the standard**, not
just “has endpoints with similar names”.

### 1.1 Profiles

| Profile | Operations | Required |
|---|---|---|
| **Core Read** | `get_capabilities`, `list_workspaces`, `list_containers`, `list_work_items`, `get_work_item` | Yes |
| Workflow | `list_workflow_states`, `transition_work_item` | Optional |
| Discovery extras | `list_boards`, `get_board_scope`, `list_custom_field_definitions`, `list_iterations`, `list_milestones` | Optional |
| Work item extras | `update_work_item`, `list_work_item_comments`, `list_work_item_attachments` | Optional |

A Core Read implementation is already usable through the gateway. Optional
operations must be advertised truthfully and must return `501 not_supported`
when unavailable, rather than pretending to succeed.

---

## 2. Source-of-truth map

| Artifact | Location | Role |
|---|---|---|
| OpenAPI contract | `spec/pm-api-openapi-v0.1.yaml` | Normative paths, parameters, schemas, status codes |
| Gateway consumer behavior | `src/pm_gateway/adapters/compliant.py` and `src/pm_gateway/impl/provider_api.py` | How the gateway actually calls a compliant provider |
| Conformance checker | `src/pm_gateway/conformance.py` | Automated wire-contract verification |
| Semantic draft | `docs/reference/pm-api-interoperability-standard.md` (OpenNomos repo) | Terminology, conformance language, design rationale |
| README | `README.md` | Setup, roadmap, security rules |

The generated gateway models under `src/pm_gateway/generated/models/` are a
convenient reference for exact field names and nullability. Do not import them
from a standalone service as a runtime dependency unless you deliberately want
to depend on the gateway package.

---

## 3. Contract at a glance

All operations are mounted under **`/pm/v1`** and require a bearer token.

| # | Method | Path | Operation ID | Profile |
|---|---|---|---|---|
| 1 | `GET` | `/capabilities` | `getCapabilities` | Core Read |
| 2 | `GET` | `/workspaces` | `listWorkspaces` | Core Read |
| 3 | `GET` | `/workspaces/{workspaceId}/containers` | `listContainers` | Core Read |
| 4 | `GET` | `/workspaces/{workspaceId}/containers/{containerId}/boards` | `listBoards` | Optional |
| 5 | `GET` | `/workspaces/{workspaceId}/containers/{containerId}/work-items` | `listWorkItems` | Core Read |
| 6 | `GET` | `/workspaces/{workspaceId}/containers/{containerId}/board-scope` | `getBoardScope` | Optional |
| 7 | `GET` | `/workspaces/{workspaceId}/work-items/{workItemId}` | `getWorkItem` | Core Read |
| 8 | `PATCH` | `/workspaces/{workspaceId}/work-items/{workItemId}` | `updateWorkItem` | Optional |
| 9 | `GET` | `/workspaces/{workspaceId}/containers/{containerId}/workflow-states` | `listWorkflowStates` | Optional |
| 10 | `POST` | `/workspaces/{workspaceId}/work-items/{workItemId}/transitions` | `transitionWorkItem` | Optional |
| 11 | `GET` | `/workspaces/{workspaceId}/containers/{containerId}/custom-fields` | `listCustomFieldDefinitions` | Optional |
| 12 | `GET` | `/workspaces/{workspaceId}/containers/{containerId}/iterations` | `listIterations` | Optional |
| 13 | `GET` | `/workspaces/{workspaceId}/containers/{containerId}/milestones` | `listMilestones` | Optional |
| 14 | `GET` | `/workspaces/{workspaceId}/work-items/{workItemId}/comments` | `listWorkItemComments` | Optional |
| 15 | `GET` | `/workspaces/{workspaceId}/work-items/{workItemId}/attachments` | `listWorkItemAttachments` | Optional |

### 3.1 Universal rules

- Use `application/json` for every response and request body.
- IDs are opaque strings. Never parse an ID, and never use a display name as an
  ID. Path IDs arrive percent-encoded; decode them before use.
- Pages are `{ "items": [...], "nextCursor": "..." }`. `nextCursor` is a
  string or `null`; omit it or set it to `null` on the last page.
- `limit` is an integer from 1 to 200, default 50. Clamp out-of-range values or
  reject them with `400 invalid_request`; do not silently return 10 000 rows.
- `cursor` is opaque to clients and bound to the query context. Reject an
  invalid or expired cursor with `400 invalid_request`.
- Dates use RFC 3339 (`2026-01-31T12:00:00Z`); date-only fields use
  `YYYY-MM-DD`.
- Errors are JSON `ApiError` objects, never HTML, never a bare string.
- Every response must be tenant-scoped from the authenticated principal, not
  from a client-supplied workspace ID alone.

---

## 4. Authentication and tenancy

### 4.1 Bearer token

The OpenAPI document declares `bearerAuth` globally. Every operation requires:

```http
Authorization: Bearer <provider-token>
```

The gateway forwards the provider credential it received for that request as
this header. The token is request-scoped: do not persist it, do not put it in
logs, and do not echo it in errors.

Implementation requirements:

1. Resolve the token to a principal and tenant before route logic runs.
2. Missing or malformed header → `401 unauthenticated`.
3. Valid token but insufficient access → `403 forbidden`.
4. A resource in another tenant → `404 not_found` (do not reveal existence).
5. Compare tokens in constant time if you validate them directly.
6. Terminate TLS in front of the service. Never accept bearer tokens over
   plaintext HTTP outside local development.

Example error:

```json
{
  "code": "unauthenticated",
  "message": "A valid bearer token is required.",
  "requestId": "req-7f3c",
  "retryable": false
}
```

### 4.2 `X-PM-Workspace-Slugs`

On `list_workspaces` only, the gateway may forward:

```http
X-PM-Workspace-Slugs: team-a,team-b
```

This is a **filter hint from the credential configuration**, not an
authorization mechanism. Intersect it with the workspaces the token can already
access. If the header is absent, return all accessible workspaces. Never treat
the header as proof of access.

### 4.3 Headers and the gateway boundary

The OpenAPI document lists `X-PM-Provider`, `X-PM-Provider-Credential`, and
`X-PM-Provider-Base-Url` as request parameters because the same document also
describes the gateway's own API. **The compliant passthrough does not forward
these headers upstream.** It sends only `Authorization` (plus
`X-PM-Workspace-Slugs` on `list_workspaces`).

Therefore a compliant implementation:

- **MUST** authenticate with `Authorization: Bearer`.
- **MUST NOT** require `X-PM-Provider`, `X-PM-Provider-Credential`, or
  `X-PM-Provider-Base-Url`.
- **MAY** accept and ignore those headers for direct clients.
- **MUST NOT** use them to select a tenant or a database.

This is a deliberate compatibility rule, not an invitation to weaken auth.

### 4.4 Multi-tenancy checklist

- Derive the tenant from the token principal.
- Scope every workspace, container, work-item, comment, and attachment lookup
  by tenant.
- Do not allow a caller to enumerate another tenant by guessing IDs.
- Do not include secrets, internal hostnames, SQL, or stack traces in errors.
- Apply per-token rate limits and return `429` with `Retry-After`.

---

## 5. Errors, status codes, and retries

### 5.1 `ApiError` shape

```json
{
  "code": "validation_error",
  "message": "The due date is not a valid date.",
  "requestId": "req-123",
  "retryable": false,
  "retryAfterSeconds": null,
  "invalidParams": [
    { "name": "dates.dueDate", "reason": "Expected YYYY-MM-DD." }
  ],
  "providerCode": "ERR_INVALID_DATE"
}
```

Required: `code`, `message`. All other fields are optional.

### 5.2 Status mapping

| HTTP | `code` | Use |
|---:|---|---|
| 400 | `invalid_request` | Malformed query, unsupported filter, invalid cursor |
| 401 | `unauthenticated` | Missing/invalid bearer token |
| 403 | `forbidden` | Authenticated but not allowed |
| 404 | `not_found` | Missing or cross-tenant resource |
| 409 | `conflict` | Transition conflicts with current state |
| 412 | `precondition_failed` | `If-Match` does not match |
| 422 | `validation_error` | Field values or transition target rejected |
| 429 | `rate_limited` | Throttled; include `Retry-After` |
| 500 | `internal_error` / `upstream_error` | Unexpected failure |
| 501 | `not_supported` | Optional operation not implemented |

### 5.3 Rules

- Unsupported optional **operation** → `501 not_supported`.
- Unsupported **filter** → `400 invalid_request` (or declare it unsupported in
  capabilities), never silently ignore it.
- Validation failures should list offending fields in `invalidParams`.
- `429` must include `Retry-After` in seconds.
- `5xx` messages must be generic; log correlation details server-side using
  `requestId`.
- Do not return `200` with a fake empty result to hide an unimplemented
  operation. The one documented exception is `list_boards`, where providers
  without a board concept return an empty page **and** advertise
  `boards.list: false`.

---

## 6. Pagination, filtering, and concurrency

### 6.1 Pagination

```
GET /workspaces/{workspaceId}/containers/{containerId}/work-items?limit=50
→ { "items": [...], "nextCursor": "opaque-token" }

GET ...?limit=50&cursor=opaque-token
→ { "items": [...], "nextCursor": null }
```

Requirements:

- Order must be stable for the lifetime of the cursor. Prefer
  `(updated_at, id)` or an immutable sequence.
- `nextCursor` must never point back to the same page.
- Cursors must be tamper-resistant or validated; invalid cursors → `400`.
- Do not return more than `limit` items (a smaller page is allowed).
- Do not invent a `next_cursor` snake_case field; the wire name is
  `nextCursor`.

### 6.2 Filters

Supported filters for work items: `changedSince`, `stateId`, `assigneeId`,
`archived`, `deleted`, `query`, `sort`, `boardId`, `iterationIds`.

- Apply only filters you can honor.
- Unsupported filter → `400 invalid_request` with a clear message.
- `changedSince` means “updated at or after this timestamp” and must use UTC.
- `archived`/`deleted` omission means “no filter”; `false` means “only
  non-archived/non-deleted”, not “unknown”.
- If the provider cannot list tombstones, report `work-items.deleted-filter` as
  unsupported; do not let `deleted=false` imply a complete sync.

### 6.3 Merge patch

`PATCH /workspaces/{workspaceId}/work-items/{workItemId}` is a JSON merge
patch:

- Omitted property → unchanged.
- `null` → clear the field, but only if the field is nullable and writable.
- Empty object `{}` → `400 invalid_request` (`minProperties: 1`).
- Unknown field or non-writable field → `422 validation_error` with
  `invalidParams`.
- Title cannot be set to `null`.

```json
PATCH /pm/v1/workspaces/ws-1/work-items/wi-1?containerId=c-1
Content-Type: application/json

{ "title": "Fix login on mobile" }
```

> **Gateway compatibility note:** the draft spec advertises
> `application/merge-patch+json`, but the gateway currently sends
> `application/json` because it uses `httpx`'s `json=` parameter. Accept both
> content types and apply merge-patch semantics to either. Rejecting
> `application/json` will break the gateway.

### 6.4 ETag and `If-Match`

ETags are optional, but if you emit one you must enforce it:

1. `GET` work item returns `ETag: "rev-42"`.
2. `PATCH`/`POST transition` may include `If-Match: "rev-42"`.
3. If it matches, apply the change and return the new `ETag`.
4. If it does not match, return `412 precondition_failed` and change nothing.
5. If the header is absent, the update is unconditional (unless your security
   model requires otherwise; then document it).

The gateway forwards the header and propagates the response `ETag` unchanged.

---

## 7. OpenAPI-driven scaffolding

### 7.1 Generate a server skeleton

The contract is an OpenAPI 3.1 YAML document. If your project starts from a
different interface description (Smithy, an `.api` descriptor, RAML, GraphQL
schema, or a hand-written spec), export or convert it to OpenAPI 3.1 first, then
generate the server. Do not generate from a lossy re-description of the
gateway's model; use `spec/pm-api-openapi-v0.1.yaml` as the source.

Use the same generator version as the gateway (`7.24.0`) to avoid model drift.
Install Java and the OpenAPI Generator CLI, then:

```bash
openapi-generator-cli generate \
  -i spec/pm-api-openapi-v0.1.yaml \
  -g python-fastapi \
  -o build/compliant-server \
  --additional-properties=packageName=compliant_api,fastapiImplementationPackage=impl,serverPort=8080,disallowAdditionalPropertiesIfNotPresent=false \
  --global-property=apiTests=false,modelTests=false
```

Other server generators:

| Stack | Generator |
|---|---|
| Python / FastAPI | `python-fastapi` |
| Node.js / Express | `nodejs-express-server` |
| Go | `go-server` |
| Java / Spring | `spring` |
| TypeScript clients | `typescript-fetch` |
| Python clients | `python` |

If you use the npm wrapper, pin the generator version it downloads to `7.24.0`
as well; the wrapper's own version is not the generator version.

### 7.2 What to keep and what to replace

- **Keep** the generated model classes and route signatures; they encode the
  field names and enums.
- **Replace** every generated stub body with real logic.
- **Remove or disable** generated mock controllers, sample data, and permissive
  development auth.
- **Mount** the routes at `/pm/v1` (or configure the server base path to match).
- **Pin** the generator version in your repository. Regenerate deliberately,
  never as part of a production build.
- **Do not** edit generated files by hand; wrap or extend them.

### 7.3 If you hand-write the server

At minimum, reproduce:

- camelCase wire names (`nextCursor`, `changedSince`, `workItemId`);
- required fields and nullability from the schemas;
- the enums for `ApiError.code`, `MappingMetadata.quality`,
  `WorkflowStateRef.category`, `CustomFieldDefinition.valueType`/`scope`,
  `BoardScope.scope`, and relationship types;
- `additionalProperties: false` on core objects (use `extensions` instead).

### 7.4 Cross-check the contract

Before implementing, run:

```bash
openapi-generator-cli validate -i spec/pm-api-openapi-v0.1.yaml
```

The gateway's own generation check is:

```bash
./scripts/generate-server.sh --check   # from pm-gateway/
```

That check is for the gateway package, not your service, but it confirms the
specification itself is internally consistent.

---

## 8. Endpoint implementation guide

For each endpoint below: implement the success shape exactly, return the listed
errors, and keep tenant scoping in mind. Examples are illustrative; the schema
is authoritative.

### 8.1 `GET /capabilities`

**Purpose:** tell the caller what this deployment supports, before it relies on
optional operations.

**Response 200:**

```json
{
  "apiVersion": "1.0",
  "coreProfile": "core-read-0.1",
  "provider": {
    "key": "tinytasks",
    "productName": "TinyTasks",
    "apiVersion": "2026-01"
  },
  "features": [
    { "name": "workspaces.list", "supported": true, "scope": "workspace" },
    { "name": "containers.list", "supported": true, "scope": "workspace" },
    { "name": "work-items.read", "supported": true, "scope": "container" },
    { "name": "work-items.update", "supported": true, "scope": "work-item" },
    { "name": "workflow-states.list", "supported": true, "scope": "container" },
    { "name": "work-items.transition", "supported": false, "scope": "work-item" },
    { "name": "comments.read", "supported": false, "scope": "work-item" }
  ],
  "limits": { "pageSizeMax": 200 }
}
```

**Rules:**

- Required: `apiVersion`, `coreProfile`, `features`.
- Every feature requires `name` (string) and `supported` (boolean).
- Use the conventional feature names the gateway understands:

  `workspaces.list`, `containers.list`, `boards.list`, `board-scope.read`,
  `work-items.read`, `work-items.update`, `work-items.transition`,
  `work-items.archived-filter`, `work-items.deleted-filter`,
  `workflow-states.list`, `custom-fields.discovery`, `iterations.list`,
  `milestones.list`, `comments.read`, `attachments.read`.
- Report `supported: false` for anything you cannot honor. The caller will
  degrade gracefully.
- `coreProfile` should be `core-read-0.1` for this draft.
- Do not advertise a feature merely because a route exists.

### 8.2 `GET /workspaces`

**Purpose:** list tenants/workspaces visible to the token.

**Query:** `cursor`, `limit`; optional `X-PM-Workspace-Slugs` filter.

**Response 200:**

```json
{
  "items": [
    {
      "id": "ws-1",
      "name": "Acme",
      "url": "https://tinytasks.example/ws-1",
      "provider": { "key": "tinytasks", "productName": "TinyTasks" }
    }
  ],
  "nextCursor": null
}
```

**Rules:**

- Required per item: `id`, `name`, `provider.key`.
- `id` is opaque and stable.
- Return only workspaces the token can access.
- An empty page is valid if the token has no workspaces.
- Do not invent a workspace per request; discovery must reflect real scope.

**Errors:** `401`, `403`, `429`, `500`.

### 8.3 `GET /workspaces/{workspaceId}/containers`

**Purpose:** list projects, boards, plans, lists, repositories, or sheets.

**Query:** `cursor`, `limit`.

**Response 200:**

```json
{
  "items": [
    {
      "id": "c-1",
      "kind": "project",
      "name": "Mobile app",
      "url": "https://tinytasks.example/ws-1/c-1",
      "parent": null,
      "memberships": []
    }
  ],
  "nextCursor": null
}
```

**Rules:**

- Required per item: `id`, `kind`, `name`.
- `kind` is provider-native (`project`, `board`, `plan`, `list`, `sheet`, …).
  Do not pretend all kinds have identical semantics.
- Verify the workspace is in the token's tenant; otherwise `404`.

**Errors:** `401`, `403`, `404`, `429`, `500`.

### 8.4 `GET .../containers/{containerId}/boards` (optional)

**Purpose:** expose board-like subdivisions of a container.

**Response 200:** a `ContainerPage` where each board is a `Container`.

**Rules:**

- If the provider has no board concept, return an empty page **and** advertise
  `boards.list: false`. This is the documented exception to the “501 for
  unsupported” rule.
- Board IDs must be usable as `boardId` in work-item filters, workflow-state
  lookup, and board scope.

**Errors:** `401`, `403`, `404`, `500`.

### 8.5 `GET .../containers/{containerId}/work-items`

**Purpose:** the primary read path. This is the operation that must be fast,
stable, and correct.

**Query:** `cursor`, `limit`, `changedSince`, `stateId`, `assigneeId`,
`archived`, `deleted`, `query`, `sort`, `boardId`, `iterationIds`.

**Response 200:**

```json
{
  "items": [
    {
      "id": "wi-1",
      "identifier": "TT-1",
      "title": "Fix login",
      "description": "Users cannot log in on mobile.",
      "descriptionContentType": "text/plain",
      "url": "https://tinytasks.example/ws-1/c-1/wi-1",
      "status": {
        "id": "state-2",
        "name": "In Progress",
        "category": "started",
        "mapping": { "quality": "mapped" }
      },
      "priority": {
        "level": "high",
        "nativeValue": "P1",
        "mapping": { "quality": "mapped" }
      },
      "assignees": [
        { "id": "u-1", "displayName": "Ada Lovelace" }
      ],
      "dates": {
        "createdAt": "2026-01-10T09:00:00Z",
        "updatedAt": "2026-01-12T11:30:00Z",
        "dueDate": "2026-02-01"
      },
      "labels": [{ "id": "l-1", "name": "mobile" }],
      "source": {
        "provider": "tinytasks",
        "resourceType": "work-item",
        "nativeUrl": "https://tinytasks.example/ws-1/c-1/wi-1",
        "revision": "42"
      },
      "extensions": {
        "com.tinytasks": { "storyPoints": 3 }
      }
    }
  ],
  "nextCursor": "eyJvZmZzZXQiOjUwfQ"
}
```

**Rules:**

- Required per item: `id`, `title`, `source.provider`,
  `source.resourceType`.
- `status.mapping.quality` is required when `status` is present.
- `status.category` is one of `backlog`, `unstarted`, `started`, `completed`,
  `cancelled`, or `null`; keep the native state name in `status.name`.
- Use `extensions` for provider-specific values, not ad-hoc top-level fields.
  Core work items have `additionalProperties: false`.
- `sort` supports a leading `-` for descending. Declare supported sort fields in
  capabilities if they are not obvious.
- Do not silently ignore an unsupported filter.
- Apply `containerId` scoping; never return items from another container.

**Errors:** `400`, `401`, `403`, `404`, `429`, `500`.

### 8.6 `GET .../containers/{containerId}/board-scope` (optional)

**Purpose:** describe the effective board/backlog/sprint scope.

**Query:** `boardId`, `iterationIds` (repeatable).

**Response 200:**

```json
{
  "boardId": "b-1",
  "scope": "sprint",
  "sprint": {
    "id": "s-1",
    "name": "Sprint 12",
    "kind": "sprint",
    "startsAt": "2026-01-05T00:00:00Z",
    "endsAt": "2026-01-19T00:00:00Z",
    "state": "active"
  },
  "sprints": [
    {
      "id": "s-1",
      "name": "Sprint 12",
      "kind": "sprint",
      "startsAt": "2026-01-05T00:00:00Z",
      "endsAt": "2026-01-19T00:00:00Z",
      "state": "active"
    }
  ]
}
```

**Rules:**

- Required: `boardId` (string or null), `scope` (`all`/`backlog`/`sprint`),
  `sprints` (array).
- Preserve native iteration kinds (`sprint`, `cycle`, `iteration`, `timebox`).

**Errors:** `401`, `403`, `404`, `500`.

### 8.7 `GET /workspaces/{workspaceId}/work-items/{workItemId}`

**Purpose:** fetch one work item, optionally with container context.

**Query:** `containerId` (optional context required by some providers).

**Response 200:** a `WorkItem`, as in §8.5. Include `ETag` if you support
conditional updates:

```http
ETag: "rev-42"
```

**Rules:**

- Return the same representation as the list endpoint for the same item.
- The response `id` must equal the requested `workItemId`.
- If the item is in another tenant or inaccessible, return `404`, not `403`,
  to avoid existence leaks.
- If you cannot determine the container from the ID, use `containerId`.

**Errors:** `401`, `403`, `404`, `429`, `500`.

### 8.8 `PATCH /workspaces/{workspaceId}/work-items/{workItemId}` (optional)

**Purpose:** update writable fields.

**Query:** `containerId` (optional context).

**Headers:** `If-Match` (optional), `Content-Type: application/json` or
`application/merge-patch+json`.

**Body:** a `WorkItemPatch`; see §6.3. Writable fields:
`title`, `description`, `priority`, `estimate`, `assignees`, `dates`,
`itemType`, `memberships`, `parent`, `labels`, `components`, `versions`,
`lifecycle`, `followers`, `iteration`, `milestones`, `timeTracking`,
`customFields`.

**Response 200:** the updated `WorkItem`, plus a new `ETag` if supported.

**Rules:**

- Empty body → `400`.
- Unknown or read-only field → `422 validation_error` with `invalidParams`.
- Do not change fields the caller omitted.
- Enforce `If-Match` if you emitted an ETag; mismatch → `412`.
- Unsupported update capability → `501 not_supported` (and advertise
  `work-items.update: false`).

**Errors:** `400`, `401`, `403`, `404`, `409`, `412`, `422`, `429`, `500`,
`501`.

### 8.9 `GET .../containers/{containerId}/workflow-states` (optional)

**Purpose:** expose the native workflow so callers can map and transition.

**Query:** `boardId` (optional).

**Response 200:**

```json
{
  "items": [
    {
      "id": "state-1",
      "name": "To Do",
      "category": "unstarted",
      "mapping": { "quality": "mapped" },
      "availableTransitions": [
        {
          "id": "t-1",
          "name": "Start",
          "targetState": {
            "id": "state-2",
            "name": "In Progress",
            "category": "started",
            "mapping": { "quality": "mapped" }
          }
        }
      ]
    }
  ]
}
```

**Rules:**

- Required per state: `name`, `mapping.quality`.
- Keep native state names; `category` is a lossy normalized view, not a
  replacement.
- `availableTransitions` is optional but recommended.

**Errors:** `401`, `403`, `404`, `500`.

### 8.10 `POST /workspaces/{workspaceId}/work-items/{workItemId}/transitions` (optional)

**Purpose:** perform a workflow action.

**Query:** `containerId` (optional context).

**Headers:** `Content-Type: application/json`, optional `If-Match`.

**Body:** exactly one of:

```json
{ "targetStateId": "state-2" }
```

```json
{ "targetStateName": "In Progress" }
```

**Response 200:** the updated `WorkItem`.

**Rules:**

- Validate that the transition is allowed from the current state.
- Target state not found or not allowed → `409 conflict` or
  `422 validation_error`; document which one you use consistently.
- Do not accept both `targetStateId` and `targetStateName`.
- Apply `If-Match` if provided; stale token → `412`.
- Return the full updated item, not just a status string.

**Errors:** `400`, `401`, `403`, `404`, `409`, `422`, `429`, `500`, `501`.

### 8.11 `GET .../containers/{containerId}/custom-fields` (optional)

**Purpose:** publish field definitions before values are mapped.

**Query:** `cursor`, `limit`.

**Response 200:**

```json
{
  "items": [
    {
      "id": "cf-1",
      "name": "Story Points",
      "description": "Relative estimate.",
      "valueType": "number",
      "cardinality": "one",
      "required": false,
      "writable": true,
      "scope": "container",
      "options": [],
      "extensions": { "com.tinytasks": { "position": 3 } }
    }
  ],
  "nextCursor": null
}
```

**Rules:**

- Required: `id`, `name`, `valueType`, `scope`.
- `valueType` and `scope` must use the spec enums.
- Use stable IDs, never display names.
- `writable` and `required` should be `null` when unknown, not guessed `false`.

**Errors:** `401`, `403`, `404`, `500`, `501`.

### 8.12 `GET .../containers/{containerId}/iterations` (optional)

**Purpose:** list sprints, cycles, iterations, or timeboxes.

**Query:** `boardId`, `cursor`, `limit`.

**Response 200:**

```json
{
  "items": [
    {
      "id": "s-1",
      "name": "Sprint 12",
      "kind": "sprint",
      "startsAt": "2026-01-05T00:00:00Z",
      "endsAt": "2026-01-19T00:00:00Z",
      "state": "active"
    }
  ],
  "nextCursor": null
}
```

**Rules:**

- Required per item: `id`, `kind`.
- Preserve the native kind; do not label every timebox as a sprint.
- Dates are RFC 3339 with timezone.

**Errors:** `401`, `403`, `404`, `500`.

### 8.13 `GET .../containers/{containerId}/milestones` (optional)

**Purpose:** list milestones or releases.

**Query:** `cursor`, `limit`.

**Response 200:**

```json
{
  "items": [
    {
      "id": "m-1",
      "name": "v1.0",
      "dueDate": "2026-03-01",
      "state": "open",
      "url": "https://tinytasks.example/ws-1/c-1/milestones/m-1"
    }
  ],
  "nextCursor": null
}
```

**Rules:**

- Required per item: `id`, `name`.
- Keep milestone and release semantics distinct in `extensions` if needed.

**Errors:** `401`, `403`, `404`, `500`.

### 8.14 `GET /workspaces/{workspaceId}/work-items/{workItemId}/comments` (optional)

**Purpose:** list comments, stories, updates, notes, or discussions.

**Query:** `cursor`, `limit`.

**Response 200:**

```json
{
  "items": [
    {
      "id": "cm-1",
      "body": "Reproduced on iOS 18.",
      "contentType": "text/plain",
      "author": { "id": "u-1", "displayName": "Ada Lovelace" },
      "createdAt": "2026-01-11T08:00:00Z",
      "updatedAt": null,
      "parentCommentId": null,
      "source": { "provider": "tinytasks", "resourceType": "comment" }
    }
  ],
  "nextCursor": null
}
```

**Rules:**

- Required per item: `id`, `body`.
- `contentType` tells the caller how to render `body`; sanitize HTML if you
  return `text/html`.
- Preserve threading with `parentCommentId` when available.

**Errors:** `401`, `403`, `404`, `429`, `500`, `501`.

### 8.15 `GET /workspaces/{workspaceId}/work-items/{workItemId}/attachments` (optional)

**Purpose:** list attachment metadata. File download is out of scope.

**Query:** `cursor`, `limit`.

**Response 200:**

```json
{
  "items": [
    {
      "id": "at-1",
      "name": "screenshot.png",
      "mediaType": "image/png",
      "sizeBytes": 182734,
      "url": "https://tinytasks.example/files/at-1",
      "createdAt": "2026-01-11T08:05:00Z",
      "createdBy": { "id": "u-1", "displayName": "Ada Lovelace" },
      "source": { "provider": "tinytasks", "resourceType": "attachment" }
    }
  ],
  "nextCursor": null
}
```

**Rules:**

- Required per item: `id`, `name`.
- `sizeBytes` is a non-negative integer or null.
- Do not inline binary payloads.

**Errors:** `401`, `403`, `404`, `429`, `500`, `501`.

---

## 9. Data-model rules that commonly fail conformance

### 9.1 Identity and references

- Every `id` is opaque and stable for the lifetime of the object.
- Never expose a sequential integer as a security-sensitive identifier without
  tenant scoping.
- `WorkItemRef` requires `id`. `PrincipalRef` requires `id`. `LabelRef` and
  `NamedRef` require `name`; their `id` is optional but should be stable when
  present.

### 9.2 Null vs absent

- `null` means “the provider can report this and it is empty”.
- Absent means “not provided in this representation”.
- For lifecycle flags, `null` means “cannot report”, `false` means “can report
  and the flag is false”. Do not collapse these.

### 9.3 Mapping metadata

Any normalized value (`status`, `priority`, relationships, custom fields) should
carry `mapping.quality`:

| Quality | Meaning |
|---|---|
| `exact` | Native concept matches the common concept |
| `mapped` | Deliberate, documented mapping |
| `lossy` | Information is lost |
| `inferred` | Derived, not native |
| `unmapped` | No safe mapping; use `nativeValue`/`nativeType` |

### 9.4 Extensions

- Namespace keys: reverse-DNS (`com.example.pm`), URI-like
  (`https://example.com/pm/fields`), or provider-prefixed (`jira:native`).
- Unknown extension keys must be safely ignorable.
- Do not put secrets, raw payloads, or unbounded blobs in extensions.
- Do not use extensions to redefine a core field.

### 9.5 Time and estimates

- `EstimateValue` requires `value` (number) and `unit` (string).
- Do not infer priority from labels or workflow state.
- Preserve the native scale in `nativeScale` or `nativeValue`.
- Convert all timestamps to UTC with an explicit offset or `Z`.

---

## 10. Testing your implementation

### 10.1 Run the conformance checker

From the gateway package:

```bash
pip install -e '.[server,dev]'

# Read-only, Core Read:
pm-gateway-conformance \
  --base-url https://tools.example.com \
  --token-env COMPLIANT_TOKEN

# Discover the first workspace/container/work item automatically;
# or pin IDs for deterministic CI:
pm-gateway-conformance \
  --base-url https://tools.example.com \
  --token-env COMPLIANT_TOKEN \
  --workspace-id ws-1 \
  --container-id c-1 \
  --work-item-id wi-1

# Machine-readable output for CI:
pm-gateway-conformance ... --json

# Also probe optional read operations:
pm-gateway-conformance ... --full

# Mutating checks, only in a staging environment:
pm-gateway-conformance ... --write

# Skip the anonymous 401 probe when the service is behind mTLS:
pm-gateway-conformance ... --no-auth-check
```

Exit codes: `0` no failures, `1` a check failed, `2` invalid configuration. A
skip is not a failure; a skipped check means the required fixture was
unavailable or the optional feature was not advertised. If a required check is
skipped, the overall result is `INCOMPLETE` rather than `COMPLIANT`; rerun with
explicit IDs in a tenant that contains the required fixtures.

### 10.2 What the checker verifies

- Capabilities shape (`apiVersion`, `coreProfile`, `features`) and, with
  `--full`, that a feature advertised as supported does not return `501`.
- Workspace, container, and work-item page shapes and required fields.
- Cursor pagination does not loop or repeat the first item.
- Work-item retrieval and ID echo.
- Anonymous access is rejected with `401 unauthenticated`.
- Missing resources return `404 not_found` with the standard error shape.
- Optional operations either return a valid page or `501 not_supported`.
- With `--write`: merge-patch update and stale `If-Match` → `412`.
- With `--transition-to`/`--transition-to-id`: transition returns a work item.

### 10.3 What the checker does not verify

Automate these separately:

- **Filter correctness:** that `stateId`, `assigneeId`, `changedSince`,
  `archived`, `deleted`, and `sort` actually filter/sort.
- **Tenant isolation:** that cross-tenant IDs return `404` and never leak data.
- **Pagination stability under concurrent writes.**
- **Merge-patch semantics:** omitted fields unchanged, `null` clears.
- **Transition validity:** invalid targets return `409`/`422`.
- **Rate limiting and `Retry-After`.**
- **Comment/attachment content safety.**
- **Performance and timeouts.**

Recommended test matrix:

| Test | Fixture |
|---|---|
| Capabilities with no optional features | Minimal tenant |
| Empty workspace list | Token with no access |
| Multiple pages | > `limit` items |
| Invalid cursor | `cursor=not-a-cursor` → `400` |
| Unsupported filter | `?stateId=...` on a provider without filters → `400` |
| Cross-tenant access | ID from tenant B with token A → `404` |
| Stale ETag | `If-Match: "stale"` → `412` |
| Invalid transition | `targetStateId` not reachable → `409`/`422` |
| HTML comment body | Sanitized or clearly marked `text/html` |

---

## 11. Connecting through the gateway

### 11.1 Configuration

```bash
export PM_GATEWAY_API_TOKEN='gateway-token'
export PM_GATEWAY_ALLOWED_PROVIDER_HOSTS='tools.example.com'
pm-gateway
```

The tool must be reachable over HTTPS. For local development only:

```bash
export PM_GATEWAY_ALLOW_INSECURE_HTTP=1
```

### 11.2 HTTP call

```bash
curl -s http://127.0.0.1:8080/pm/v1/workspaces \
  -H "Authorization: Bearer $PM_GATEWAY_API_TOKEN" \
  -H "X-PM-Provider: compliant" \
  -H "X-PM-Provider-Base-Url: https://tools.example.com" \
  -H "X-PM-Provider-Credential: $TOOL_TOKEN"
```

The gateway forwards `$TOOL_TOKEN` upstream as `Authorization: Bearer` and
strips the three `X-PM-*` headers.

### 11.3 SDK call

```python
from pm_gateway import Operation, PMGateway, Provider

gateway = PMGateway()
page = await gateway.execute(
    Operation.LIST_WORK_ITEMS,
    provider=Provider.COMPLIANT,
    credential=os.environ["TOOL_TOKEN"],
    base_url="https://tools.example.com",
    workspaceId="ws-1",
    containerId="c-1",
)
```

### 11.4 Troubleshooting

| Symptom | Likely cause |
|---|---|
| Gateway returns `400` “Base URL is required” | Missing `X-PM-Provider-Base-Url` for `compliant` |
| Gateway returns `400` “host is not allowlisted” | Add the exact host to `PM_GATEWAY_ALLOWED_PROVIDER_HOSTS` |
| Gateway returns `401` | Gateway token missing/invalid, **or** upstream credential rejected |
| Gateway returns `404` for every path | Upstream is not mounted at `/pm/v1` |
| Gateway returns `501` | Tool returned `not_supported` for that operation |
| Gateway works, direct call fails | Direct call is missing the bearer token or uses the wrong base path |
| PATCH returns `415` | Server rejects `application/json`; accept both JSON and merge-patch content types |

---

## 12. Implementation task list for AI agents

Work in this order. Do not skip the conformance run at the end.

### Phase 1 — contract and scaffolding

1. Read `spec/pm-api-openapi-v0.1.yaml` and this guide.
2. Decide which optional operations you will support. Record the decision in a
   capability table before coding.
3. Generate models/server stubs with generator `7.24.0`.
4. Mount the service at `/pm/v1`.
5. Add JSON error handling and request IDs.

### Phase 2 — authentication and tenancy

6. Implement bearer-token middleware.
7. Map token → principal → tenant.
8. Scope every query by tenant.
9. Return `401` for missing/invalid tokens and `403` for insufficient scope.
10. Add a test for cross-tenant access returning `404`.

### Phase 3 — Core Read

11. Implement `GET /capabilities` with truthful feature flags.
12. Implement `GET /workspaces` with pagination.
13. Implement `GET /workspaces/{workspaceId}/containers`.
14. Implement `GET .../work-items` with filters, stable ordering, and cursor
    pagination.
15. Implement `GET /workspaces/{workspaceId}/work-items/{workItemId}`.
16. Ensure every work item includes `id`, `title`, and `source`.

### Phase 4 — optional operations

17. Add boards and board scope if the native model has boards.
18. Add workflow states and transitions if the native model has a workflow.
19. Add custom-field definitions before exposing custom-field values.
20. Add iterations and milestones if the native model has them.
21. Add comments and attachments as read-only listings.
22. Add `PATCH` only if writes are safe; implement merge-patch semantics and
    ETag/`If-Match`.

### Phase 5 — verification

23. Run the checker read-only:
    `pm-gateway-conformance --base-url ... --token-env ...`
24. Fix every `FAIL`. Explain every `SKIP`.
25. Run with `--full` and fix optional-operation failures.
26. In staging, run with `--write` and a targeted transition.
27. Run the manual test matrix in §10.3.
28. Connect through the gateway and repeat the Core Read calls.
29. Record the exact conformance command and output in your repository.

### Definition of done

- [ ] All Core Read endpoints return the exact wire shapes.
- [ ] Bearer auth is required; anonymous access returns `401 unauthenticated`.
- [ ] Cross-tenant access returns `404 not_found`.
- [ ] Pagination is stable and does not loop.
- [ ] Unsupported filters are rejected or declared, never ignored.
- [ ] Errors are `ApiError` JSON with a valid `code`.
- [ ] Capabilities match reality.
- [ ] Optional operations return `501 not_supported` when unavailable.
- [ ] `pm-gateway-conformance` reports `COMPLIANT` (read-only).
- [ ] `--write` and transition checks pass in staging, if those features are
      advertised.
- [ ] The gateway can call the service with `X-PM-Provider: compliant`.

---

## Appendix A — Minimal Core Read response fixtures

Use these as test fixtures; they are intentionally small.

`GET /capabilities`

```json
{
  "apiVersion": "1.0",
  "coreProfile": "core-read-0.1",
  "provider": { "key": "tinytasks", "productName": "TinyTasks" },
  "features": [
    { "name": "workspaces.list", "supported": true },
    { "name": "containers.list", "supported": true },
    { "name": "work-items.read", "supported": true }
  ]
}
```

`GET /workspaces`

```json
{
  "items": [
    {
      "id": "ws-1",
      "name": "Acme",
      "provider": { "key": "tinytasks", "productName": "TinyTasks" }
    }
  ],
  "nextCursor": null
}
```

`GET /workspaces/ws-1/containers`

```json
{
  "items": [{ "id": "c-1", "kind": "project", "name": "Mobile app" }],
  "nextCursor": null
}
```

`GET /workspaces/ws-1/containers/c-1/work-items`

```json
{
  "items": [
    {
      "id": "wi-1",
      "title": "Fix login",
      "status": {
        "id": "state-2",
        "name": "In Progress",
        "category": "started",
        "mapping": { "quality": "mapped" }
      },
      "source": { "provider": "tinytasks", "resourceType": "work-item" }
    }
  ],
  "nextCursor": null
}
```

`GET /workspaces/ws-1/work-items/wi-1`

```json
{
  "id": "wi-1",
  "title": "Fix login",
  "status": {
    "id": "state-2",
    "name": "In Progress",
    "category": "started",
    "mapping": { "quality": "mapped" }
  },
  "source": {
    "provider": "tinytasks",
    "resourceType": "work-item",
    "revision": "42"
  }
}
```

`404` error

```json
{
  "code": "not_found",
  "message": "The work item does not exist.",
  "retryable": false
}
```

## Appendix B — Feature-name reference

| Feature | Endpoint |
|---|---|
| `workspaces.list` | `GET /workspaces` |
| `containers.list` | `GET .../containers` |
| `boards.list` | `GET .../boards` |
| `board-scope.read` | `GET .../board-scope` |
| `work-items.read` | `GET .../work-items`, `GET .../work-items/{id}` |
| `work-items.update` | `PATCH .../work-items/{id}` |
| `work-items.transition` | `POST .../transitions` |
| `work-items.archived-filter` | `archived` query parameter |
| `work-items.deleted-filter` | `deleted` query parameter |
| `workflow-states.list` | `GET .../workflow-states` |
| `custom-fields.discovery` | `GET .../custom-fields` |
| `iterations.list` | `GET .../iterations` |
| `milestones.list` | `GET .../milestones` |
| `comments.read` | `GET .../comments` |
| `attachments.read` | `GET .../attachments` |

These names are conventions used by the gateway and checker. The OpenAPI schema
only constrains `FeatureCapability.name` to a string; adopting these names keeps
capability reporting interoperable.

## Appendix C — Glossary

| Term | Meaning |
|---|---|
| **Workspace** | Top-level tenant or organizational boundary |
| **Container** | Project, board, plan, list, repository, sheet, or similar |
| **Work item** | Issue, card, task, row, or to-do |
| **Workflow state** | Native status plus a normalized category |
| **Iteration** | Sprint, cycle, iteration, or timebox |
| **Milestone** | Milestone or release |
| **Extension** | Namespaced provider-specific value |
| **Mapping quality** | How faithfully a native value maps to the common model |

---

## Appendix D — Known draft-contract quirks

These are intentional compatibility notes for the current alpha. Re-check them
when the spec version changes.

1. **Gateway headers are not forwarded.** The spec lists `X-PM-Provider*` as
   required parameters because it also documents the gateway API. A compliant
   upstream receives only `Authorization` (and `X-PM-Workspace-Slugs` for
   `list_workspaces`).
2. **PATCH content type.** The spec advertises
   `application/merge-patch+json`; the gateway sends `application/json`. Accept
   both.
3. **Boards exception.** Providers without boards return an empty page and
   advertise `boards.list: false`; other unsupported optional operations return
   `501 not_supported`.
4. **Capabilities feature names are conventions.** The schema does not enumerate
   them, but the gateway and checker use the names in Appendix B.
5. **`ETag` is optional.** If you do not emit it, conditional updates are not
   tested. If you do emit it, enforce `If-Match`.
6. **`compliant` is not in the spec's `X-PM-Provider` enum.** The enum lists
   `plane`, `jira`, and `planner`, but the gateway accepts `compliant`. When
   generating a client, send the header as a plain string or extend the enum in
   the generated client. A compliant upstream never receives this header from
   the gateway.

---

## Appendix E — Commands

```bash
# Validate the contract
openapi-generator-cli validate -i spec/pm-api-openapi-v0.1.yaml

# Generate a FastAPI server skeleton
openapi-generator-cli generate \
  -i spec/pm-api-openapi-v0.1.yaml \
  -g python-fastapi \
  -o build/compliant-server \
  --additional-properties=packageName=compliant_api,fastapiImplementationPackage=impl,serverPort=8080,disallowAdditionalPropertiesIfNotPresent=false \
  --global-property=apiTests=false,modelTests=false

# Install the gateway and checker
pip install -e '.[server,dev]'

# Read-only conformance
pm-gateway-conformance \
  --base-url https://tools.example.com \
  --token-env COMPLIANT_TOKEN

# Full optional read checks
pm-gateway-conformance \
  --base-url https://tools.example.com \
  --token-env COMPLIANT_TOKEN \
  --full

# Mutating checks in staging
pm-gateway-conformance \
  --base-url https://staging.tools.example.com \
  --token-env COMPLIANT_TOKEN \
  --write \
  --transition-to "In Progress"

# Machine-readable output
pm-gateway-conformance ... --json
```
