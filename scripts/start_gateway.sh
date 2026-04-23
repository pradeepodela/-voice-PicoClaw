#!/usr/bin/env bash
# Safe picoclaw gateway launcher — exits immediately if one is already running.
set -euo pipefail

BINARY="${PICOCLAW_BINARY:-picoclaw}"

if pgrep -x "$BINARY" > /dev/null 2>&1; then
    echo "[gateway] picoclaw gateway is already running ($(pgrep -x "$BINARY" | tr '\n' ' '))"
    echo "[gateway] Not starting a second instance — Telegram 409 conflicts would occur."
    exit 1
fi

echo "[gateway] Starting picoclaw gateway..."
exec "$BINARY" gateway "$@"
