# Configuration

## Main Agent

The Agent reads credentials in this order:

1. `ARENA_HERO_API_KEY` in the process environment.
2. The file passed to `--env-file`.
3. `.env` in the current directory.
4. A secure terminal prompt when interactive.

| CLI option | Default | Purpose |
| --- | --- | --- |
| `--base-url` | `https://api.arenahero.io` | Arena Hero HTTP API base. |
| `--env-file` | `.env` | Explicit credential file. |
| `--worker-target` | `18` | Worker goal; accepted range is 1-18. |
| `--beacon-policy` | `pursue` | `hold`, `pursue`, or `retreat`; `pursue` is the aggressive default. |
| `--compatibility-marker` | systemd marker path | Enter compatibility hold while the file exists. |
| `--no-compatibility-marker` | off | Disable marker checks for local/container runs. |
| `--heartbeat-file` | none | Write liveness metadata after each accepted Turn. |
| `--history-db` | `arena_history.sqlite3` | Bounded SQLite history used by the dashboard. |
| `--stale-turn-timeout-seconds` | `0` | Exit transiently after no accepted Turn; `0` disables. |

The default unattended force is `18 Workers + 14 Vanguards + 16 Rangers = 48`.
Core capacity therefore reaches 240 resources. Production is staged at
`6/2/2`, `12/6/8`, and `18/14/16`; the controller previews each dynamic unit
price with the official SDK and retains a ten-resource Core reserve except for
emergency defense.

The Core does not migrate for Beacon geometry or routine expansion. Workers
clear the production cell. Core migration remains available to the survival
controller when a threat is close enough to justify the slower four-Tick move.

## Dashboard

Start the read-only local dashboard with:

```powershell
.\.venv\Scripts\python.exe -m arena_dashboard --history-db .\arena_history.sqlite3
```

It listens on `127.0.0.1:8765` by default. `GET /api/ticks` lists captured Turns,
`GET /api/overview?tick=N` returns the selected historical map, and
`GET /api/leaderboard` proxies the public leaderboard without an API key.

## Runtime Files

| Deployment | History path | Runtime overrides |
| --- | --- | --- |
| Windows | `arena_history.sqlite3` | `-HistoryDb`, `ARENA_HISTORY_DB` |
| POSIX script | `$PROJECT_ROOT/arena_history.sqlite3` | `ARENA_HISTORY_DB` |
| Docker Compose | `/data/history.sqlite3` | `ARENA_DASHBOARD_PORT` for the UI |
| systemd | `/var/lib/arena-hero-agent/history.sqlite3` | `/etc/arena-hero-agent/runtime.env` |

Systemd runtime configuration contains only non-secret values:

```dotenv
ARENA_WORKER_TARGET=18
ARENA_BEACON_POLICY=pursue
ARENA_TUNING_GENERATION=0
```

The generation value is emitted in diagnostics so optimizer changes can be
correlated with logs. Credentials remain in protected environment files or
Docker secrets.

## Optimizer

`arena-hero-optimizer` is disabled by default. When explicitly enabled on a
systemd host, it only evaluates the allow-listed Worker targets `12`, `15`, and
`18`, with `pursue` locked as the Beacon policy. It can restart or roll back the
service, so do not enable it on a host where automatic restarts are not desired.

The supervisor and optional AI review remain deterministic observers; neither
has a plan-submission path.
