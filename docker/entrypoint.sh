#!/bin/sh
set -eu

if [ "${1:-}" != "arena-hero-agent" ]; then
    exec "$@"
fi

if [ -n "${ARENA_HERO_API_KEY_FILE:-}" ]; then
    if [ ! -r "$ARENA_HERO_API_KEY_FILE" ]; then
        echo "Arena Hero API key secret is not readable." >&2
        exit 2
    fi
    ARENA_HERO_API_KEY=$(tr -d '\r\n' < "$ARENA_HERO_API_KEY_FILE")
    export ARENA_HERO_API_KEY
fi

if [ -z "${ARENA_HERO_API_KEY:-}" ]; then
    echo "ARENA_HERO_API_KEY or ARENA_HERO_API_KEY_FILE is required." >&2
    exit 2
fi

case "$ARENA_HERO_API_KEY" in
    replace-with-*|your-*|\<*)
        echo "Replace the placeholder Arena Hero API key before starting." >&2
        exit 2
        ;;
esac

exec "$@"
