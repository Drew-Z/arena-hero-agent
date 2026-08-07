#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(dirname "$SCRIPT_DIR")
INSTALLER=$PROJECT_ROOT/scripts/install-systemd.sh
GIT_BIN=${ARENA_UPDATE_GIT_BIN:-git}
SUDO_BIN=${ARENA_UPDATE_SUDO_BIN:-sudo}
ID_BIN=${ARENA_UPDATE_ID_BIN:-id}
STAT_BIN=${ARENA_UPDATE_STAT_BIN:-stat}
PIP_INDEX_URL_OVERRIDE=${ARENA_PIP_INDEX_URL:-}
SOURCE_ARCHIVE=

usage() {
    cat <<'EOF'
Usage: sh scripts/update-systemd.sh

Fast-forward the current checkout to its configured upstream, archive that exact
commit, then switch the running systemd Agent from the old strategy process to
the new strategy. Run this command as the checkout owner, without sudo;
privilege escalation happens only for isolated deployment and service restart.

Set ARENA_PIP_INDEX_URL to a trusted HTTPS package index only when the configured
mirror has not synchronized a pinned dependency.
EOF
}

if [ "$#" -gt 1 ]; then
    echo "This updater does not accept installer or credential arguments." >&2
    usage >&2
    exit 2
fi
case "${1:-}" in
    "") ;;
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

require_command() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Required command is unavailable: $1" >&2
        exit 2
    fi
}

require_command "$GIT_BIN"
require_command "$ID_BIN"
require_command "$STAT_BIN"
for command_name in chmod grep mktemp rm tar; do
    require_command "$command_name"
done
if [ -n "$PIP_INDEX_URL_OVERRIDE" ] && ! printf '%s\n' "$PIP_INDEX_URL_OVERRIDE" | \
    grep -Eq '^https://[^/[:space:]@]+(/[^[:space:]@]*)?$'; then
    echo "ARENA_PIP_INDEX_URL must be an HTTPS URL without credentials or whitespace." >&2
    exit 2
fi
current_uid=$("$ID_BIN" -u)
case "$current_uid" in
    ""|*[!0-9]*)
        echo "Unable to determine the current numeric user ID." >&2
        exit 2
        ;;
esac
if [ "$current_uid" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
    echo "Run this updater without sudo so Git remains owned by the checkout user." >&2
    exit 2
fi
if [ ! -r "$INSTALLER" ]; then
    echo "Systemd installer is missing: $INSTALLER" >&2
    exit 2
fi

repository_root=$("$GIT_BIN" -C "$PROJECT_ROOT" rev-parse --show-toplevel 2>/dev/null) || {
    echo "The project directory is not a Git checkout." >&2
    exit 2
}
if [ "$repository_root" != "$PROJECT_ROOT" ]; then
    echo "Run the updater from the standalone Arena Hero repository root." >&2
    exit 2
fi
repository_uid=$("$STAT_BIN" -c '%u' "$repository_root") || {
    echo "Unable to determine the Git checkout owner." >&2
    exit 2
}
case "$repository_uid" in
    ""|*[!0-9]*)
        echo "Unable to determine the Git checkout owner." >&2
        exit 2
        ;;
esac
if [ "$repository_uid" -ne "$current_uid" ]; then
    echo "Run this updater as the Git checkout owner (UID $repository_uid), without sudo." >&2
    exit 2
fi

working_changes=$("$GIT_BIN" -C "$PROJECT_ROOT" status --porcelain --untracked-files=all)
if [ -n "$working_changes" ]; then
    echo "The Git worktree is not clean. Commit, stash, or remove local changes before updating." >&2
    exit 2
fi

branch=$("$GIT_BIN" -C "$PROJECT_ROOT" symbolic-ref --quiet --short HEAD) || {
    echo "The checkout is detached. Switch to a branch with a configured upstream." >&2
    exit 2
}
upstream=$("$GIT_BIN" -C "$PROJECT_ROOT" rev-parse \
    --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null) || {
    echo "The current branch has no configured upstream." >&2
    exit 2
}
upstream_ref=$("$GIT_BIN" -C "$PROJECT_ROOT" rev-parse \
    --symbolic-full-name '@{upstream}') || {
    echo "The configured upstream is not a symbolic remote-tracking branch." >&2
    exit 2
}
remote=$("$GIT_BIN" -C "$PROJECT_ROOT" config --get "branch.$branch.remote") || {
    echo "The current branch has no configured remote." >&2
    exit 2
}
case "$remote" in
    ""|-*|*[!A-Za-z0-9._-]*)
        echo "The configured Git remote name is not supported." >&2
        exit 2
        ;;
esac
merge_ref=$("$GIT_BIN" -C "$PROJECT_ROOT" config --get "branch.$branch.merge") || {
    echo "The current branch has no configured merge ref." >&2
    exit 2
}
case "$merge_ref" in
    refs/heads/*) ;;
    *)
        echo "The configured upstream merge ref is not a branch." >&2
        exit 2
        ;;
esac
case "$upstream_ref" in
    refs/remotes/"$remote"/*) ;;
    *)
        echo "The configured upstream is not a supported remote-tracking branch." >&2
        exit 2
        ;;
esac

current_commit=$("$GIT_BIN" -C "$PROJECT_ROOT" rev-parse --verify 'HEAD^{commit}')
echo "Fetching $upstream for branch $branch."
"$GIT_BIN" -C "$PROJECT_ROOT" fetch --prune "$remote" \
    "+$merge_ref:$upstream_ref"
target_commit=$("$GIT_BIN" -C "$PROJECT_ROOT" rev-parse --verify '@{upstream}^{commit}')

if ! "$GIT_BIN" -C "$PROJECT_ROOT" merge-base --is-ancestor \
    "$current_commit" "$target_commit"; then
    echo "The upstream update is not a fast-forward. Review the branch history manually." >&2
    exit 2
fi

if [ "$current_commit" != "$target_commit" ]; then
    "$GIT_BIN" -C "$PROJECT_ROOT" merge --ff-only "$target_commit"
fi
checked_out_commit=$("$GIT_BIN" -C "$PROJECT_ROOT" rev-parse --verify 'HEAD^{commit}')
if [ "$checked_out_commit" != "$target_commit" ]; then
    echo "The checkout changed during the update. Retry from a stable worktree." >&2
    exit 2
fi
working_changes=$("$GIT_BIN" -C "$PROJECT_ROOT" status --porcelain --untracked-files=all)
if [ -n "$working_changes" ]; then
    echo "The Git worktree changed during the update. Retry after resolving local changes." >&2
    exit 2
fi

cleanup() {
    if [ -n "$SOURCE_ARCHIVE" ]; then
        rm -f -- "$SOURCE_ARCHIVE"
    fi
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

SOURCE_ARCHIVE=$(mktemp "${TMPDIR:-/tmp}/arena-hero-update.XXXXXX.tar")
chmod 0600 "$SOURCE_ARCHIVE"
"$GIT_BIN" -C "$PROJECT_ROOT" archive --format=tar \
    --output "$SOURCE_ARCHIVE" "$target_commit"
deployed_commit=$("$GIT_BIN" -C "$PROJECT_ROOT" rev-parse --short=12 "$target_commit")
echo "Deploying source commit $deployed_commit."

INSTALL_ARCHIVE_COMMAND='
set -eu
archive=$1
commit=$2
case "$commit" in
    ""|*[!0-9a-fA-F]*) echo "Invalid archived Git commit: $commit" >&2; exit 2 ;;
esac
stage=$(mktemp -d /var/tmp/arena-hero-update.XXXXXX)
case "$stage" in
    /var/tmp/arena-hero-update.*) ;;
    *) echo "Unsafe privileged staging path: $stage" >&2; exit 2 ;;
esac
cleanup_stage() {
    rm -rf -- "$stage"
}
trap cleanup_stage EXIT
trap "exit 1" HUP INT TERM
tar -xf "$archive" -C "$stage"
if [ ! -r "$stage/scripts/install-systemd.sh" ]; then
    echo "The archived commit does not contain the systemd installer." >&2
    exit 2
fi
ARENA_HERO_API_KEY= ARENA_SOURCE_COMMIT="$commit" \
    sh "$stage/scripts/install-systemd.sh"
'

run_installer() {
    if [ "$current_uid" -eq 0 ]; then
        ARENA_HERO_API_KEY= \
        ARENA_PIP_INDEX_URL="$PIP_INDEX_URL_OVERRIDE" \
            sh -c "$INSTALL_ARCHIVE_COMMAND" \
            arena-hero-update "$SOURCE_ARCHIVE" "$target_commit"
    else
        require_command "$SUDO_BIN"
        "$SUDO_BIN" env \
            ARENA_HERO_API_KEY= \
            ARENA_PIP_INDEX_URL="$PIP_INDEX_URL_OVERRIDE" \
            sh -c "$INSTALL_ARCHIVE_COMMAND" \
            arena-hero-update "$SOURCE_ARCHIVE" "$target_commit"
    fi
}

if run_installer; then
    echo "Arena Hero Agent updated to $deployed_commit and the new strategy is running."
    echo "The systemd restart stopped any previous strategy process before starting this version."
    echo "Follow logs with: sudo journalctl -fu arena-hero-agent.service -o short-iso-precise"
else
    status=$?
    echo "Update deployment failed with exit code $status. Review the installer diagnostics, current/previous release links, and systemd status." >&2
    exit "$status"
fi
