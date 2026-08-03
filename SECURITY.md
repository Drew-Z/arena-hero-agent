# Security Policy

## Supported Versions

Security fixes are applied to the latest release on the default branch. Older snapshots are not guaranteed to receive backports.

## Reporting a Vulnerability

Do not open a public issue for a vulnerability or exposed credential. Use GitHub's private vulnerability reporting or private security advisory flow for the repository. Include reproduction steps, affected versions, and impact, but do not include live API keys, model credentials, player IDs, or production logs.

Maintainers aim to acknowledge a private report within three business days and
will coordinate validation, remediation, and disclosure on a best-effort basis.
Critical credential exposure should still be rotated immediately rather than
waiting for acknowledgement.

If a credential has already appeared in chat, logs, an issue, a commit, or Git history, rotate it immediately at the provider. Removing or rewriting the text does not invalidate the credential.

## Security Boundaries

- The main Agent needs only the Arena Hero key and should run without root privileges.
- Docker runs the Agent as an unprivileged user and mounts the game key at runtime.
- The supervisor reads journald and writes reports; model access is disabled by default.
- The optimizer can write runtime configuration and restart the systemd service. It runs as root and must be explicitly enabled.
- No component should log credential values or Authorization headers.
