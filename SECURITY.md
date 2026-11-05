# Security Policy

## Supported versions

`opennomos-pm-gateway` is currently an alpha technical preview. Security fixes
are applied to the latest published release and the `main` branch.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting for this repository
(**Security** → **Report a vulnerability**). Do not open a public issue for a
suspected vulnerability.

Include:

- the affected version or commit;
- a description of the impact and the attacker's prerequisites;
- minimal reproduction steps;
- whether provider credentials, `base_url`, or the gateway bearer token are
  involved;
- any suggested fix.

Do not include real credentials, tokens, or private provider data in a report.
Use placeholders and rotate any secret that may have been exposed.

## Trust boundary

The gateway handles provider credentials but is not an authorization boundary:

- Anyone who can call the HTTP service with the gateway bearer token can use any
  provider credential they supply.
- A provider `base_url` determines where that credential or an acquired token is
  sent. It must not be chosen by an untrusted user for a stored credential.
- The OpenNomos backend (or any other consumer) is responsible for scoping
  credentials to the correct user or integration and for validating integration
  base URLs.
- Deployments must terminate TLS in front of the service and must not log
  `Authorization` or `X-PM-Provider-Credential`.

See `README.md` for the configuration options that enforce the provider
destination policy.

## Secret scanning

Credentials must reach the gateway through the environment, never through the
repository. Two gates enforce that:

- **Locally, before every push.** `hooks/pre-push` scans the commits a push is
  about to publish — not the working tree — and blocks the push when added lines
  match a known credential pattern or when a credential file (`.env`, `.env.*`,
  `*.pem`, `*.key`, `id_rsa`, `.pypirc`, …) is added. Install it once per clone:

  ```sh
  ./scripts/install-hooks.sh            # or: pre-commit install --hook-type pre-push
  ```

  `--check`, `--uninstall` and `--force` cover the other cases; `--force` backs
  up a hook it does not own before replacing it.

- **In CI, as a backstop.** The `secret-scan` job in
  `.github/workflows/ci.yml` runs gitleaks against the pushed range, so
  `git push --no-verify` still gets caught. Publishing a release depends on that
  job succeeding.

Rules live in one place: `hooks/pre-push` for the pattern list and
`.gitleaks.toml` for the CI allowlist. To bypass the local hook for a single
push, use `ALLOW_SECRETS=1 git push`; the skip is announced on stderr. For a
known false positive, append `# secret-scan:allow` to the line.

If a real credential was ever committed, rotating it is mandatory: it stays in
local history even after the file is rewritten, and it is in the remote history
once pushed.

