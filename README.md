# OpenNomos PM Gateway

Python package providing both an in-process SDK and an optional standalone HTTP
service for the draft PM interoperability API. It does not import OpenNomos
backend code or share its database.

## In-process SDK

Install the root OpenNomos package (which includes the SDK source) or install
this package directly. The SDK is asynchronous and calls the provider adapter
in-process: you do not need to start the gateway service, configure a gateway
bearer token, or make an HTTP request to the gateway.

```sh
pip install -e ./pm-gateway
```

Create a `PMGateway` and pass the provider credentials on each `execute` call.
Supported provider keys are `plane`, `jira`, and `planner`. Jira requires its
site URL as `base_url`; Plane and Planner use their documented defaults unless
you supply a URL. Credentials use the provider's expected format: Plane API
token, Jira `email:api-token`, or Planner `tenant_id:client_id:client_secret`.
Keep credentials in a secret store or environment variables, not in source
control.

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
argument takes a `Provider` enum member: `Provider.PLANE`, `Provider.JIRA`, or
`Provider.PLANNER`. Use operation members such as
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
log credentials or include them in exception output. The OpenNomos backend uses
this SDK directly: it resolves and decrypts stored credentials, then supplies
them per operation.

## Generated server code

The OpenAPI document at `../docs/reference/pm-api-openapi-v0.1.yaml` is the
contract source. Generated API routes and models are grouped under
`src/pm_gateway/generated/` (APIs, models, and `security_api.py`). **Do not edit
those generated files by hand.** Implementations belong under
`src/pm_gateway/impl/` and other handwritten modules. Regenerate with:

```sh
./pm-gateway/scripts/generate-server.sh
# Verify generated files without changing the package:
./pm-gateway/scripts/generate-server.sh --check
```

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
cd pm-gateway
python -m pip install -e '.[server,dev]'
PM_GATEWAY_API_TOKEN='replace-with-a-long-random-secret' \
  uvicorn pm_gateway.app:app --host 127.0.0.1 --port 8080
```

The service mounts the draft OpenAPI API at `/pm/v1`. `/healthz` is an
unauthenticated liveness endpoint; API operations require the gateway bearer token.

Plane, Jira, and Microsoft Planner adapters are implemented in
`src/pm_gateway/adapters/`. They support workspace/container discovery, work-item
reads, workflow-state reads and transitions; Jira and Planner also expose their
current board/bucket behavior. Capabilities mark custom fields, milestones,
comments, attachments, and general field updates unsupported until implemented.

The bearer token protects the optional HTTP service and is distinct from the PM
vendor credential. HTTP consumers send provider identity, provider credential,
and optional provider base URL per request. The service uses request-scoped
clients and does not persist credentials. The backend does not use or start this
HTTP service. To run it as a separate gateway, use
`docker compose -f docker-compose.pm-gateway.yml up --build` with
`PM_GATEWAY_API_TOKEN` set, or start it with the command above.
