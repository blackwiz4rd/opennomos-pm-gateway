# Releasing `pm-gateway`

This repository prepares, but does not publish, the `pm-gateway` distribution.
Follow this checklist when the project is ready to release.

## 1. Prerequisites

### Public project home

PyPI renders the README and `[project.urls]` for people who have no repository
access. Before publishing:

- make the repository public, or create a dedicated public repository for the
  package and update the URLs in `pyproject.toml` and `README.md`;
- confirm every absolute link in `README.md` resolves without authentication;
- confirm `LICENSE`, `CHANGELOG.md`, and the OpenAPI document are reachable.

If the repository or documentation site is private, PyPI visitors will see dead
links. Make the repository public, or point `[project.urls]` and the README at a
public mirror, before the first release.

### PyPI trusted publishing

1. Create or verify the PyPI account and enable two-factor authentication.
2. Add a pending publisher:
   - Project: `pm-gateway`
   - Owner/repository: the public repository
   - Workflow: `pm-gateway.yml`
   - Environment: `pypi`
3. Add the same pending publisher on TestPyPI with environment `testpypi`.
4. In GitHub, create environments `pypi` and `testpypi`; consider requiring a
   reviewer for `pypi`.

No `PYPI_API_TOKEN` secret is required; the workflow uses OIDC trusted publishing.

## 2. Version and changelog

1. Update `src/pm_gateway/_version.py`.
2. Add a matching entry to `CHANGELOG.md`.
3. Confirm the version is valid PEP 440 (`0.1.0a1` for a pre-release, `0.1.0` for
   a final release).
4. If the contract changed, update
   `../docs/reference/pm-api-openapi-v0.1.yaml` and regenerate:

   ```sh
   OPENAPI_GENERATOR_VERSION=7.24.0 ./scripts/generate-server.sh
   ```

## 3. Local verification

```sh
cd pm-gateway
python -m pip install -e '.[server,dev]'
python -m pytest
./scripts/generate-server.sh --check
python -m build
python -m twine check dist/*
```

Then, in a clean virtual environment:

```sh
python -m venv /tmp/pm-gateway-smoke
/tmp/pm-gateway-smoke/bin/pip install \
  'pm-gateway[server] @ file:///absolute/path/to/dist/pm_gateway-X.Y.Z-py3-none-any.whl'
PM_GATEWAY_API_TOKEN=test /tmp/pm-gateway-smoke/bin/pm-gateway &
curl http://127.0.0.1:8080/healthz
```

Also install the wheel without the `server` extra and confirm `pm-gateway`
prints the "install the server extra" message instead of a traceback.

## 4. TestPyPI dry run

```sh
python -m twine upload --repository testpypi dist/*
```

Or run the **pm-gateway** workflow manually (`workflow_dispatch`); it builds and
uploads to TestPyPI through trusted publishing.

Install from TestPyPI with the real PyPI index as a dependency fallback:

```sh
pip install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple \
  'pm-gateway[server]==X.Y.Z'
```

## 5. Publish

1. Tag the release with the package-prefixed tag:

   ```sh
   git tag pm-gateway-v0.1.0a1
   git push origin pm-gateway-v0.1.0a1
   ```

2. The `publish` job builds, checks metadata, and uploads to PyPI.
3. Verify that the PyPI project page renders the README, license, and links.

PyPI versions are immutable. If a release is wrong, bump the version; do not try
to overwrite it.

## 6. After the first publish: stop vendoring

The repository's root `pyproject.toml` currently includes `pm-gateway/src` in the
`opennomos` distribution. Once `pm-gateway` is on PyPI:

1. Remove `"pm-gateway/src"` from `[tool.setuptools.packages.find].where` and
   `pm_gateway*` from `include`.
2. Add `"pm-gateway>=0.1.0a1,<0.2"` to the root dependencies.
3. Use `pip install -e ./pm-gateway` for local development.
4. Run the backend test suite to confirm the SDK still resolves.

This prevents two distributions from owning the same `pm_gateway` files.

## 7. Recommended follow-ups before 1.0

- Replace the SDK's `fastapi.HTTPException` contract with a framework-neutral
  exception hierarchy.
- Add typed `execute` overloads or generated SDK methods.
- Implement provider-native pagination instead of in-memory slicing.
- Add live-provider smoke tests and a conformance suite.
- Publish a standalone specification document with RFC 2119 language and a
  versioning/governance policy.
