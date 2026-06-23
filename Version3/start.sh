#!/usr/bin/env bash
# Install deps and launch the magic-pigeon web UI (single Flask process).
set -e

cd "$(dirname "$0")"

PY="${PYTHON:-python3}"

if [ -z "$ANTHROPIC_API_KEY" ]; then
  echo "ERROR: ANTHROPIC_API_KEY is not set."
  echo "  export ANTHROPIC_API_KEY=\"sk-ant-...\"  then re-run."
  exit 1
fi

echo "[1/2] Installing Python dependencies…"
"$PY" -m pip install --quiet --upgrade -r requirements.txt

PORT="${PORT:-5001}"
echo "[2/2] Starting server on http://127.0.0.1:${PORT}"
echo "      (frontend is served by Flask — no separate frontend server)"
PORT="$PORT" "$PY" server.py
