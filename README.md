# Arena Hero Aggressive Expansion Agent

[English](README.md) | [简体中文](README.zh-CN.md)

[![CI](https://github.com/WuDiWangWaSai/arena-hero-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/WuDiWangWaSai/arena-hero-agent/actions/workflows/ci.yml)
[![Release image](https://github.com/WuDiWangWaSai/arena-hero-agent/actions/workflows/release.yml/badge.svg)](https://github.com/WuDiWangWaSai/arena-hero-agent/actions/workflows/release.yml)
[![License](https://img.shields.io/github/license/WuDiWangWaSai/arena-hero-agent)](LICENSE)

A deterministic, long-running aggressive tactic for [Arena Hero](https://doc.arenahero.io/), maintained by [WuDiWangWaSai](https://github.com/WuDiWangWaSai). It uses the official Python SDK and includes a live tactical dashboard with replayable vision history and public leaderboards.

This is a community project, not an official Arena Hero product.

## Strategy

The default force grows through three production stages:

| Stage | Workers | Vanguards | Rangers | Population |
| --- | ---: | ---: | ---: | ---: |
| Establish | 6 | 2 | 2 | 10 |
| Mobilize | 12 | 6 | 8 | 26 |
| Overwhelm | 18 | 14 | 16 | 48 |

At 48 living Units, the Core capacity is 240 resources under `max(10, population * 5)`.

- Production continues while assaults are active and keeps a small Core repair reserve. Emergency defenders may spend that reserve.
- Visible hostile Cores are the primary offensive target. Escorts and remote interception do not automatically cancel the raid.
- Only one Vanguard and one Ranger remain as permanent Core guards; the rest attack, pursue visible enemies, or patrol an expanding perimeter.
- Once population reaches 40, resources reach 30, and the Core is healthy and not threatened, a non-guard Vanguard contests the Champion Beacon.
- The Core does not migrate for routine expansion or Beacon pressure. Workers clear the production cell; the Core moves only for verified survival threats.
- Arena Hero has no territory-ownership command. "Expansion" here means accumulated vision, outward patrols, enemy removal, and map control.

The tactic targets gameplay rules v0.14 and `arena-hero` SDK 0.2.9. See [strategy](docs/strategy.md), [threat response](docs/threat-response.md), and [configuration](docs/configuration.md).

## Tactical Dashboard

Every accepted Turn is stored in a bounded SQLite history. The dashboard provides:

- current and historical map state;
- explored cells, obstacles, resources, and remembered enemy Cores;
- friendly movement trails and submitted move lines;
- Tick playback, timeline navigation, pan, and zoom;
- event feed and public damage, Core-destruction, and Beacon leaderboards.

Start it after the Agent has begun writing `arena_history.sqlite3`:

```powershell
.\.venv\Scripts\python.exe -m arena_dashboard --history-db .\arena_history.sqlite3
```

Open [http://127.0.0.1:8765](http://127.0.0.1:8765). The leaderboard proxy uses the public Arena Hero endpoint and never sends the Agent API key.

## Requirements

- Python 3.11 or newer
- An Arena Hero API key
- PowerShell on Windows or a POSIX shell on Linux
- Docker or systemd only for those deployment paths

Runtime dependencies are hash-locked. Credentials and private runtime logs are ignored and must never be committed.

## Windows

Run these commands in PowerShell:

```powershell
git clone https://github.com/WuDiWangWaSai/arena-hero-agent.git
cd arena-hero-agent
.\scripts\bootstrap.ps1
.\start_agent.ps1
```

On first run, the script securely prompts for an API key if neither `.env` nor `ARENA_HERO_API_KEY` provides one. It writes `arena_farmer.log`, records `arena_history.sqlite3`, rotates logs, and retries transient failures.

From Command Prompt, use:

```bat
start_agent.cmd
```

Optional PowerShell overrides use a single dash:

```powershell
.\start_agent.ps1 -WorkerTarget 18 -BeaconPolicy pursue -HistoryDb .\arena_history.sqlite3
```

Stop the foreground Agent with `Ctrl+C`. Code changes require an Agent restart.

## Linux

```bash
git clone https://github.com/WuDiWangWaSai/arena-hero-agent.git
cd arena-hero-agent
cp .env.example .env
# Set ARENA_HERO_API_KEY locally. Never commit .env.
./scripts/bootstrap.sh
./scripts/run-agent.sh
```

Optional runtime overrides:

```bash
ARENA_WORKER_TARGET=18 ARENA_BEACON_POLICY=pursue ARENA_HISTORY_DB=./arena_history.sqlite3 ./scripts/run-agent.sh
```

## Docker Compose

Place the API key as the only line in `secrets/arena_hero_api_key.txt`, then run:

```bash
docker compose up -d --build
docker compose logs -f agent
```

Compose starts both the Agent and dashboard, persists history in a named volume, and publishes the dashboard only on `127.0.0.1:8765`. The containers use read-only root filesystems, drop Linux capabilities, and pass the API key only to the Agent through a Docker secret.

## systemd

Install the Agent and compatibility monitor on a supported Linux host:

```bash
sudo sh scripts/install-systemd.sh
sudo systemctl status arena-hero-agent.service --no-pager
sudo journalctl -fu arena-hero-agent.service -o short-iso-precise
```

History is written to `/var/lib/arena-hero-agent/history.sqlite3`. Production updates must use the transactional updater, then verify the service and deployed commit:

```bash
sh scripts/update-systemd.sh
sudo systemctl is-active arena-hero-agent.service
cat /opt/arena-hero-agent/current/source-commit
```

See [deployment](docs/deployment.md) for rollback, optional supervisor/optimizer, and uninstall procedures.

## Verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover
.\.venv\Scripts\python.exe -m compileall -q arena_farmer.py arena_history.py arena_dashboard.py arena_health.py arena_supervisor.py arena_optimizer.py arena_version_monitor.py
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe scripts\check_secrets.py
```

## License

Licensed under [Apache-2.0](LICENSE). Security reports should follow [SECURITY.md](SECURITY.md), and contributions should follow [CONTRIBUTING.md](CONTRIBUTING.md).
