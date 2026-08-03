# Public Release Checklist

Use this checklist from a clean reviewed checkout before creating the GitHub repository or pushing the first commit.

## Before Git

- Rotate any Arena Hero or model key that has appeared in chat, logs, screenshots, or previous repositories.
- Keep the live `.env` outside the release checkout, or verify that it is ignored and not staged.
- Remove or archive local logs, `.venv`, temporary source snapshots, and generated reports.
- Run the credential scan, then manually review public files for local paths, hostnames, IP addresses, player identifiers, and provider-specific model IDs:

```powershell
python scripts/check_secrets.py
```

## First Commit

```bash
git init
git add .
git status --short
git check-ignore -v .env secrets/arena_hero_api_key.txt
git ls-files .env secrets/arena_hero_api_key.txt
```

The last command must print nothing. Do not use `git add -f` for credential files.

## Validation

```bash
python -m unittest discover -v
python -m compileall -q arena_farmer.py arena_health.py arena_supervisor.py arena_optimizer.py arena_version_monitor.py
python scripts/check_secrets.py
docker compose config
docker build --tag arena-hero-agent:release-candidate .
```

On Linux, also run `systemd-analyze verify deploy/*.service deploy/*.timer` after installing the expected service users and executable paths. The Windows development environment cannot run this check.

## GitHub Settings

- Enable private vulnerability reporting.
- Enable Dependabot security updates.
- Confirm the Community Standards checklist sees `README`, `LICENSE`, `CONTRIBUTING`, `CODE_OF_CONDUCT`, `SECURITY`, and issue templates.
- Set the default branch protection and require the CI workflow before merging.
- Confirm tag releases call the reusable CI workflow before publishing an image.
- Confirm the repository, documentation, issue, and source URLs in `pyproject.toml`.

The release workflow rejects a tag that does not exactly match the version in
`pyproject.toml`, waits for the complete reusable CI workflow, and publishes
SBOM and provenance attestations alongside the image digest.
