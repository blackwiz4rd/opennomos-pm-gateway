# OpenNomos PM Gateway

Standalone Python service implementing the draft PM interoperability HTTP API.
It lives in this repository but has its own package, runtime, dependencies, and
adapter boundary; it does not import OpenNomos backend code or share its database.

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
python -m pip install -e '.[dev]'
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

The bearer token authenticates OpenNomos to the gateway; it is distinct from
the PM vendor credential. OpenNomos sends provider identity, provider
credential, and optional provider base URL on each request. The gateway uses
request-scoped clients and does not persist provider credentials. Configure
`PM_GATEWAY_URL` and the matching `PM_GATEWAY_API_KEY` in OpenNomos. Docker
Compose and `dev.sh` start the gateway alongside the backend.
