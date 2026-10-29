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
  contents become the repository root. Tags are not copied by the split; create
  and push `v*` tags in the standalone repository.
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

- The PyPI project name is registered. Keep trusted publishing configured and
  the `pypi` environment reviewer rule in place for future releases.
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

The package version is derived from git tags by `setuptools-scm`; do not edit
`src/pm_gateway/_version.py`. It reads the installed distribution metadata and
only contains a fallback for the vendored monorepo copy or an uninstalled source
tree.

1. Decide the next PEP 440 version (`0.1.0a2` for a pre-release, `0.1.0` for a
   final release) and add a matching entry to `CHANGELOG.md`.
2. If the contract changed, edit `spec/pm-api-openapi-v0.1.yaml` and regenerate:

   ```sh
   ./scripts/generate-server.sh
   ```

3. If the OpenNomos docs mirror still exists, copy the spec to
   `docs/reference/pm-api-openapi-v0.1.yaml` and run `python -m pytest` (the
   mirror test fails on drift).

### Version baseline

The already-published `0.1.0a1` predates tag-derived versioning. If the
standalone repository has no `v*` tag, the CI version job creates a temporary
local `v0.1.0a1` tag on the root commit for version calculation only; it is not
pushed. Main-branch builds therefore use `0.1.0a2.devN`, which remains ordered
after `0.1.0a1`. The first real release tag (for example, `v0.1.0a2`) becomes
the permanent baseline; do not push a `v0.1.0a1` tag unless you intend to reset
the development version counter.

## 3. Local verification

From the repository root:

```sh
python -m pip install -e '.[server,dev]'
python -m pytest
./scripts/generate-server.sh --check
python -m build
python -m twine check dist/*
```

`setuptools-scm` reads the version from the git tag and history. A shallow
checkout, or a source tree without `.git`, needs
`SETUPTOOLS_SCM_PRETEND_VERSION` set to an explicit PEP 440 version. The Docker
build accepts this as `--build-arg PM_GATEWAY_VERSION=...` and defaults to
`0.0.0` for local image builds.

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

The **ci** workflow also builds and uploads every push to `main` to TestPyPI
through trusted publishing. You can run it manually (`workflow_dispatch`) as
well. Re-running the same commit is safe because the publish action is configured
with `skip-existing: true`.

Install from TestPyPI with the real PyPI index as a dependency fallback:

```sh
pip install --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple \
  'opennomos-pm-gateway[server]==X.Y.Z'
```

## 5. Publish

1. Tag the release. The tag is the version; no version file edit is needed:

   ```sh
   git tag -a v0.1.0a2 -m "opennomos-pm-gateway 0.1.0a2"
   git push origin v0.1.0a2
   ```

2. The `publish` job builds, checks metadata, and uploads that exact version to
   PyPI.
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
