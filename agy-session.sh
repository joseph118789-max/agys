#!/usr/bin/env bash
# agy-session.sh — Antigravity Session & Conversation Manager
# Browse, inspect, move, and copy sessions across AGY homes.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Error: python3 is required for agy-session.sh but is not installed or not in PATH." >&2
    exit 1
fi

exec python3 "${SCRIPT_DIR}/agy_session.py" "$@"
