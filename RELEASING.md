# Releasing `opennomos-pm-gateway`

This directory is self-contained. It can be split into the standalone repository
`https://github.com/blackwiz4rd/opennomos-pm-gateway` and released to PyPI from
there. Nothing in this document publishes anything by itself.

## 0. Split into the standalone repository

Create an empty repository at
`https://github.com/blackwiz4rd/opennomos-pm-gateway` (no README, no license).
Commit the package, then run the split from the OpenNomos repository root:

```sh
git subtree split --prefix=pm-gateway -b opennomos-pm-gateway
git remote add pm-gateway git@github.com:blackwiz4rd/opennomos-pm-gateway.git
git push pm-gateway opennomos-pm-gateway:main
```

Notes:

- `git subtree split` rewrites the `pm-gateway/` history so the directory
  contents become the repository root.
- `.github/workflows/ci.yml` is inert inside the monorepo but becomes the CI
  workflow in the standalone repository.
- `OSLC.md` and `STANDARDIZATION_ROADMAP.md` are gitignored working drafts and
  are not copied by the split. Move them into a tracked `docs/` directory or
  force-add them first if they should ship.
- The canonical OpenAPI contract is `spec/pm-api-openapi-v0.1.yaml`. The
  monorepo keeps a mirror at `docs/reference/` for its documentation site;
  `tests/test_spec_mirror.py` keeps the two identical while both exist.
- After the split, the monorepo can either remove `pm-gateway/` and depend on
  the published package, or add the new repository as a submodule at
  `pm-gateway/`.

## 1. Prerequisites

### Public project home

PyPI renders the README and `[project.urls]` for people who have no repository
access. Confirm that the repository and every absolute link in `README.md` are
publicly reachable before the first release. If the repository is private, make
it public or point the URLs at a public mirror.

### PyPI trusted publishing

1. Create or verify the PyPI account and enable two-factor authentication.
2. Add a pending publisher:
   - Project: `opennomos-pm-gateway`
   - Owner/repository: the public repository
   - Workflow: `ci.yml`
   - Environment: `pypi`
3. Add the same pending publisher on TestPyPI with environment `testpypi`.
4. In GitHub, create environments `pypi` and `testpypi`; consider requiring a
   reviewer for `pypi`.

No `PYPI_API_TOKEN` secret is required; the workflow uses OIDC trusted
publishing.

### Repository and release security

Before the first public release:

- Reserve the PyPI project name immediately. `opennomos-pm-gateway` was still
  unregistered when this checklist was written, and the GitHub repository is
  public; an attacker could otherwise publish a malicious package under the
  name the README tells users to install.
- Enable GitHub private vulnerability reporting and the `SECURITY.md` contact
  path.
- Enable Dependabot alerts/updates for Python and GitHub Actions, secret
  scanning, and push protection.
- Protect `main` and release tags: require CI, prevent force pushes/deletions,
  and limit who can create `v*` tags.
- Add required reviewers to the `pypi` environment so publishing is deliberate.
- Keep `PM_GATEWAY_ALLOWED_PROVIDER_HOSTS` explicit for any Jira/Plane host not
  covered by the built-in Jira Cloud/Plane Cloud defaults, and for every
  compliant provider. Never let an untrusted user choose a stored credential's
  base URL.
- Never commit `.env`, provider tokens, or the gateway bearer token. Use a
  secret manager and rotate any value that has been exposed.

## 2. Version and changelog

1. Update `src/pm_gateway/_version.py`.
2. Add a matching entry to `CHANGELOG.md`.
3. Confirm the version is valid PEP 440 (`0.1.0a1` for a pre-release, `0.1.0` for
   a final release).
4. If the contract changed, edit `spec/pm-api-openapi-v0.1.yaml` and regenerate:

   ```sh
   ./scripts/generate-server.sh
   ```

5. If the OpenNomos docs mirror still exists, copy the spec to
   `docs/reference/pm-api-openapi-v0.1.yaml` and run `python -m pytest` (the
   mirror test fails on drift).

## 3. Local verification

From the repository root:

```sh
python -m pip install -e '.[server,dev]'
python -m pytest
./scripts/generate-server.sh --check
python -m build
python -m twine check dist/*
```

Then, in a clean virtual environment:

```sh
python -m venv /tmp/opennomos-pm-gateway-smoke
/tmp/opennomos-pm-gateway-smoke/bin/pip install \
  'opennomos-pm-gateway[server] @ file:///absolute/path/to/dist/opennomos_pm_gateway-X.Y.Z-py3-none-any.whl'
PM_GATEWAY_API_TOKEN=test /tmp/opennomos-pm-gateway-smoke/bin/pm-gateway &
curl http://127.0.0.1:8080/healthz
```

Also install the wheel without the `server` extra and confirm `pm-gateway`
prints the "install the server extra" message instead of a traceback.

## 4. TestPyPI dry run

```sh
python -m twine upload --repository testpypi dist/*
```

Or run the **ci** workflow manually (`workflow_dispatch`); it builds and uploads
to TestPyPI through trusted publishing.

Install from TestPyPI with the real PyPI index as a dependency fallback:

```sh
pip install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple \
  'opennomos-pm-gateway[server]==X.Y.Z'
```

## 5. Publish

1. Tag the release:

   ```sh
   git tag v0.1.0a1
   git push origin v0.1.0a1
   ```

2. The `publish` job builds, checks metadata, and uploads to PyPI.
3. Verify that the PyPI project page renders the README, license, and links.

PyPI versions are immutable. If a release is wrong, bump the version; do not try
to overwrite it.

## 6. After the first publish: stop vendoring

If the monorepo still vendors the package, update its root `pyproject.toml`:

1. Remove `"pm-gateway/src"` from `[tool.setuptools.packages.find].where` and
   `pm_gateway*` from `include`.
2. Add `"opennomos-pm-gateway>=0.1.0a1,<0.2"` to the root dependencies.
3. Use `pip install -e ./pm-gateway` for local development, or replace
   `pm-gateway/` with a git submodule of the standalone repository.
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
