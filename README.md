# Arena Hero Unattended Agent

[English](README.md) | [简体中文](README.zh-CN.md)

[![CI](https://github.com/WuDiWangWaSai/arena-hero-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/WuDiWangWaSai/arena-hero-agent/actions/workflows/ci.yml)
[![Release image](https://github.com/WuDiWangWaSai/arena-hero-agent/actions/workflows/release.yml/badge.svg)](https://github.com/WuDiWangWaSai/arena-hero-agent/actions/workflows/release.yml)
[![License](https://img.shields.io/github/license/WuDiWangWaSai/arena-hero-agent)](LICENSE)

A deterministic, resource-first long-running tactic for [Arena Hero](https://doc.arenahero.io/), maintained by [WuDiWangWaSai](https://github.com/WuDiWangWaSai). It uses the official Python SDK and supports Windows, Linux, Docker, and hardened systemd deployment.

This is a community project, not an official Arena Hero product.

## Current Strategy

The default profile targets:

| Unit | Target | Purpose |
| --- | ---: | --- |
| Worker | 23 | Harvest, deposit, scouting, and cargo recovery |
| Vanguard | 3 | Outer Core defense and route screening |
| Ranger | 4 | Inner defense and ranged counterfire |
| Total | 30 | Raises Core resource capacity to 150 |

The tactic is aligned with gameplay rules v0.14 and `arena-hero` SDK 0.2.9:

- Builds the early economy and minimum defense first, reaches 12 Workers, completes the full `3 Vanguard + 4 Ranger` defense, then resumes Worker expansion.
- Funds dynamic-price growth in stages at populations 20, 24, 29, and 30 instead of spending every temporary surplus.
- Keeps a strict 30-Unit production cap. Core capacity is `max(10, population * 5)`, so the mature fleet stores 150 resources.
- Clears the Core production cell by moving its Worker out. Congested exits use a deterministic corridor handoff; a full Core keeps loaded Workers outside until storage is available.
- Treats every Turn as authoritative, re-evaluates dynamic resource cells, avoids duplicate harvest assignments, and recovers dropped Worker cargo.
- Separates lifecycle, threat, and mission decisions. Threat states include `NORMAL`, `ALERT`, `PRE_EVADE`, `ENGAGED`, and `BREAKOUT`.
- Keeps Core migration for verified survival threats; ordinary production-lane clearing moves Workers, not the Core.
- Uses target-free Ranger cell fire, post-combat healing, recovery after Core loss, compatibility hold, heartbeat health checks, and structured diagnostics.

See [strategy](docs/strategy.md), [threat response](docs/threat-response.md), and [configuration](docs/configuration.md) for the detailed policy.

## Requirements

- Python 3.11 or newer
- An Arena Hero API key
- PowerShell on Windows, or a POSIX shell on Linux
- Docker or systemd only when using those deployment paths

Dependencies are hash-locked. The runtime uses `arena-hero==0.2.9`.

## Windows

Clone and prepare the environment in PowerShell:

```powershell
git clone https://github.com/WuDiWangWaSai/arena-hero-agent.git
cd arena-hero-agent
.\scripts\bootstrap.ps1
.\start_agent.ps1
```

On the first run, `start_agent.ps1` securely prompts for the API key when neither `.env` nor `ARENA_HERO_API_KEY` provides one. It writes logs to `arena_farmer.log`, rotates them, and retries transient exit code 75 with backoff.

From Command Prompt, use the wrapper:

```bat
start_agent.cmd
```

PowerShell parameters use a single dash and PowerShell names:

```powershell
.\start_agent.ps1 -WorkerTarget 23 -BeaconPolicy retreat
```

Stop the foreground process with `Ctrl+C`.

## Linux

```bash
git clone https://github.com/WuDiWangWaSai/arena-hero-agent.git
cd arena-hero-agent
cp .env.example .env
# Edit .env locally and set ARENA_HERO_API_KEY. Never commit it.
./scripts/bootstrap.sh
./scripts/run-agent.sh
```

Runtime tuning can be supplied without editing code:

```bash
ARENA_WORKER_TARGET=23 ARENA_BEACON_POLICY=retreat ./scripts/run-agent.sh
```

## Docker Compose

Place the API key as the only line in `secrets/arena_hero_api_key.txt`, then run:

```bash
docker compose up -d --build
docker compose logs -f agent
```

To use a published release image:

```bash
ARENA_HERO_AGENT_IMAGE=ghcr.io/wudiwangwasai/arena-hero-agent:0.1.0 docker compose up -d --no-build
```

The container is read-only, drops Linux capabilities, uses a Docker secret, and reports health from accepted Turns.

## systemd

Install the main Agent and version monitor on a supported Linux host:

```bash
sudo sh scripts/install-systemd.sh
sudo systemctl status arena-hero-agent.service --no-pager
sudo journalctl -fu arena-hero-agent.service -o short-iso-precise
```

Optional components are explicit:

```bash
sudo sh scripts/install-systemd.sh --with-supervisor
sudo sh scripts/install-systemd.sh --with-optimizer
```

The supervisor performs deterministic health review. The optimizer only tests allow-listed Worker targets and can restart or roll back the service; it is disabled by default.

Production updates must use the transactional updater:

```bash
sh scripts/update-systemd.sh
sudo systemctl is-active arena-hero-agent.service
cat /opt/arena-hero-agent/current/source-commit
```

See [deployment](docs/deployment.md) for supported distributions, rollback, optional AI review, and uninstall procedures.

## Tests and Safety

```powershell
.\.venv\Scripts\python.exe -m unittest discover
.\.venv\Scripts\python.exe -m compileall -q arena_farmer.py arena_health.py arena_supervisor.py arena_optimizer.py arena_version_monitor.py
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts\check_secrets.py
```

Credentials belong only in environment variables, `.env`, Docker secrets, or protected systemd configuration. Do not commit API keys, player identifiers, private logs, or model credentials.

## License

Licensed under [Apache-2.0](LICENSE). Security reports should follow [SECURITY.md](SECURITY.md), and contributions should follow [CONTRIBUTING.md](CONTRIBUTING.md).
