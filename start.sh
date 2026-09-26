#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
. .venv/bin/activate
pip install -q -e ".[mcp,dev]"
export HASSAN_AI_MODE="${HASSAN_AI_MODE:-mock}"
export HASSAN_ALLOWED_ROOTS="${HASSAN_ALLOWED_ROOTS:-$HOME}"
echo "[Hassan AI OS] Mode=$HASSAN_AI_MODE  -  http://127.0.0.1:8787"
exec python -m hassan_ai
