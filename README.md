# Arena Hero Unattended Agent

[English](README.md) | [简体中文](README.zh-CN.md)

[![CI](https://github.com/Drew-Z/arena-hero-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/Drew-Z/arena-hero-agent/actions/workflows/ci.yml)
[![Release image](https://github.com/Drew-Z/arena-hero-agent/actions/workflows/release.yml/badge.svg)](https://github.com/Drew-Z/arena-hero-agent/actions/workflows/release.yml)
[![License](https://img.shields.io/github/license/Drew-Z/arena-hero-agent)](LICENSE)

A deterministic, resource-first long-running agent for [Arena Hero](https://doc.arenahero.io/). It uses a hierarchical threat controller and the official `arena-hero` Python SDK, keeps decisions inside the 15-second Tick window, and can run locally, in Docker, or as hardened systemd services.

This is a community project and is not an official Arena Hero product.

## Highlights

- Builds toward `12 Workers + 4 Vanguards + 4 Rangers = 20`, using the complete base-price fleet before v0.14 dynamic spawn pricing starts at Unit 21.
- Moves the Core away from the Beacon, prioritizes collection and survival, and maintains distributed Core defense.
- Classifies lifecycle, threat, and Unit missions independently, including activity alerts, pre-evasion, engagement, multi-axis breakout, and detached-squad return.
- Scouts stale map regions, tracks resource memory, returns cargo, and recovers dropped cargo after losses.
- Avoids active enemy fleets while opportunistically clearing confirmed stationary threats or isolated Cores.
- Detects game/SDK compatibility changes before unattended play continues.
- Keeps AI out of the per-Tick control loop. Optional model review only analyzes anomaly reports after the fact.

```mermaid
flowchart LR
    Game["Arena Hero API"] -->|"authoritative Turn"| Agent["Deterministic Agent"]
    Agent -->|"one current-Tick plan"| Game
    Agent --> Assessment["Hierarchical threat assessment"]
    Assessment --> Agent
    Agent --> Logs["Structured logs"]
    Logs --> Supervisor["Optional deterministic supervisor"]
    Supervisor -. "explicit AI opt-in" .-> Model["Responses-compatible model channel"]
    Logs --> Optimizer["Optional root optimizer"]
    Version["Version monitor"] --> Marker["Compatibility hold"]
    Marker --> Agent
```

## Requirements

- Python 3.11 or newer
- An Arena Hero API key
- Docker Compose v2 for the container path
- A GNU/Linux server with systemd 235+ for the unattended server path; systemd
  247+ applies the complete unit hardening policy

The tested contract is API `v0.1`, gameplay `v0.14`, and official Python SDK `0.2.9`. Gameplay v0.14 removes per-Tick upkeep and maintenance damage. Unit 21 and later use dynamic spawn prices, so the Agent previews every purchase with the official SDK and reconciles the settled event cost. The bundled version monitor fails closed when it detects an incompatible contract.

## Quick Start

Clone the repository and enter its directory before choosing a deployment path:

```bash
git clone https://github.com/Drew-Z/arena-hero-agent.git
cd arena-hero-agent
```

### Windows

```powershell
.\scripts\bootstrap.ps1
.\start_agent.ps1
```

The first start securely prompts for the Arena Hero key and appends it to the ignored `.env` file. After bootstrapping, `start_agent.cmd` is also available for double-click use and keeps errors visible instead of closing immediately.

### Linux or macOS

```bash
sh scripts/bootstrap.sh
cp .env.example .env
chmod 600 .env
# Edit .env and set ARENA_HERO_API_KEY.
sh scripts/run-agent.sh
```

The POSIX bootstrap auto-detects versioned Python 3.11+ commands. On systems
whose `python3` is older, you can force one with
`PYTHON_BIN="$(command -v python3.11)" sh scripts/bootstrap.sh`.

### Docker Compose

```bash
mkdir -p secrets
cp secrets/arena_hero_api_key.example.txt secrets/arena_hero_api_key.txt
# Replace the placeholder in secrets/arena_hero_api_key.txt, then:
docker compose up -d --build
docker compose logs -f agent
```

Compose mounts the key as a Docker secret. The image runs as an unprivileged user with a read-only filesystem and does not include the supervisor or optimizer.

Use the published image without a local build:

```bash
ARENA_HERO_AGENT_IMAGE=ghcr.io/drew-z/arena-hero-agent:0.1.0 docker compose up -d --no-build
```

### Linux server with systemd

From a checked-out release on the server:

```bash
sudo sh scripts/install-systemd.sh
sudo journalctl -fu arena-hero-agent.service -o short-iso-precise
```

Ubuntu 22.04 uses Python 3.10 by default. Install a system-wide Python 3.11+
with its matching `venv` package and pass it with `--python`; see the
[Linux support matrix](docs/deployment.md#linux-systemd-server) for Debian,
Ubuntu, RHEL/Alma/Rocky, Fedora, Arch, and openSUSE guidance.

The installer prompts for the Arena Hero key without echoing it, builds an
immutable release under `/opt/arena-hero-agent/releases`, atomically updates the
`current` symlink, and enables the main Agent plus the six-hour compatibility
monitor. A failed compatibility, restart, or health check restores the prior
release. After a successful upgrade, use `sudo arena-hero-rollback` for an
immediate version swap.

After strategy code is published, update an existing running systemd instance
from its checkout with one command:

```bash
sh scripts/update-systemd.sh
```

Run the updater as the checkout owner, without `sudo`. It accepts only a clean
branch with a configured upstream, fetches and verifies a fast-forward update,
archives the exact target commit, then builds and validates the new release from
an isolated root-owned staging directory. systemd stops the old strategy process
during restart before it starts the new version, so two main Agent instances do
not run in parallel.
Existing credentials, runtime tuning, and enabled optional components are
preserved. The previous release remains active while the new release is being
prepared; the installer attempts to restore it if restart or health validation
fails and reports when recovery itself needs manual intervention.

Optional components are explicit:

```bash
# Deterministic, read-only anomaly reports; no model required.
sudo sh scripts/install-systemd.sh --with-supervisor

# Model review; first configure a private env file from the example.
sudo sh scripts/install-systemd.sh --with-ai /secure/path/supervisor.env

# High-privilege runtime tuning; read docs/deployment.md before enabling.
sudo sh scripts/install-systemd.sh --with-optimizer
```

## AI Monitoring Is Optional

The main Agent never needs a model. The supervisor always runs deterministic checks first. It calls a model only when all of these are true:

1. `ARENA_SUPERVISOR_AI_ENABLED=true` is explicitly set.
2. A deterministic anomaly trigger fires.
3. The base URL, API key, and at least one model ID are configured.

Model output is advisory and read-only. It cannot submit game plans, rewrite the tactic, or restart the Agent. See [configuration](docs/configuration.md) for provider settings.

The separate optimizer can update a narrow runtime configuration and restart the systemd service. It runs as root by design and is disabled by default.

## Configuration

Common Agent options:

```text
--worker-target 12
--beacon-policy retreat
--base-url https://api.arenahero.io
--compatibility-marker PATH
--no-compatibility-marker
```

See [configuration](docs/configuration.md), [deployment](docs/deployment.md), and [strategy](docs/strategy.md) for the complete operational contract.

Before the first public commit, follow the [release checklist](docs/release-checklist.md).

## Documentation and Community

- [LINUX DO](https://linux.do/) - an open-source community this project recognizes and supports
- [Documentation index](docs/README.md)
- [Strategy design](docs/strategy.md)
- [Threat response state machine](docs/threat-response.md)
- [Contributing](CONTRIBUTING.md)
- [Code of Conduct](CODE_OF_CONDUCT.md)
- [Security policy](SECURITY.md)

## Development

```bash
python -m pip install --require-hashes -r requirements-build.lock
python -m pip install --require-hashes -r requirements.lock
python -m pip install --no-deps --no-build-isolation -e .
python -m unittest discover -v
python -m compileall -q arena_farmer.py arena_health.py arena_supervisor.py arena_optimizer.py arena_version_monitor.py
python scripts/check_secrets.py
```

Tests use synthetic UUIDs and do not need an API key or a live game connection.
CI runs Python 3.11-3.13 on GitHub-hosted Ubuntu and Windows, and also validates
the container build and systemd units. This is not a claim that every Linux
distribution has received a real service installation test.

Regenerate the lock files with the exact `uv pip compile` commands recorded in
their headers, then review and test the resulting dependency diff before commit.

## Security

Never commit `.env`, model-provider files, Docker secret files, logs, or systemd credentials. If a key appears in chat, logs, an issue, or Git history, rotate it immediately; deleting the text is not sufficient.

Report vulnerabilities through the process in [SECURITY.md](SECURITY.md). For contribution rules, see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Licensed under the [Apache License 2.0](LICENSE), matching the official Arena Hero Python SDK.
