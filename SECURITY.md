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
