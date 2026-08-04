#!/bin/sh
set -eu

INSTALL_ROOT=${ARENA_INSTALL_ROOT:-/opt/arena-hero-agent}
RELEASES_DIR=$INSTALL_ROOT/releases
CURRENT_LINK=$INSTALL_ROOT/current
PREVIOUS_LINK=$INSTALL_ROOT/previous
LOCK_FILE=$INSTALL_ROOT/.deploy.lock
TRANSACTION_FILE=$INSTALL_ROOT/.link-transaction
SYSTEMCTL_BIN=${ARENA_SYSTEMCTL_BIN:-systemctl}
HEALTH_ATTEMPTS=${ARENA_HEALTH_ATTEMPTS:-12}
HEALTH_INTERVAL=${ARENA_HEALTH_INTERVAL:-10}
TEMP_LINK=
TEMP_TRANSACTION=
LINK_TRANSACTION_ACTIVE=0
TRANSACTION_CURRENT=
TRANSACTION_PREVIOUS=
SERVICE_STATE_CAPTURED=0

usage() {
    cat <<'EOF'
Usage: sudo arena-hero-rollback

Atomically switch current to the previous validated Arena Hero release. The
release being replaced becomes previous, so running the command again switches
forward. Compatibility, restart, and health failures restore the original pair.
EOF
}

case "${1:-}" in
    -h|--help)
        usage
        exit 0
        ;;
    "") ;;
    *)
        echo "Unknown option: $1" >&2
        usage >&2
        exit 2
        ;;
esac

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Required command is unavailable: $1" >&2
        exit 2
    fi
}

for command_name in basename chmod dirname flock id ln mv readlink rm sed sleep tr; do
    require_command "$command_name"
done
require_command "$SYSTEMCTL_BIN"

if [ "$(id -u)" -ne 0 ]; then
    echo "Run rollback as root (for example, with sudo)." >&2
    exit 2
fi
if [ ! -d "$RELEASES_DIR" ]; then
    echo "No versioned Arena Hero installation exists in $INSTALL_ROOT." >&2
    exit 1
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
}
trap cleanup EXIT
on_signal() {
    trap - EXIT HUP INT TERM
    if [ "$LINK_TRANSACTION_ACTIVE" -eq 1 ] && [ "$SERVICE_STATE_CAPTURED" -eq 1 ]; then
        if restore_original_release; then
            finish_link_transaction
        elif rollback_link_transaction; then
            finish_link_transaction
        else
            echo "Interrupted rollback could not be restored; keeping the transaction journal." >&2
        fi
    fi
    cleanup
    exit 1
}
trap on_signal HUP INT TERM

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

CURRENT_RELEASE=$(resolve_release_link "$CURRENT_LINK") || {
    echo "$CURRENT_LINK must be a valid symlink into $RELEASES_DIR." >&2
    exit 1
}
PREVIOUS_RELEASE=$(resolve_release_link "$PREVIOUS_LINK") || {
    echo "No valid previous release is available in $PREVIOUS_LINK." >&2
    exit 1
}
validate_release "$CURRENT_RELEASE" || {
    echo "Current release is incomplete: $CURRENT_RELEASE" >&2
    exit 1
}
validate_release "$PREVIOUS_RELEASE" || {
    echo "Previous release is incomplete: $PREVIOUS_RELEASE" >&2
    exit 1
}
if [ "$CURRENT_RELEASE" = "$PREVIOUS_RELEASE" ]; then
    echo "Current and previous resolve to the same release; refusing a no-op rollback." >&2
    exit 1
fi

SUPERVISOR_WAS_ACTIVE=0
OPTIMIZER_WAS_ACTIVE=0
if "$SYSTEMCTL_BIN" is-active --quiet arena-hero-supervisor.service; then
    SUPERVISOR_WAS_ACTIVE=1
fi
if "$SYSTEMCTL_BIN" is-active --quiet arena-hero-optimizer.service; then
    OPTIMIZER_WAS_ACTIVE=1
fi
"$SYSTEMCTL_BIN" stop arena-hero-supervisor.service arena-hero-optimizer.service || true
SERVICE_STATE_CAPTURED=1

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

start_selected_release() {
    "$SYSTEMCTL_BIN" daemon-reload || return 1
    "$SYSTEMCTL_BIN" start arena-hero-version-monitor.service || return 1
    "$SYSTEMCTL_BIN" restart arena-hero-agent.service || return 1
    wait_for_health || return 1
    if [ "$SUPERVISOR_WAS_ACTIVE" -eq 1 ]; then
        "$SYSTEMCTL_BIN" start arena-hero-supervisor.service || return 1
    fi
    if [ "$OPTIMIZER_WAS_ACTIVE" -eq 1 ]; then
        "$SYSTEMCTL_BIN" start arena-hero-optimizer.service || return 1
    fi
}

restore_original_release() {
    replace_release_link "$CURRENT_LINK" "$CURRENT_RELEASE" || return 1
    replace_release_link "$PREVIOUS_LINK" "$PREVIOUS_RELEASE" || return 1
    "$SYSTEMCTL_BIN" daemon-reload || return 1
    "$SYSTEMCTL_BIN" start arena-hero-version-monitor.service || true
    "$SYSTEMCTL_BIN" restart arena-hero-agent.service || return 1
    if [ "$SUPERVISOR_WAS_ACTIVE" -eq 1 ]; then
        "$SYSTEMCTL_BIN" start arena-hero-supervisor.service || true
    fi
    if [ "$OPTIMIZER_WAS_ACTIVE" -eq 1 ]; then
        "$SYSTEMCTL_BIN" start arena-hero-optimizer.service || true
    fi
}

begin_link_transaction "$CURRENT_RELEASE" "$PREVIOUS_RELEASE"
if ! replace_release_link "$CURRENT_LINK" "$PREVIOUS_RELEASE"; then
    rollback_link_transaction || true
    if [ "$SUPERVISOR_WAS_ACTIVE" -eq 1 ]; then
        "$SYSTEMCTL_BIN" start arena-hero-supervisor.service || true
    fi
    if [ "$OPTIMIZER_WAS_ACTIVE" -eq 1 ]; then
        "$SYSTEMCTL_BIN" start arena-hero-optimizer.service || true
    fi
    exit 1
fi
if ! replace_release_link "$PREVIOUS_LINK" "$CURRENT_RELEASE"; then
    rollback_link_transaction || true
    if [ "$SUPERVISOR_WAS_ACTIVE" -eq 1 ]; then
        "$SYSTEMCTL_BIN" start arena-hero-supervisor.service || true
    fi
    if [ "$OPTIMIZER_WAS_ACTIVE" -eq 1 ]; then
        "$SYSTEMCTL_BIN" start arena-hero-optimizer.service || true
    fi
    exit 1
fi

if ! start_selected_release; then
    echo "Rollback target failed compatibility, restart, or health checks; restoring the original release." >&2
    if restore_original_release; then
        finish_link_transaction
    else
        echo "Automatic restoration also failed. Inspect current/previous links and systemd logs immediately." >&2
    fi
    exit 1
fi
finish_link_transaction

echo "Arena Hero rollback completed."
echo "Current release: $CURRENT_LINK -> $(readlink "$CURRENT_LINK")"
echo "Previous release: $PREVIOUS_LINK -> $(readlink "$PREVIOUS_LINK")"
