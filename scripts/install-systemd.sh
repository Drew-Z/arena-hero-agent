#!/bin/sh
set -eu

INSTALL_ROOT=${ARENA_INSTALL_ROOT:-/opt/arena-hero-agent}
RELEASES_DIR=$INSTALL_ROOT/releases
CURRENT_LINK=$INSTALL_ROOT/current
PREVIOUS_LINK=$INSTALL_ROOT/previous
LOCK_FILE=$INSTALL_ROOT/.deploy.lock
TRANSACTION_FILE=$INSTALL_ROOT/.link-transaction
AGENT_ENV=${ARENA_AGENT_ENV:-/etc/arena-hero-agent.env}
RUNTIME_DIR=${ARENA_RUNTIME_DIR:-/etc/arena-hero-agent}
RUNTIME_ENV=$RUNTIME_DIR/runtime.env
SUPERVISOR_ENV=${ARENA_SUPERVISOR_ENV:-/etc/arena-hero-supervisor.env}
SYSTEMD_UNIT_DIR=${ARENA_SYSTEMD_UNIT_DIR:-/etc/systemd/system}
ROLLBACK_BIN=${ARENA_ROLLBACK_BIN:-/usr/local/sbin/arena-hero-rollback}
SYSTEMCTL_BIN=${ARENA_SYSTEMCTL_BIN:-systemctl}
PYTHON_BIN=${PYTHON_BIN:-}
SOURCE_COMMIT=${ARENA_SOURCE_COMMIT:-}
PIP_INDEX_URL_OVERRIDE=${ARENA_PIP_INDEX_URL:-}
HEALTH_ATTEMPTS=${ARENA_HEALTH_ATTEMPTS:-12}
HEALTH_INTERVAL=${ARENA_HEALTH_INTERVAL:-10}
MIN_SYSTEMD_VERSION=235
FULL_HARDENING_SYSTEMD_VERSION=247
WITH_SUPERVISOR=0
WITH_OPTIMIZER=0
DISABLE_SUPERVISOR=0
DISABLE_AI=0
DISABLE_OPTIMIZER=0
START_SERVICES=1
API_KEY_FILE=
AI_ENV_FILE=
BUILD_DIR=
TEMP_LINK=
TEMP_TRANSACTION=
TEMP_UNIT=
TEMP_ROLLBACK=
LINK_TRANSACTION_ACTIVE=0
TRANSACTION_CURRENT=
TRANSACTION_PREVIOUS=
SERVICE_STATE_CAPTURED=0

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
  --no-start            Activate the release without enabling, starting, or checking services.
  --python PATH         Explicit Python 3.11+ interpreter (default: auto-detect).
  -h, --help            Show this help.

Environment:
  ARENA_PIP_INDEX_URL   Trusted HTTPS package index for this install only.
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

python_version_supported() {
    "$1" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' \
        >/dev/null 2>&1
}

select_python() {
    if [ -n "$PYTHON_BIN" ]; then
        if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
            echo "Selected Python interpreter is unavailable: $PYTHON_BIN" >&2
            exit 2
        fi
        if ! python_version_supported "$PYTHON_BIN"; then
            echo "Selected Python interpreter must be Python 3.11 or newer: $PYTHON_BIN" >&2
            exit 2
        fi
        PYTHON_BIN=$(command -v "$PYTHON_BIN")
        return
    fi

    for candidate in python3 python3.13 python3.12 python3.11; do
        if command -v "$candidate" >/dev/null 2>&1 && python_version_supported "$candidate"; then
            PYTHON_BIN=$(command -v "$candidate")
            return
        fi
    done

    echo "No compatible Python interpreter was found. Install Python 3.11+ or pass --python /path/to/python3.11." >&2
    exit 2
}

run_pip() {
    if [ -n "$PIP_INDEX_URL_OVERRIDE" ]; then
        PIP_CONFIG_FILE=/dev/null \
        PIP_INDEX_URL=$PIP_INDEX_URL_OVERRIDE \
        PIP_EXTRA_INDEX_URL= \
            "$@"
    else
        "$@"
    fi
}

check_systemd_version() {
    version_line=$("$SYSTEMCTL_BIN" --version 2>/dev/null | sed -n '1p') || {
        echo "Unable to read the installed systemd version." >&2
        exit 2
    }
    set -- $version_line
    systemd_version=${2:-}
    if [ "${1:-}" != "systemd" ]; then
        systemd_version=
    fi
    case "$systemd_version" in
        ""|*[!0-9]*)
            echo "Unable to parse the installed systemd version: $version_line" >&2
            exit 2
            ;;
    esac
    if [ "$systemd_version" -lt "$MIN_SYSTEMD_VERSION" ]; then
        echo "systemd $MIN_SYSTEMD_VERSION or newer is required; found systemd $systemd_version." >&2
        exit 2
    fi
    if [ "$systemd_version" -lt "$FULL_HARDENING_SYSTEMD_VERSION" ]; then
        echo "Warning: systemd $systemd_version can run the Agent, but some process and kernel isolation directives require systemd $FULL_HARDENING_SYSTEMD_VERSION+." >&2
    fi
}

for command_name in basename cat chmod chown date dirname flock getent grep groupadd id install ln mktemp mv readlink rm sed sleep tr useradd; do
    require_command "$command_name"
done
if [ "$WITH_SUPERVISOR" -eq 1 ]; then
    require_command usermod
fi
require_command "$SYSTEMCTL_BIN"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer as root (for example, with sudo)." >&2
    exit 2
fi
check_systemd_version
select_python
if [ -n "$PIP_INDEX_URL_OVERRIDE" ] && ! printf '%s\n' "$PIP_INDEX_URL_OVERRIDE" | \
    grep -Eq '^https://[^/[:space:]@]+(/[^[:space:]@]*)?$'; then
    echo "ARENA_PIP_INDEX_URL must be an HTTPS URL without credentials or whitespace." >&2
    exit 2
fi
if [ -n "$SOURCE_COMMIT" ] && ! printf '%s\n' "$SOURCE_COMMIT" | \
    grep -Eq '^[0-9a-fA-F]{40}([0-9a-fA-F]{24})?$'; then
    echo "ARENA_SOURCE_COMMIT must be a full 40- or 64-character Git object ID." >&2
    exit 2
fi
if [ "$WITH_SUPERVISOR" -eq 1 ] && ! getent group systemd-journal >/dev/null 2>&1; then
    echo "The systemd-journal group is required when enabling the supervisor." >&2
    exit 2
fi

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(dirname "$SCRIPT_DIR")

for deployment_path in \
    "$INSTALL_ROOT" \
    "$AGENT_ENV" \
    "$RUNTIME_DIR" \
    "$SUPERVISOR_ENV" \
    "$SYSTEMD_UNIT_DIR" \
    "$ROLLBACK_BIN"
do
    case "$deployment_path" in
        /*) ;;
        *)
            echo "Deployment paths must be absolute: $deployment_path" >&2
            exit 2
            ;;
    esac
    case "$deployment_path" in
        *[!A-Za-z0-9_./-]*)
            echo "Deployment path contains unsupported characters: $deployment_path" >&2
            exit 2
            ;;
    esac
done

for required_file in \
    "$PROJECT_ROOT/pyproject.toml" \
    "$PROJECT_ROOT/requirements-build.lock" \
    "$PROJECT_ROOT/requirements.lock" \
    "$PROJECT_ROOT/scripts/rollback-systemd.sh"
do
    if [ ! -r "$required_file" ]; then
        echo "Required release file is missing: $required_file" >&2
        exit 2
    fi
done

"$PYTHON_BIN" -c 'import tempfile, venv; root = tempfile.TemporaryDirectory(); venv.EnvBuilder(with_pip=True).create(root.name)' || {
    echo "The selected Python requires venv with pip. Install its matching venv/pip package (for example python3.11-venv on Debian/Ubuntu) or select another system Python with --python." >&2
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

cleanup() {
    if [ "$LINK_TRANSACTION_ACTIVE" -eq 1 ]; then
        rollback_link_transaction || {
            echo "Failed to restore an interrupted release-link transaction." >&2
        }
    fi
    if [ -n "$TEMP_LINK" ]; then
        rm -f "$TEMP_LINK"
    fi
    if [ -n "$TEMP_TRANSACTION" ]; then
        rm -f "$TEMP_TRANSACTION"
    fi
    if [ -n "$TEMP_UNIT" ]; then
        rm -f "$TEMP_UNIT"
    fi
    if [ -n "$TEMP_ROLLBACK" ]; then
        rm -f "$TEMP_ROLLBACK"
    fi
    if [ -n "$BUILD_DIR" ] && [ -d "$BUILD_DIR" ]; then
        rm -rf "$BUILD_DIR"
    fi
}
trap cleanup EXIT
on_signal() {
    trap - EXIT HUP INT TERM
    if [ "$LINK_TRANSACTION_ACTIVE" -eq 1 ] && [ "$SERVICE_STATE_CAPTURED" -eq 1 ]; then
        if restore_old_release; then
            finish_link_transaction
        elif rollback_link_transaction; then
            finish_link_transaction
        else
            echo "Interrupted release activation could not be restored; keeping the transaction journal." >&2
        fi
    fi
    cleanup
    exit 1
}
trap on_signal HUP INT TERM

install -d -o root -g root -m 0755 "$INSTALL_ROOT" "$RELEASES_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    echo "Another Arena Hero install or rollback is in progress." >&2
    exit 1
fi

resolve_release_link() {
    link_path=$1
    [ -L "$link_path" ] || return 1
    resolved_path=$(readlink -f "$link_path") || return 1
    case "$resolved_path" in
        "$RELEASES_DIR"/*) ;;
        *) return 1 ;;
    esac
    [ "$(dirname "$resolved_path")" = "$RELEASES_DIR" ] || return 1
    [ -d "$resolved_path" ] || return 1
    printf '%s\n' "$resolved_path"
}

validate_release() {
    release_path=$1
    [ -d "$release_path" ] || return 1
    for command_name in \
        arena-hero-agent \
        arena-hero-health \
        arena-hero-optimizer \
        arena-hero-supervisor \
        arena-hero-version-monitor
    do
        [ -x "$release_path/.venv/bin/$command_name" ] || return 1
    done
}

replace_release_link() {
    link_path=$1
    release_path=$2
    validate_release "$release_path" || {
        echo "Refusing to link an incomplete release: $release_path" >&2
        return 1
    }
    link_name=$(basename "$link_path")
    release_name=$(basename "$release_path")
    TEMP_LINK=$INSTALL_ROOT/.$link_name.tmp.$$
    rm -f "$TEMP_LINK"
    ln -s "releases/$release_name" "$TEMP_LINK"
    mv -Tf "$TEMP_LINK" "$link_path"
    TEMP_LINK=
}

restore_release_link() {
    link_path=$1
    release_path=$2
    if [ -n "$release_path" ]; then
        replace_release_link "$link_path" "$release_path"
    else
        rm -f "$link_path"
    fi
}

begin_link_transaction() {
    TRANSACTION_CURRENT=$1
    TRANSACTION_PREVIOUS=$2
    current_name=
    previous_name=
    if [ -n "$TRANSACTION_CURRENT" ]; then
        current_name=$(basename "$TRANSACTION_CURRENT")
    fi
    if [ -n "$TRANSACTION_PREVIOUS" ]; then
        previous_name=$(basename "$TRANSACTION_PREVIOUS")
    fi
    LINK_TRANSACTION_ACTIVE=1
    TEMP_TRANSACTION=$INSTALL_ROOT/.link-transaction.tmp.$$
    printf '%s\n%s\n' "$current_name" "$previous_name" > "$TEMP_TRANSACTION"
    chmod 0600 "$TEMP_TRANSACTION"
    mv -f "$TEMP_TRANSACTION" "$TRANSACTION_FILE"
    TEMP_TRANSACTION=
}

finish_link_transaction() {
    LINK_TRANSACTION_ACTIVE=0
    TRANSACTION_CURRENT=
    TRANSACTION_PREVIOUS=
    rm -f "$TRANSACTION_FILE"
}

rollback_link_transaction() {
    LINK_TRANSACTION_ACTIVE=0
    restore_release_link "$CURRENT_LINK" "$TRANSACTION_CURRENT" || return 1
    restore_release_link "$PREVIOUS_LINK" "$TRANSACTION_PREVIOUS" || return 1
    rm -f "$TRANSACTION_FILE"
    TRANSACTION_CURRENT=
    TRANSACTION_PREVIOUS=
}

recover_pending_link_transaction() {
    [ -f "$TRANSACTION_FILE" ] || return 0
    current_name=$(sed -n '1p' "$TRANSACTION_FILE" | tr -d '\r')
    previous_name=$(sed -n '2p' "$TRANSACTION_FILE" | tr -d '\r')
    for release_name in "$current_name" "$previous_name"; do
        case "$release_name" in
            "") ;;
            *[!A-Za-z0-9._-]*)
                echo "Invalid pending link transaction in $TRANSACTION_FILE." >&2
                return 1
                ;;
        esac
    done
    recovered_current=
    recovered_previous=
    if [ -n "$current_name" ]; then
        recovered_current=$RELEASES_DIR/$current_name
        validate_release "$recovered_current" || return 1
    fi
    if [ -n "$previous_name" ]; then
        recovered_previous=$RELEASES_DIR/$previous_name
        validate_release "$recovered_previous" || return 1
    fi
    restore_release_link "$CURRENT_LINK" "$recovered_current"
    restore_release_link "$PREVIOUS_LINK" "$recovered_previous"
    rm -f "$TRANSACTION_FILE"
    echo "Recovered an interrupted Arena Hero release-link transaction."
}

recover_pending_link_transaction

OLD_CURRENT=
if [ -e "$CURRENT_LINK" ] || [ -L "$CURRENT_LINK" ]; then
    OLD_CURRENT=$(resolve_release_link "$CURRENT_LINK") || {
        echo "$CURRENT_LINK must be a valid symlink into $RELEASES_DIR." >&2
        exit 1
    }
elif [ -x "$INSTALL_ROOT/.venv/bin/arena-hero-agent" ]; then
    LEGACY_RELEASE=$RELEASES_DIR/legacy-pre-atomic
    if [ ! -e "$LEGACY_RELEASE" ]; then
        install -d -o root -g root -m 0755 "$LEGACY_RELEASE"
        ln -s ../../.venv "$LEGACY_RELEASE/.venv"
        printf '%s\n' legacy-pre-atomic > "$LEGACY_RELEASE/release-id"
    fi
    validate_release "$LEGACY_RELEASE" || {
        echo "The existing $INSTALL_ROOT/.venv cannot be used as a rollback target." >&2
        exit 1
    }
    OLD_CURRENT=$LEGACY_RELEASE
fi

OLD_PREVIOUS=
if [ -e "$PREVIOUS_LINK" ] || [ -L "$PREVIOUS_LINK" ]; then
    OLD_PREVIOUS=$(resolve_release_link "$PREVIOUS_LINK") || {
        echo "$PREVIOUS_LINK must be a valid symlink into $RELEASES_DIR." >&2
        exit 1
    }
fi

ensure_user() {
    if ! getent group "$1" >/dev/null 2>&1; then
        groupadd --system "$1"
    fi
    if ! id "$1" >/dev/null 2>&1; then
        nologin_shell=$(command -v nologin 2>/dev/null || true)
        case "$nologin_shell" in
            /*) ;;
            *) nologin_shell=/bin/false ;;
        esac
        useradd --system --no-create-home --home-dir /nonexistent \
            --shell "$nologin_shell" --gid "$1" "$1"
    fi
}

ensure_user arena-hero
ensure_user arena-hero-version
if [ "$WITH_SUPERVISOR" -eq 1 ]; then
    ensure_user arena-hero-supervisor
    usermod -a -G systemd-journal arena-hero-supervisor
fi

PROJECT_VERSION=$(sed -n 's/^version = "\([^"]*\)"/\1/p' "$PROJECT_ROOT/pyproject.toml" | sed -n '1p' | tr -d '\r')
case "$PROJECT_VERSION" in
    ""|*[!A-Za-z0-9._-]*)
        echo "pyproject.toml contains an unsafe or missing project version." >&2
        exit 1
        ;;
esac
RELEASE_ID=$PROJECT_VERSION-$(date -u +%Y%m%dT%H%M%SZ)-$$
FINAL_RELEASE=$RELEASES_DIR/$RELEASE_ID
BUILD_DIR=$FINAL_RELEASE
if [ -e "$BUILD_DIR" ]; then
    echo "Release path already exists: $FINAL_RELEASE" >&2
    exit 1
fi

install -d -o root -g root -m 0755 "$BUILD_DIR"
"$PYTHON_BIN" -m venv "$BUILD_DIR/.venv"
run_pip "$BUILD_DIR/.venv/bin/python" -m pip install --require-hashes \
    -r "$PROJECT_ROOT/requirements-build.lock"
run_pip "$BUILD_DIR/.venv/bin/python" -m pip install --require-hashes \
    -r "$PROJECT_ROOT/requirements.lock"
run_pip "$BUILD_DIR/.venv/bin/python" -m pip install --no-deps \
    --no-build-isolation "$PROJECT_ROOT"
run_pip "$BUILD_DIR/.venv/bin/python" -m pip check
for command_name in \
    arena-hero-agent \
    arena-hero-health \
    arena-hero-optimizer \
    arena-hero-supervisor \
    arena-hero-version-monitor
do
    "$BUILD_DIR/.venv/bin/$command_name" --help >/dev/null
done
printf '%s\n' "$RELEASE_ID" > "$BUILD_DIR/release-id"
printf '%s\n' "$PROJECT_VERSION" > "$BUILD_DIR/source-version"
if [ -n "$SOURCE_COMMIT" ]; then
    printf '%s\n' "$SOURCE_COMMIT" > "$BUILD_DIR/source-commit"
fi
chmod -R go-w "$BUILD_DIR"

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
    AGENT_ENV_DIR=$(dirname "$AGENT_ENV")
    TEMP_ENV=$(mktemp "$AGENT_ENV_DIR/.arena-hero-agent.env.XXXXXX")
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

install -d -o root -g root -m 0755 "$SYSTEMD_UNIT_DIR" "$(dirname "$ROLLBACK_BIN")"
for unit in \
    arena-hero-agent.service \
    arena-hero-version-monitor.service \
    arena-hero-version-monitor.timer \
    arena-hero-supervisor.service \
    arena-hero-supervisor.timer \
    arena-hero-optimizer.service \
    arena-hero-optimizer.timer
do
    TEMP_UNIT=$(mktemp "$SYSTEMD_UNIT_DIR/.$unit.XXXXXX")
    sed \
        -e "s|/opt/arena-hero-agent|$INSTALL_ROOT|g" \
        -e "s|/etc/arena-hero-agent.env|$AGENT_ENV|g" \
        -e "s|/etc/arena-hero-agent/runtime.env|$RUNTIME_ENV|g" \
        -e "s|/etc/arena-hero-supervisor.env|$SUPERVISOR_ENV|g" \
        "$PROJECT_ROOT/deploy/$unit" > "$TEMP_UNIT"
    chmod 0644 "$TEMP_UNIT"
    chown root:root "$TEMP_UNIT"
    mv -f "$TEMP_UNIT" "$SYSTEMD_UNIT_DIR/$unit"
    TEMP_UNIT=
done
TEMP_ROLLBACK=$(mktemp "$(dirname "$ROLLBACK_BIN")/.arena-hero-rollback.XXXXXX")
sed "s|/opt/arena-hero-agent|$INSTALL_ROOT|g" \
    "$PROJECT_ROOT/scripts/rollback-systemd.sh" > "$TEMP_ROLLBACK"
chmod 0755 "$TEMP_ROLLBACK"
chown root:root "$TEMP_ROLLBACK"
mv -f "$TEMP_ROLLBACK" "$ROLLBACK_BIN"
TEMP_ROLLBACK=

if [ "$DISABLE_SUPERVISOR" -eq 1 ]; then
    "$SYSTEMCTL_BIN" disable --now arena-hero-supervisor.timer || true
    "$SYSTEMCTL_BIN" stop arena-hero-supervisor.service || true
fi
if [ "$DISABLE_AI" -eq 1 ]; then
    "$SYSTEMCTL_BIN" stop arena-hero-supervisor.service || true
    rm -f "$SUPERVISOR_ENV"
fi
if [ "$DISABLE_OPTIMIZER" -eq 1 ]; then
    "$SYSTEMCTL_BIN" disable --now arena-hero-optimizer.timer || true
    "$SYSTEMCTL_BIN" stop arena-hero-optimizer.service || true
fi

SUPERVISOR_WAS_ACTIVE=0
OPTIMIZER_WAS_ACTIVE=0
AGENT_WAS_ENABLED=0
AGENT_WAS_ACTIVE=0
VERSION_TIMER_WAS_ENABLED=0
VERSION_TIMER_WAS_ACTIVE=0
SUPERVISOR_TIMER_WAS_ENABLED=0
SUPERVISOR_TIMER_WAS_ACTIVE=0
OPTIMIZER_TIMER_WAS_ENABLED=0
OPTIMIZER_TIMER_WAS_ACTIVE=0
if [ "$START_SERVICES" -eq 1 ]; then
    if "$SYSTEMCTL_BIN" is-enabled --quiet arena-hero-agent.service; then
        AGENT_WAS_ENABLED=1
    fi
    if "$SYSTEMCTL_BIN" is-active --quiet arena-hero-agent.service; then
        AGENT_WAS_ACTIVE=1
    fi
    if "$SYSTEMCTL_BIN" is-enabled --quiet arena-hero-version-monitor.timer; then
        VERSION_TIMER_WAS_ENABLED=1
    fi
    if "$SYSTEMCTL_BIN" is-active --quiet arena-hero-version-monitor.timer; then
        VERSION_TIMER_WAS_ACTIVE=1
    fi
    if "$SYSTEMCTL_BIN" is-enabled --quiet arena-hero-supervisor.timer; then
        SUPERVISOR_TIMER_WAS_ENABLED=1
    fi
    if "$SYSTEMCTL_BIN" is-active --quiet arena-hero-supervisor.timer; then
        SUPERVISOR_TIMER_WAS_ACTIVE=1
    fi
    if "$SYSTEMCTL_BIN" is-enabled --quiet arena-hero-optimizer.timer; then
        OPTIMIZER_TIMER_WAS_ENABLED=1
    fi
    if "$SYSTEMCTL_BIN" is-active --quiet arena-hero-optimizer.timer; then
        OPTIMIZER_TIMER_WAS_ACTIVE=1
    fi
    if "$SYSTEMCTL_BIN" is-active --quiet arena-hero-supervisor.service; then
        SUPERVISOR_WAS_ACTIVE=1
    fi
    if "$SYSTEMCTL_BIN" is-active --quiet arena-hero-optimizer.service; then
        OPTIMIZER_WAS_ACTIVE=1
    fi
    "$SYSTEMCTL_BIN" stop arena-hero-supervisor.service arena-hero-optimizer.service || true
fi
SERVICE_STATE_CAPTURED=1

BUILD_DIR=

wait_for_health() {
    attempt=1
    while [ "$attempt" -le "$HEALTH_ATTEMPTS" ]; do
        if "$CURRENT_LINK/.venv/bin/arena-hero-health"; then
            return 0
        fi
        if [ "$attempt" -lt "$HEALTH_ATTEMPTS" ]; then
            sleep "$HEALTH_INTERVAL"
        fi
        attempt=$((attempt + 1))
    done
    return 1
}

activate_services() {
    "$SYSTEMCTL_BIN" daemon-reload || return 1
    if [ "$START_SERVICES" -eq 0 ]; then
        return 0
    fi
    "$SYSTEMCTL_BIN" enable --now arena-hero-version-monitor.timer || return 1
    "$SYSTEMCTL_BIN" start arena-hero-version-monitor.service || return 1
    "$SYSTEMCTL_BIN" enable arena-hero-agent.service || return 1
    "$SYSTEMCTL_BIN" restart arena-hero-agent.service || return 1
    wait_for_health || return 1
    if [ "$WITH_SUPERVISOR" -eq 1 ]; then
        "$SYSTEMCTL_BIN" enable --now arena-hero-supervisor.timer || return 1
        "$SYSTEMCTL_BIN" start arena-hero-supervisor.service || return 1
    elif [ "$SUPERVISOR_WAS_ACTIVE" -eq 1 ]; then
        "$SYSTEMCTL_BIN" start arena-hero-supervisor.service || return 1
    fi
    if [ "$WITH_OPTIMIZER" -eq 1 ]; then
        "$SYSTEMCTL_BIN" enable --now arena-hero-optimizer.timer || return 1
    elif [ "$OPTIMIZER_WAS_ACTIVE" -eq 1 ]; then
        "$SYSTEMCTL_BIN" start arena-hero-optimizer.service || return 1
    fi
}

restore_enablement() {
    unit_name=$1
    was_enabled=$2
    if [ "$was_enabled" -eq 1 ]; then
        "$SYSTEMCTL_BIN" enable "$unit_name" || return 1
    else
        "$SYSTEMCTL_BIN" disable "$unit_name" || return 1
    fi
}

restore_timer_state() {
    unit_name=$1
    was_enabled=$2
    was_active=$3
    restore_enablement "$unit_name" "$was_enabled" || return 1
    if [ "$was_active" -eq 1 ]; then
        "$SYSTEMCTL_BIN" start "$unit_name" || return 1
    else
        "$SYSTEMCTL_BIN" stop "$unit_name" || return 1
    fi
}

restore_old_release() {
    restore_release_link "$CURRENT_LINK" "$OLD_CURRENT" || return 1
    restore_release_link "$PREVIOUS_LINK" "$OLD_PREVIOUS" || return 1
    "$SYSTEMCTL_BIN" daemon-reload || return 1
    if [ "$START_SERVICES" -eq 1 ]; then
        if [ -n "$OLD_CURRENT" ]; then
            "$SYSTEMCTL_BIN" start arena-hero-version-monitor.service || true
        fi
        restore_enablement arena-hero-agent.service "$AGENT_WAS_ENABLED" || return 1
        if [ -n "$OLD_CURRENT" ] && [ "$AGENT_WAS_ACTIVE" -eq 1 ]; then
            "$SYSTEMCTL_BIN" restart arena-hero-agent.service || return 1
        else
            "$SYSTEMCTL_BIN" stop arena-hero-agent.service || return 1
        fi
        restore_timer_state arena-hero-version-monitor.timer \
            "$VERSION_TIMER_WAS_ENABLED" "$VERSION_TIMER_WAS_ACTIVE" || return 1
        restore_timer_state arena-hero-supervisor.timer \
            "$SUPERVISOR_TIMER_WAS_ENABLED" "$SUPERVISOR_TIMER_WAS_ACTIVE" || return 1
        restore_timer_state arena-hero-optimizer.timer \
            "$OPTIMIZER_TIMER_WAS_ENABLED" "$OPTIMIZER_TIMER_WAS_ACTIVE" || return 1
        if [ "$SUPERVISOR_WAS_ACTIVE" -eq 1 ]; then
            "$SYSTEMCTL_BIN" start arena-hero-supervisor.service || return 1
        else
            "$SYSTEMCTL_BIN" stop arena-hero-supervisor.service || return 1
        fi
        if [ "$OPTIMIZER_WAS_ACTIVE" -eq 1 ]; then
            "$SYSTEMCTL_BIN" start arena-hero-optimizer.service || return 1
        else
            "$SYSTEMCTL_BIN" stop arena-hero-optimizer.service || return 1
        fi
    fi
}

begin_link_transaction "$OLD_CURRENT" "$OLD_PREVIOUS"
if [ -n "$OLD_CURRENT" ]; then
    if ! replace_release_link "$PREVIOUS_LINK" "$OLD_CURRENT"; then
        rollback_link_transaction || true
        if [ "$SUPERVISOR_WAS_ACTIVE" -eq 1 ]; then
            "$SYSTEMCTL_BIN" start arena-hero-supervisor.service || true
        fi
        if [ "$OPTIMIZER_WAS_ACTIVE" -eq 1 ]; then
            "$SYSTEMCTL_BIN" start arena-hero-optimizer.service || true
        fi
        exit 1
    fi
else
    rm -f "$PREVIOUS_LINK"
fi
if ! replace_release_link "$CURRENT_LINK" "$FINAL_RELEASE"; then
    rollback_link_transaction || true
    if [ "$SUPERVISOR_WAS_ACTIVE" -eq 1 ]; then
        "$SYSTEMCTL_BIN" start arena-hero-supervisor.service || true
    fi
    if [ "$OPTIMIZER_WAS_ACTIVE" -eq 1 ]; then
        "$SYSTEMCTL_BIN" start arena-hero-optimizer.service || true
    fi
    exit 1
fi

if ! activate_services; then
    echo "Release activation failed; restoring the previous release." >&2
    if restore_old_release; then
        finish_link_transaction
    else
        echo "Automatic restoration also failed. Inspect current/previous links and systemd logs immediately." >&2
    fi
    exit 1
fi
finish_link_transaction

echo "Installed Arena Hero Agent release $RELEASE_ID."
if [ -n "$SOURCE_COMMIT" ]; then
    echo "Source commit: $SOURCE_COMMIT"
fi
echo "Current release: $CURRENT_LINK -> $(readlink "$CURRENT_LINK")"
if [ -L "$PREVIOUS_LINK" ]; then
    echo "Previous release: $PREVIOUS_LINK -> $(readlink "$PREVIOUS_LINK")"
else
    echo "Previous release: unavailable (this was the first installation)."
fi
echo "Rollback: $ROLLBACK_BIN"
echo "Main service: $SYSTEMCTL_BIN status arena-hero-agent.service"
echo "Logs: journalctl -fu arena-hero-agent.service -o short-iso-precise"
if [ "$START_SERVICES" -eq 0 ]; then
    echo "Services were not enabled, started, restarted, or health-checked (--no-start)."
fi
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
