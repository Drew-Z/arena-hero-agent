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

Useful overrides (the aggressive default is `18 Worker` and `pursue`):

```powershell
.\start_agent.ps1 -WorkerTarget 18 -BeaconPolicy pursue -HistoryDb .\arena_history.sqlite3 -NoCompatibilityMarker
```

Stop with `Ctrl+C`. When using `start_agent.cmd`, an initialization failure pauses the terminal so the error remains visible.

## Local Linux or macOS

```bash
sh scripts/bootstrap.sh
cp .env.example .env
chmod 600 .env
sh scripts/run-agent.sh
```

The bootstrap script checks `python3` and then versioned commands from
`python3.13` through `python3.11`. To force a specific installation, set an
explicit path; an incompatible explicit interpreter fails instead of silently
falling back:

```bash
PYTHON_BIN="$(command -v python3.11)" sh scripts/bootstrap.sh
```

Set `ARENA_WORKER_TARGET`, `ARENA_BEACON_POLICY`, `ARENA_HISTORY_DB`, or
`ARENA_HERO_ENV_FILE` to override defaults. Additional CLI arguments are passed
through:

```bash
ARENA_WORKER_TARGET=18 ARENA_BEACON_POLICY=pursue sh scripts/run-agent.sh --base-url https://api.arenahero.io
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

Compose also enables a 150-second accepted-Turn deadline. If the SDK is still
reconnecting but no plan has been accepted, the Agent exits with its transient
failure code and Docker's `unless-stopped` policy rebuilds the process. A stale
health status alone does not restart a Docker container, so the deadline is part
of the unattended recovery path.

Compose also starts a separate `dashboard` service on `127.0.0.1:8765`. It
mounts the Agent's `/data/history.sqlite3` volume read-only and provides current
state, historical vision replay, events, and public leaderboards.

Change the strategy without editing Compose:

```bash
ARENA_WORKER_TARGET=18 ARENA_BEACON_POLICY=pursue docker compose up -d
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
ARENA_HERO_AGENT_IMAGE=ghcr.io/wudiwangwasai/arena-hero-agent:0.1.0 docker compose pull
ARENA_HERO_AGENT_IMAGE=ghcr.io/wudiwangwasai/arena-hero-agent:0.1.0 docker compose up -d --no-build
```

## Linux systemd Server

The installer targets GNU/Linux systemd distributions. It requires root,
systemd 235+, Python 3.11+ with `venv` and pip, Git and tar for one-command
updates, `flock`, GNU coreutils, util-linux, shadow account tools, and network
access to install hash-locked Python dependencies.

The installer reads `systemctl --version` before changing the host. Versions
below 235 are rejected because required service state-directory behavior is not
available. systemd 235-246 can run the services, but the installer warns that
some process and kernel isolation directives are unavailable. systemd 247+ is
the full-hardening baseline.

| Distribution family | Installation status |
| --- | --- |
| Debian 12, Ubuntu 24.04 | Direct path when the distribution Python includes `venv` and pip. |
| Ubuntu 22.04, Debian 11 | systemd is sufficient; install a parallel system-wide Python 3.11+ and select it with `--python`. |
| RHEL, AlmaLinux, Rocky Linux 9 | systemd is sufficient; the default Python may be older than 3.11, so install and explicitly select Python 3.11+. |
| Current Fedora, Arch Linux, openSUSE Tumbleweed | Expected to work with current system packages; rolling Python upgrades should be tested before production updates. |
| openSUSE Leap 15.x | Expected compatibility after installing and selecting the parallel `python311` packages. |
| RHEL-family 8 and other systemd 235-246 hosts | Compatibility mode only: Python 3.11+ is still required and some hardening directives are unavailable. |
| RHEL/CentOS 7 and other systemd <235 hosts | Unsupported by the systemd deployment path; use Docker on a supported container host or upgrade the OS. |

The transaction tests run on GitHub-hosted Ubuntu with redirected system paths
and simulated `systemctl`; CI also performs static unit verification. The table
is a conservative compatibility policy, not a claim that every listed release
has completed a real booted-systemd installation test.
The documented Debian 12 and AlmaLinux 9 dependency sets are additionally
smoke-tested in clean containers for package resolution, Python `venv`, systemd
version, and shell parsing; those containers do not boot or start systemd.

On Debian or Ubuntu whose default Python is already 3.11 or newer, install the
common prerequisites with:

```bash
sudo apt-get update
sudo apt-get install -y git sudo tar python3 python3-venv ca-certificates systemd util-linux coreutils passwd
```

Ubuntu 22.04 ships Python 3.10 as `python3`. Provision a system-wide Python
3.11+ and its matching `venv` package from an approved package source, then
select it explicitly. For example, if `python3.11` is available from that
source:

```bash
sudo apt-get install -y git sudo tar python3.11 python3.11-venv ca-certificates systemd util-linux coreutils passwd
sudo sh scripts/install-systemd.sh --python "$(command -v python3.11)"
```

Without `--python`, the installer checks `python3`, `python3.13`, `python3.12`,
and `python3.11` and selects the first compatible interpreter. A user-local
Python must be passed by absolute path and remain executable by the installed
service accounts; a system-wide interpreter is preferred for systemd.

On RHEL/Alma/Rocky 9, enable an approved repository that provides Python 3.11+
when necessary, then use the matching package names for that minor release. A
typical AppStream-based installation is:

```bash
sudo dnf install -y git sudo tar ca-certificates systemd util-linux shadow-utils python3.11 python3.11-pip
sudo sh scripts/install-systemd.sh --python "$(command -v python3.11)"
```

Typical package sets for other current distributions are:

```bash
# Fedora
sudo dnf install -y git sudo tar ca-certificates systemd util-linux coreutils shadow-utils python3 python3-pip

# Arch Linux
sudo pacman -S --needed git sudo tar ca-certificates systemd util-linux coreutils shadow python

# openSUSE (package names can vary by release)
sudo zypper install git sudo tar ca-certificates systemd util-linux coreutils shadow python311 python311-pip
```

Verify `python -m venv` works after using the distribution package manager. The
installer performs command, systemd version, Python version, and `venv` preflight
before creating service users or writing configuration. It never installs OS
packages or changes repositories automatically. It creates explicit same-name
groups before service users, avoiding distribution-specific `useradd` defaults.

### Default installation

```bash
sudo sh scripts/install-systemd.sh
```

This creates dedicated service users, stores the Arena Hero credential as
`/etc/arena-hero-agent.env` with restricted permissions, and uses this release
layout:

```text
/opt/arena-hero-agent/
├── releases/<version-timestamp-instance>/.venv
├── current -> releases/<active-release>
└── previous -> releases/<rollback-release>
```

Every install creates a fresh inactive release at its final versioned path,
installs both hash-locked dependency sets, and runs `pip check` plus all CLI
self-checks. Building the virtual environment at its final path preserves the
absolute interpreter paths embedded in its console scripts. A pre-activation
failure removes the incomplete release. Only after all checks succeed does an
atomic symlink replacement activate `current`. The installer then enables:

- `arena-hero-agent.service`
- `arena-hero-version-monitor.timer`

The installer runs the compatibility check once before starting the Agent. A
failed or changed contract, failed service restart, or failed post-restart
health probe restores the old `current`/`previous` links, prior unit
enablement/running state, and the old release. Link changes are journaled: a
handled interruption restores them immediately, while the next install or
rollback repairs a transaction left by an uncatchable process or host failure.
The failed immutable release is retained for diagnosis.

The main unit uses a 90-second accepted-Turn deadline with systemd's 120-second
watchdog as a second layer. Transient exit code 75 always restarts, while
authentication, policy, protocol, configuration, and terminal Agent failures do
not. Start-rate limiting is disabled for this unit so a prolonged network outage
cannot leave it permanently stopped after a fixed retry burst. Core dumps are
disabled for the service so a watchdog abort cannot persist the in-memory game
credential.

The installer preserves an existing game credential and runtime tuning file
during upgrades. It also preserves a pre-versioned `/opt/arena-hero-agent/.venv`
and exposes it through `previous` during the first migration. After a successful
compatibility check, it explicitly restarts the main Agent so an already-running
installation cannot continue using old in-memory code.

`--no-start` still installs the units and activates the new release, but it does
not enable, start, restart, or health-check services. Use it only when another
provisioning step owns service activation.

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
sudo /opt/arena-hero-agent/current/.venv/bin/arena-hero-health --require-supervisor
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

For a checkout that follows a reviewed upstream branch, switch a running game
instance to the latest published strategy with:

```bash
cd /path/to/arena-hero-agent
sh scripts/update-systemd.sh
```

Run the command as the checkout owner, without `sudo`. The updater refuses a
dirty worktree, detached HEAD, missing upstream, unsupported remote name, or
non-fast-forward history. Git authentication and the fast-forward merge happen
before privilege escalation. The updater does not accept installer or
credential arguments and clears `ARENA_HERO_API_KEY` for the install step, so an
unrelated shell variable cannot replace the protected systemd credential.

After Git validation, the updater archives the exact remote-tracking commit and
extracts it into a root-owned temporary directory before invoking the
transactional installer with no configuration overrides. The privileged build
therefore cannot silently consume a later edit or a second updater's checkout.
Existing `/etc` credentials, runtime tuning, private AI configuration, and
enabled optional components are retained. Running the command while the checkout
is already current still redeploys that commit, which is useful when the source
checkout is newer than the active instance.

The updater records the full object ID in the immutable release. Verify it with:

```bash
cat /opt/arena-hero-agent/current/source-commit
```

The old strategy keeps running while the installer builds and validates the new
immutable release. Activation updates `current` and restarts the single
`arena-hero-agent.service`; systemd completes the stop of the old strategy
process before starting the new process from `current`, so two main Agent
instances do not run in parallel. A post-restart health check confirms the new
strategy is running before the update reports success.

Use the full installer directly when intentionally changing components,
credentials, Python selection, or deploying a reviewed detached release. Do not
run `sudo git pull`; keep Git operations under the checkout owner and elevate
only `scripts/install-systemd.sh`.

Installer, updater, and rollback operations ultimately share the installer's
non-blocking deployment lock; a concurrent deployment exits without changing
release links. The installer and rollback command recover a pending release-link
transaction before reading `current` or `previous`. A failed update leaves the
old release active or restores it automatically when recovery succeeds, while
preserving the original installer exit code. The updater deliberately makes no
absolute recovery claim after a failed install: read the installer diagnostics
and inspect the service and release links, because recovery itself can fail in
an exceptional host or filesystem failure. Runtime recovery does not move the
source checkout back to its earlier commit; after fixing the host problem,
running the updater again redeploys the already checked-out target commit.

Before updating, record the installed version and back up the restricted configuration:

```bash
readlink -f /opt/arena-hero-agent/current
readlink -f /opt/arena-hero-agent/previous || true
/opt/arena-hero-agent/current/.venv/bin/python -m pip show arena-hero-agent
sudo install -d -m 0700 /root/arena-hero-agent-backup
sudo cp -a /etc/arena-hero-agent.env /etc/arena-hero-agent /root/arena-hero-agent-backup/
```

After a successful upgrade, roll back without rebuilding or contacting a
package index:

```bash
sudo arena-hero-rollback
sudo /opt/arena-hero-agent/current/.venv/bin/arena-hero-health
```

The command validates that both links resolve inside `releases/`, atomically
switches `current`, runs compatibility and health checks, and makes the replaced
release the new `previous`. Running it again switches forward. If the selected
release fails validation, compatibility, restart, or health checks, the original
link pair and service are restored. A first installation has no rollback target
unless a legacy `.venv` was migrated.

Application rollback swaps immutable releases but does not install historical
systemd unit templates or change the host's Python packages. Unit definitions
are intentionally stable and point through `current`; a release that changes the
host-level service contract requires a reviewed installer change in both upgrade
and downgrade directions.

Releases are not pruned automatically. Keep at least the `current` and
`previous` targets; delete any older unreferenced release only after resolving
its absolute path and verifying that neither symlink selects it.

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
sudo rm -f /usr/local/sbin/arena-hero-rollback
sudo rm -f /var/lib/systemd/timers/stamp-arena-hero-version-monitor.timer
sudo rm -f /var/lib/systemd/timers/stamp-arena-hero-supervisor.timer /var/lib/systemd/timers/stamp-arena-hero-optimizer.timer
sudo rm -rf /opt/arena-hero-agent /etc/arena-hero-agent
sudo rm -rf /var/lib/arena-hero-version /var/lib/arena-hero-supervisor /var/lib/arena-hero-optimizer
sudo rm -f /etc/arena-hero-agent.env /etc/arena-hero-supervisor.env
sudo systemctl daemon-reload
sudo systemctl reset-failed
```

Complete removal permanently deletes credentials and local reports. Service
accounts are intentionally retained because deleting users is host-policy
specific. To remove them too, first verify that the dedicated accounts own no
files outside the paths above. Then remove only these users and their same-name
groups; never remove the shared `systemd-journal` group:

```bash
sudo userdel arena-hero
sudo userdel arena-hero-version
sudo userdel arena-hero-supervisor
sudo groupdel arena-hero 2>/dev/null || true
sudo groupdel arena-hero-version 2>/dev/null || true
sudo groupdel arena-hero-supervisor 2>/dev/null || true
```

Source checkouts, deployment directories under `/tmp`, and operator-created
backups are not owned by the installer. Inspect and resolve each exact path
before deleting those separately; do not remove a broad parent directory or an
unresolved wildcard.

## Credential Hygiene

- Keep Arena Hero and model keys in separate files.
- Never place either key in Compose YAML, systemd units, screenshots, issues, or logs.
- Keep secret files mode `0600` before installation.
- Rotate a key immediately if it has appeared outside its intended secret store.
- Run `python scripts/check_secrets.py` before publishing.
