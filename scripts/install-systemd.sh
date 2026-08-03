#!/bin/sh
set -eu

INSTALL_ROOT=/opt/arena-hero-agent
AGENT_ENV=/etc/arena-hero-agent.env
RUNTIME_DIR=/etc/arena-hero-agent
RUNTIME_ENV=$RUNTIME_DIR/runtime.env
SUPERVISOR_ENV=/etc/arena-hero-supervisor.env
PYTHON_BIN=${PYTHON_BIN:-python3}
WITH_SUPERVISOR=0
WITH_OPTIMIZER=0
DISABLE_SUPERVISOR=0
DISABLE_AI=0
DISABLE_OPTIMIZER=0
START_SERVICES=1
API_KEY_FILE=
AI_ENV_FILE=

usage() {
    cat <<'EOF'
Usage: sudo ./scripts/install-systemd.sh [options]

Options:
  --api-key-file PATH    Read the Arena Hero API key from the first line.
  --with-supervisor     Enable deterministic six-hour supervisor reports.
  --with-ai ENV_FILE    Enable supervisor reports and install explicit AI config.
  --with-optimizer      Enable the root optimizer timer (advanced, high privilege).
  --without-supervisor  Disable the supervisor timer; preserve its private config.
  --without-ai          Remove the private AI config; keep deterministic reports.
  --without-optimizer   Disable the optimizer timer and stop an active run.
  --no-start            Install files without enabling or starting services.
  --python PATH         Python 3.11+ interpreter (default: python3).
  -h, --help            Show this help.
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --api-key-file)
            [ "$#" -ge 2 ] || { echo "--api-key-file requires a path" >&2; exit 2; }
            API_KEY_FILE=$2
            shift 2
            ;;
        --with-supervisor)
            WITH_SUPERVISOR=1
            shift
            ;;
        --with-ai)
            [ "$#" -ge 2 ] || { echo "--with-ai requires an env file" >&2; exit 2; }
            WITH_SUPERVISOR=1
            AI_ENV_FILE=$2
            shift 2
            ;;
        --with-optimizer)
            WITH_OPTIMIZER=1
            shift
            ;;
        --without-supervisor)
            DISABLE_SUPERVISOR=1
            shift
            ;;
        --without-ai)
            DISABLE_AI=1
            shift
            ;;
        --without-optimizer)
            DISABLE_OPTIMIZER=1
            shift
            ;;
        --no-start)
            START_SERVICES=0
            shift
            ;;
        --python)
            [ "$#" -ge 2 ] || { echo "--python requires a path" >&2; exit 2; }
            PYTHON_BIN=$2
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [ "$WITH_SUPERVISOR" -eq 1 ] && [ "$DISABLE_SUPERVISOR" -eq 1 ]; then
    echo "--with-supervisor/--with-ai cannot be combined with --without-supervisor." >&2
    exit 2
fi
if [ -n "$AI_ENV_FILE" ] && [ "$DISABLE_AI" -eq 1 ]; then
    echo "--with-ai cannot be combined with --without-ai." >&2
    exit 2
fi
if [ "$WITH_OPTIMIZER" -eq 1 ] && [ "$DISABLE_OPTIMIZER" -eq 1 ]; then
    echo "--with-optimizer cannot be combined with --without-optimizer." >&2
    exit 2
fi

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Required command is unavailable: $1" >&2
        exit 2
    fi
}

for command_name in cat chmod chown grep id install mktemp mv rm sed systemctl tr useradd; do
    require_command "$command_name"
done
if [ "$WITH_SUPERVISOR" -eq 1 ]; then
    require_command getent
    require_command usermod
fi
require_command "$PYTHON_BIN"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer as root (for example, with sudo)." >&2
    exit 2
fi

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(dirname "$SCRIPT_DIR")

"$PYTHON_BIN" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' || {
    echo "Python 3.11 or newer is required." >&2
    exit 2
}
"$PYTHON_BIN" -c 'import tempfile, venv; root = tempfile.TemporaryDirectory(); venv.EnvBuilder(with_pip=True).create(root.name)' || {
    echo "Python venv with pip is required (for example, install python3-venv on Debian/Ubuntu)." >&2
    exit 2
}

if [ -n "$API_KEY_FILE" ] && [ ! -r "$API_KEY_FILE" ]; then
    echo "API key file is not readable: $API_KEY_FILE" >&2
    exit 2
fi
if [ -n "$AI_ENV_FILE" ] && [ ! -r "$AI_ENV_FILE" ]; then
    echo "AI environment file is not readable: $AI_ENV_FILE" >&2
    exit 2
fi

ensure_user() {
    if ! id "$1" >/dev/null 2>&1; then
        useradd --system --no-create-home --home-dir /nonexistent \
            --shell /usr/sbin/nologin "$1"
    fi
}

ensure_user arena-hero
ensure_user arena-hero-version
if [ "$WITH_SUPERVISOR" -eq 1 ]; then
    ensure_user arena-hero-supervisor
    if getent group systemd-journal >/dev/null 2>&1; then
        usermod -a -G systemd-journal arena-hero-supervisor
    fi
fi

install -d -o root -g root -m 0755 "$INSTALL_ROOT"
if [ ! -x "$INSTALL_ROOT/.venv/bin/python" ]; then
    "$PYTHON_BIN" -m venv "$INSTALL_ROOT/.venv"
fi
"$INSTALL_ROOT/.venv/bin/python" -m pip install --upgrade pip
"$INSTALL_ROOT/.venv/bin/python" -m pip install --upgrade "$PROJECT_ROOT"

install -d -o root -g arena-hero -m 0750 "$RUNTIME_DIR"
if [ ! -e "$RUNTIME_ENV" ]; then
    install -o root -g arena-hero -m 0640 \
        "$PROJECT_ROOT/deploy/arena-hero-runtime.env" "$RUNTIME_ENV"
fi

API_KEY=${ARENA_HERO_API_KEY:-}
if [ -n "$API_KEY_FILE" ]; then
    API_KEY=$(sed -n '1p' "$API_KEY_FILE" | tr -d '\r\n')
fi
if [ -z "$API_KEY" ] && [ ! -e "$AGENT_ENV" ]; then
    if [ ! -t 0 ]; then
        echo "Provide --api-key-file, ARENA_HERO_API_KEY, or an existing $AGENT_ENV." >&2
        exit 2
    fi
    printf 'Arena Hero API key: ' >&2
    stty -echo
    IFS= read -r API_KEY
    stty echo
    printf '\n' >&2
fi
if [ -n "$API_KEY" ]; then
    umask 077
    TEMP_ENV=$(mktemp /etc/.arena-hero-agent.env.XXXXXX)
    printf 'ARENA_HERO_API_KEY=%s\n' "$API_KEY" > "$TEMP_ENV"
    chown root:arena-hero "$TEMP_ENV"
    chmod 0640 "$TEMP_ENV"
    mv -f "$TEMP_ENV" "$AGENT_ENV"
fi
unset API_KEY

if [ ! -r "$AGENT_ENV" ] || ! grep -qE '^ARENA_HERO_API_KEY=[^[:space:]]+' "$AGENT_ENV"; then
    echo "A valid Arena Hero API key was not configured in $AGENT_ENV." >&2
    exit 2
fi
STORED_API_KEY=$(sed -n 's/^ARENA_HERO_API_KEY=//p' "$AGENT_ENV" | sed -n '1p')
case "$STORED_API_KEY" in
    replace-with-*|your-*|\<*|"")
        echo "A real Arena Hero API key is required in $AGENT_ENV." >&2
        exit 2
        ;;
esac
unset STORED_API_KEY
chown root:arena-hero "$AGENT_ENV"
chmod 0640 "$AGENT_ENV"

if [ -n "$AI_ENV_FILE" ]; then
    install -o root -g arena-hero-supervisor -m 0640 "$AI_ENV_FILE" "$SUPERVISOR_ENV"
fi

for unit in \
    arena-hero-agent.service \
    arena-hero-version-monitor.service \
    arena-hero-version-monitor.timer \
    arena-hero-supervisor.service \
    arena-hero-supervisor.timer \
    arena-hero-optimizer.service \
    arena-hero-optimizer.timer
do
    install -o root -g root -m 0644 \
        "$PROJECT_ROOT/deploy/$unit" "/etc/systemd/system/$unit"
done

systemctl daemon-reload

if [ "$DISABLE_SUPERVISOR" -eq 1 ]; then
    systemctl disable --now arena-hero-supervisor.timer || true
    systemctl stop arena-hero-supervisor.service || true
fi
if [ "$DISABLE_AI" -eq 1 ]; then
    systemctl stop arena-hero-supervisor.service || true
    rm -f "$SUPERVISOR_ENV"
fi
if [ "$DISABLE_OPTIMIZER" -eq 1 ]; then
    systemctl disable --now arena-hero-optimizer.timer || true
    systemctl stop arena-hero-optimizer.service || true
fi

if [ "$START_SERVICES" -eq 1 ]; then
    systemctl enable --now arena-hero-version-monitor.timer
    systemctl start arena-hero-version-monitor.service
    systemctl enable arena-hero-agent.service
    systemctl restart arena-hero-agent.service
    if [ "$WITH_SUPERVISOR" -eq 1 ]; then
        systemctl enable --now arena-hero-supervisor.timer
        systemctl start arena-hero-supervisor.service
    fi
    if [ "$WITH_OPTIMIZER" -eq 1 ]; then
        systemctl enable --now arena-hero-optimizer.timer
    fi
fi

echo "Installed Arena Hero Agent in $INSTALL_ROOT."
echo "Main service: systemctl status arena-hero-agent.service"
echo "Logs: journalctl -fu arena-hero-agent.service -o short-iso-precise"
if [ "$WITH_SUPERVISOR" -eq 0 ]; then
    echo "Supervisor not enabled. Re-run with --with-supervisor or --with-ai ENV_FILE."
fi
if [ "$WITH_OPTIMIZER" -eq 0 ]; then
    echo "Root optimizer not enabled."
fi
if [ "$DISABLE_SUPERVISOR" -eq 1 ]; then
    echo "Supervisor timer disabled."
fi
if [ "$DISABLE_AI" -eq 1 ]; then
    echo "Private AI configuration removed."
fi
if [ "$DISABLE_OPTIMIZER" -eq 1 ]; then
    echo "Root optimizer disabled."
fi
