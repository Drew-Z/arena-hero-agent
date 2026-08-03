# Deployment Guide

## Deployment Matrix

| Mode | Recommended for | Restarts | Compatibility monitor | Supervisor | Root optimizer |
| --- | --- | --- | --- | --- | --- |
| Windows script | Interactive desktop use | transient failures | no | no | no |
| POSIX script | Interactive local use | transient failures | no | no | no |
| Docker Compose | Simple isolated 24/7 Agent | yes | no | no | no |
| Linux systemd | Full unattended server | yes | yes | optional | optional |

The deterministic Agent is the same in every mode. Docker intentionally excludes host-journal supervision and runtime optimization because those components depend on systemd and journald.

## Local Windows

Install Python 3.11 or newer, then run from PowerShell:

```powershell
.\scripts\bootstrap.ps1
.\start_agent.ps1
```

Useful overrides:

```powershell
.\start_agent.ps1 -WorkerTarget 12 -BeaconPolicy retreat -NoCompatibilityMarker
```

Stop with `Ctrl+C`. When using `start_agent.cmd`, an initialization failure pauses the terminal so the error remains visible.

## Local Linux or macOS

```bash
sh scripts/bootstrap.sh
cp .env.example .env
chmod 600 .env
sh scripts/run-agent.sh
```

Set `ARENA_WORKER_TARGET`, `ARENA_BEACON_POLICY`, or `ARENA_HERO_ENV_FILE` to override defaults. Additional CLI arguments are passed through:

```bash
ARENA_WORKER_TARGET=10 sh scripts/run-agent.sh --base-url https://api.arenahero.io
```

Stop with `Ctrl+C`.

## Docker Compose

Create the ignored runtime secret:

```bash
mkdir -p secrets
cp secrets/arena_hero_api_key.example.txt secrets/arena_hero_api_key.txt
chmod 600 secrets/arena_hero_api_key.txt
```

Replace the placeholder with the key, then:

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f agent
```

Compose reports the container healthy only after the Agent has submitted a recent
Turn. Inspect the exact heartbeat check with `docker inspect` or run it directly:

```bash
docker compose exec agent arena-hero-health --heartbeat-only --heartbeat-file /tmp/arena-hero-heartbeat.json
```

Change the strategy without editing Compose:

```bash
ARENA_WORKER_TARGET=10 ARENA_BEACON_POLICY=hold docker compose up -d
```

Stop and retain the image:

```bash
docker compose stop
```

Remove the Compose container and network:

```bash
docker compose down
```

The secret file stays on the host and is never copied into the image. Docker Compose mounts it under `/run/secrets` for the entrypoint to read.

Tagged releases publish a prebuilt image to GHCR. To deploy it without building:

```bash
ARENA_HERO_AGENT_IMAGE=ghcr.io/drew-z/arena-hero-agent:0.1.0 docker compose pull
ARENA_HERO_AGENT_IMAGE=ghcr.io/drew-z/arena-hero-agent:0.1.0 docker compose up -d --no-build
```

## Linux systemd Server

The installer targets standard systemd distributions and requires root,
systemd, Python 3.11+, Python `venv` support, standard account/core utilities,
and network access to install Python dependencies. On Debian or Ubuntu, install
the common prerequisites with:

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-venv ca-certificates
```

On RHEL-family systems, install the equivalent Python, pip/venv, systemd, and
shadow-utils packages. The installer performs its command and `venv` preflight
before creating service users or writing configuration.

### Default installation

```bash
sudo sh scripts/install-systemd.sh
```

This creates dedicated service users, installs the project into `/opt/arena-hero-agent/.venv`, stores the Arena Hero credential as `/etc/arena-hero-agent.env` with restricted permissions, and enables:

- `arena-hero-agent.service`
- `arena-hero-version-monitor.timer`

The installer runs the compatibility check once before starting the Agent. A
failed or changed contract stops the installation before unattended play begins.

The installer preserves an existing game credential and runtime tuning file
during upgrades. After a successful compatibility check, it explicitly
restarts the main Agent so an already-running installation cannot continue
using old in-memory code.

For non-interactive provisioning, put only the key on the first line of a protected file:

```bash
sudo sh scripts/install-systemd.sh --api-key-file /secure/path/arena-key.txt
```

### Deterministic supervisor

```bash
sudo sh scripts/install-systemd.sh --with-supervisor
```

This enables `arena-hero-supervisor.timer`. It reads the current Agent invocation from journald, writes reports under `/var/lib/arena-hero-supervisor`, and does not require any model credential.

### Optional AI review

Prepare a private file based on `deploy/arena-hero-supervisor.env.example`, set `ARENA_SUPERVISOR_AI_ENABLED=true`, and fill in a Responses-compatible endpoint, key, and model list. Then:

```bash
sudo chmod 600 /secure/path/supervisor.env
sudo sh scripts/install-systemd.sh --with-ai /secure/path/supervisor.env
```

Anomaly triggers are deterministic. AI is skipped for healthy windows and cannot control game actions.

### Optional optimizer

```bash
sudo sh scripts/install-systemd.sh --with-optimizer
```

The optimizer runs as root, writes `/etc/arena-hero-agent/runtime.env`, and can restart the Agent. Enable it only after reviewing `arena_optimizer.py`, the service hardening, and the rollback behavior. It is not required for long-running collection.

### Disable optional components

Re-running the installer preserves optional component state unless an explicit
enable or disable flag is supplied. Use these commands to converge an existing
installation safely:

```bash
# Stop and disable supervisor runs, preserving its private AI config.
sudo sh scripts/install-systemd.sh --without-supervisor

# Remove the installed private AI config. A still-enabled supervisor becomes
# deterministic-only on its next run.
sudo sh scripts/install-systemd.sh --without-ai

# Stop and disable the root optimizer.
sudo sh scripts/install-systemd.sh --without-optimizer
```

`--with-*` and the corresponding `--without-*` option cannot be used together.

### Operations

```bash
sudo systemctl status arena-hero-agent.service --no-pager
sudo journalctl -fu arena-hero-agent.service -o short-iso-precise
sudo systemctl list-timers 'arena-hero-*'
sudo /opt/arena-hero-agent/.venv/bin/arena-hero-health --require-supervisor
```

Omit `--require-supervisor` when the optional supervisor timer was not installed.
The command exits nonzero when the service is down, the accepted-Turn heartbeat is
stale, version compatibility is on hold, or a required report is stale/critical.

Stop only the game Agent:

```bash
sudo systemctl stop arena-hero-agent.service
```

Disable all unattended activity:

```bash
sudo systemctl disable --now arena-hero-agent.service
sudo systemctl disable --now arena-hero-version-monitor.timer
sudo systemctl disable --now arena-hero-supervisor.timer
sudo systemctl disable --now arena-hero-optimizer.timer
```

The supervisor and optimizer are oneshot services; stop an active run with `systemctl stop arena-hero-supervisor.service` or `systemctl stop arena-hero-optimizer.service`.

### Updating and rollback

Check out a reviewed release and rerun the installer. Existing `/etc` credentials and runtime tuning are retained unless an explicit replacement file is provided.

Before updating, record the installed version and back up the restricted configuration:

```bash
/opt/arena-hero-agent/.venv/bin/python -m pip show arena-hero-agent
sudo install -d -m 0700 /root/arena-hero-agent-backup
sudo cp -a /etc/arena-hero-agent.env /etc/arena-hero-agent /root/arena-hero-agent-backup/
```

To roll back, check out the previous release and rerun
`sudo sh scripts/install-systemd.sh`. The installer updates the existing virtual
environment from the selected source tree and restarts the Agent after the
compatibility check. This is a source rollback, not an atomic environment
snapshot: retain the previous release checkout or wheel and verify health after
every update.

### Uninstall

To stop all activity while retaining credentials, runtime tuning, and reports:

```bash
sudo systemctl disable --now arena-hero-agent.service
sudo systemctl disable --now arena-hero-version-monitor.timer
sudo systemctl disable --now arena-hero-supervisor.timer
sudo systemctl disable --now arena-hero-optimizer.timer
sudo systemctl stop arena-hero-version-monitor.service arena-hero-supervisor.service arena-hero-optimizer.service
```

For a complete removal, first run the commands above, then remove only the
project-owned units, installation, configuration, and state:

```bash
sudo rm -f /etc/systemd/system/arena-hero-agent.service
sudo rm -f /etc/systemd/system/arena-hero-version-monitor.service /etc/systemd/system/arena-hero-version-monitor.timer
sudo rm -f /etc/systemd/system/arena-hero-supervisor.service /etc/systemd/system/arena-hero-supervisor.timer
sudo rm -f /etc/systemd/system/arena-hero-optimizer.service /etc/systemd/system/arena-hero-optimizer.timer
sudo rm -rf /opt/arena-hero-agent /etc/arena-hero-agent /var/lib/arena-hero-supervisor /var/lib/arena-hero-optimizer
sudo rm -f /etc/arena-hero-agent.env /etc/arena-hero-supervisor.env
sudo systemctl daemon-reload
sudo systemctl reset-failed
```

Complete removal permanently deletes credentials and local reports. Service
accounts are intentionally retained because deleting users is host-policy
specific.

## Credential Hygiene

- Keep Arena Hero and model keys in separate files.
- Never place either key in Compose YAML, systemd units, screenshots, issues, or logs.
- Keep secret files mode `0600` before installation.
- Rotate a key immediately if it has appeared outside its intended secret store.
- Run `python scripts/check_secrets.py` before publishing.
