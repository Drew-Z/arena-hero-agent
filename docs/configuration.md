# Configuration

## Main Agent

The main Agent requires only an Arena Hero API key. Configuration precedence is:

1. `ARENA_HERO_API_KEY` in the process environment.
2. The file passed to `--env-file`.
3. `.env` in the current directory.
4. A secure terminal prompt when interactive.

| CLI option | Default | Purpose |
| --- | --- | --- |
| `--base-url` | `https://api.arenahero.io` | Arena Hero HTTP API base. |
| `--env-file` | current `.env` | Explicit credential file. |
| `--worker-target` | `23` | Worker goal; accepted range is 1-23. |
| `--beacon-policy` | `retreat` | `hold`, `pursue`, or `retreat`. |
| `--compatibility-marker` | systemd marker path | Enter compatibility hold while the file exists. |
| `--no-compatibility-marker` | off | Disable marker checks for local/container runs. |
| `--heartbeat-file` | none | Write liveness metadata after each accepted Turn. |
| `--stale-turn-timeout-seconds` | `0` | Exit with a transient failure after no accepted Turn; `0` disables. |

The supported unattended profile is `--worker-target 23 --beacon-policy retreat`, yielding `23 Workers + 3 Vanguards + 4 Rangers = 30`. At 30 living Units the Core stores up to 150 resources. Production expands in tested stages and reserves resources before crossing dynamic-price thresholds.

Docker Compose enables the stale-Turn timeout at 150 seconds. The systemd unit
uses 90 seconds, below its 120-second systemd watchdog, so a reconnect loop that
produces no accepted Turns is rebuilt instead of remaining unhealthy forever.

## Manual UI Interoperability

Avoid controlling the same fleet from the web UI and this Agent at the same
time. Plans are source-scoped and a manual plan can replace actions the Agent
expected to own; the Agent logs manual receipt counts but intentionally does not
print plan contents.

Core migration takes four Ticks and requires an explicit `CANCEL_MOVE` action to
reset progress. If the Arena Hero web UI cannot express that action, or its
pending-command list shows only opaque identifiers, that is an upstream UI
limitation rather than an Agent protocol feature. Object UUIDs in Turn state and
receipts are authoritative when filing an upstream report.

## systemd Runtime Tuning

`/etc/arena-hero-agent/runtime.env` contains non-secret values:

```dotenv
ARENA_WORKER_TARGET=23
ARENA_BEACON_POLICY=retreat
ARENA_TUNING_GENERATION=0
```

The main service reads the first two as CLI arguments. The generation value is emitted in diagnostics so optimizer changes can be correlated with logs.

## Deterministic Supervisor

The supervisor reads the current systemd invocation's journal and writes structured reports under `/var/lib/arena-hero-supervisor`. It has no game credential and no plan-submission path.

It can run with no model configuration. Enable its timer with:

```bash
sudo sh scripts/install-systemd.sh --with-supervisor
```

## Optional AI Review

Copy `deploy/arena-hero-supervisor.env.example` to a private path outside the repository, set mode `0600`, and configure:

| Variable | Required when enabled | Meaning |
| --- | --- | --- |
| `ARENA_SUPERVISOR_AI_ENABLED` | yes | Must be `true` for any model request. |
| `ARENA_SUPERVISOR_AI_BASE_URL` | yes | Base URL for an OpenAI Responses-compatible endpoint. |
| `ARENA_SUPERVISOR_AI_API_KEY` | yes | Model-channel credential. |
| `ARENA_SUPERVISOR_MODELS` | yes | Comma-separated model IDs tried in order. |

Legacy `AI_BASE_URL` and `AI_API_KEY` names remain readable for existing installations, but scoped names are preferred.

AI requests occur only after a deterministic trigger. Model output is validated structured JSON and is merged conservatively with deterministic severity. The supervisor never edits runtime configuration or controls game objects.

Install the private configuration with:

```bash
sudo sh scripts/install-systemd.sh --with-ai /secure/path/supervisor.env
```

Remove the installed model credential and return an enabled supervisor to
deterministic-only reports with:

```bash
sudo sh scripts/install-systemd.sh --without-ai
```

To validate a private model channel, run one synthetic review task per candidate model. This is an actual Responses request, not a short liveness ping:

```bash
python scripts/probe_model_channel.py \
  --env-file /secure/path/supervisor.env \
  --model model-a \
  --model model-b
```

## Optimizer

`arena-hero-optimizer` evaluates six-hour journal windows and can atomically adjust `/etc/arena-hero-agent/runtime.env`, restart the main service, and roll back a rejected candidate. Because that requires host control, its systemd service runs as root.

It is disabled by default. Do not enable it in containers or on hosts where a game Agent must not have an automated restart path.

Disable an existing optimizer timer with
`sudo sh scripts/install-systemd.sh --without-optimizer`. See the deployment
guide for supervisor disable and complete uninstall commands.
