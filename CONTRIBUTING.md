# Contributing

Contributions should preserve the Agent's deterministic Tick loop, Core-survival priority, and secret-free testability.

For a rule-dependent strategy change, deployment architecture change, or new
privileged process, open an issue before implementation so the compatibility
and operational boundary can be agreed first. Small bug fixes and documentation
corrections may go directly to a focused pull request.

## Setup

Before changing any tracked file, synchronize the clean `main` checkout with
GitHub using the cross-platform preflight:

```bash
python scripts/sync-main.py
```

The command fetches only `origin/main`, fast-forwards when the checkout is
behind, and refuses dirty, ahead, divergent, detached, or non-`main` states.

```bash
python -m venv .venv
python -m pip install --require-hashes -r requirements-build.lock
python -m pip install --require-hashes -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .
python -m unittest discover -v
```

On Windows, `scripts/bootstrap.ps1` performs the same setup. On POSIX systems, use `sh scripts/bootstrap.sh`.

After the preflight succeeds, create a topic branch when contributing through a
pull request. Keep commits scoped and write commit messages that describe the
behavior changed, not only the files.

## Before Opening a Pull Request

Run:

```bash
python -m unittest discover -v
python -m compileall -q arena_farmer.py arena_health.py arena_supervisor.py arena_optimizer.py arena_version_monitor.py
python scripts/check_secrets.py
```

For deployment changes, also validate `docker build .`, `docker compose config`,
shell syntax, `python -m unittest -v test_systemd_deploy`, and systemd units on
Linux.

Documentation-only changes may omit the Python test suite when they do not
change commands or configuration, but must still run the credential scan and
manually verify edited links and examples.

## Change Guidelines

- Use the official Arena Hero SDK; do not reproduce transport or state-model logic.
- Treat every Turn as a complete authoritative replacement and submit only current-Tick plans.
- Keep normal production at or below the tested 32-Unit redemption profile. Any dynamic-price expansion to Unit 21 or later requires an explicit strategy goal, SDK-price checks in every production branch, and focused tests.
- Add focused tests for tactic decisions and all configuration behavior.
- Keep model output advisory. A model must not enter the per-Tick action path.
- Document any new process that can write configuration, restart services, or run with elevated privileges.
- Never include live API keys, model credentials, player identifiers, hostnames, IP addresses, or operational logs.

## Pull Requests

Keep each pull request scoped. Explain the observed problem, behavioral change, tests, and operational risk. Rule-dependent changes should cite the compatible game and SDK versions.

By contributing, you agree that your contribution is licensed under Apache-2.0.
