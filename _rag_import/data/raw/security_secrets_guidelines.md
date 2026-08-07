# Security Guidelines for Handling Secrets

## Never Commit Secrets

API keys, database credentials, and tokens must never be committed to any repository, including
private ones. A pre-commit hook (`detect-secrets`) blocks commits containing patterns matching
known credential formats, but this is a safety net, not the primary control — engineers are
individually responsible for not committing secrets in the first place.

If a secret is accidentally committed and pushed, it must be treated as compromised immediately:
rotate it within one hour of discovery, even if the commit is later removed from history. Git
history rewrites do not reliably scrub secrets from forks, CI logs, or cached clones, so rotation
is the only real remediation.

## Secret Storage

All secrets are stored in the internal Vault cluster, never in `.env` files committed to git,
never in Slack messages, and never in ticket descriptions. Local `.env` files are permitted for
development but must be listed in `.gitignore` and are excluded from all backup snapshots.

Services fetch secrets from Vault at startup using a short-lived service identity token; secrets
are never baked into Docker images. Any Dockerfile that copies a `.env` file or hardcodes a
credential fails the image-scanning step in CI and cannot be deployed.

## Access Review

Vault access is scoped per-service, not per-team, and is reviewed quarterly by the Security team.
Any engineer who has not accessed a given secret path in 90 days has that grant automatically
revoked and must re-request access if needed.

## Incident Response for Leaked Credentials

A leaked credential is treated as a security incident, not a routine bug. File it via the
Incident Response Runbook process at Sev-2 minimum, rotate the credential, and check access logs
for the exposure window before closing the incident.
