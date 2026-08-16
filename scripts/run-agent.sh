#!/bin/sh
set -u

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(dirname "$SCRIPT_DIR")
AGENT="$PROJECT_ROOT/.venv/bin/arena-hero-agent"
ENV_FILE=${ARENA_HERO_ENV_FILE:-$PROJECT_ROOT/.env}
WORKER_TARGET=${ARENA_WORKER_TARGET:-18}
BEACON_POLICY=${ARENA_BEACON_POLICY:-retreat}
RETRY_DELAY=2

if [ ! -x "$AGENT" ]; then
    echo "Virtual environment is missing. Run ./scripts/bootstrap.sh first." >&2
    exit 2
fi

while :; do
    "$AGENT" \
        --env-file "$ENV_FILE" \
        --worker-target "$WORKER_TARGET" \
        --beacon-policy "$BEACON_POLICY" \
        --no-compatibility-marker \
        "$@"
    EXIT_CODE=$?
    if [ "$EXIT_CODE" -ne 75 ]; then
        exit "$EXIT_CODE"
    fi
    echo "Transient Agent failure. Restarting in ${RETRY_DELAY}s." >&2
    sleep "$RETRY_DELAY"
    if [ "$RETRY_DELAY" -lt 30 ]; then
        RETRY_DELAY=$((RETRY_DELAY * 2))
        if [ "$RETRY_DELAY" -gt 30 ]; then
            RETRY_DELAY=30
        fi
    fi
done
