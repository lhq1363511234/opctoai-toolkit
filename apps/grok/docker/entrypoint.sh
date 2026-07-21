#!/usr/bin/env bash
set -euo pipefail

cd /app

# Prefer env overrides for container networking
export DISPLAY="${DISPLAY:-:99}"
export PORT="${PORT:-18425}"

# Start virtual display for headed Chromium/DrissionPage flows
if ! pgrep -x Xvfb >/dev/null 2>&1; then
  Xvfb "$DISPLAY" -screen 0 "${SCREEN_WIDTH:-1920}x${SCREEN_HEIGHT:-1080}x${SCREEN_DEPTH:-24}" -ac +extension GLX +render -noreset &
  sleep 0.5
fi

# Ensure config exists (prefer external writable /data)
mkdir -p /data/cpa_auths
if [[ -n "${GROK_CONFIG_FILE:-}" ]]; then
  :
elif [[ -f /data/config.json ]]; then
  export GROK_CONFIG_FILE=/data/config.json
elif [[ -f /app/config.json ]]; then
  export GROK_CONFIG_FILE=/app/config.json
elif [[ -f /app/config.external.example.json ]]; then
  cp /app/config.external.example.json /data/config.json
  export GROK_CONFIG_FILE=/data/config.json
elif [[ -f /app/config.example.json ]]; then
  cp /app/config.example.json /data/config.json
  export GROK_CONFIG_FILE=/data/config.json
fi
export GROK_DATA_DIR="${GROK_DATA_DIR:-/data}"
export GROK_CPA_DIR="${GROK_CPA_DIR:-/data/cpa_auths}"

# Point browser to system chromium when available
if [[ -z "${BROWSER_PATH:-}" ]]; then
  if command -v chromium >/dev/null 2>&1; then
    export BROWSER_PATH="$(command -v chromium)"
  elif command -v chromium-browser >/dev/null 2>&1; then
    export BROWSER_PATH="$(command -v chromium-browser)"
  fi
fi

mode="${1:-web}"
shift || true

case "$mode" in
  web)
    # Bind all interfaces inside container
    exec python - <<'PY'
import os
from pathlib import Path
import web_app

# monkeypatch host/port for container use without editing source permanently
host = os.getenv("HOST", "0.0.0.0")
port = int(os.getenv("PORT", "18425"))
print(f"[entrypoint] starting grok web on {host}:{port}", flush=True)
web_app.app.run(host=host, port=port, threaded=True)
PY
    ;;
  cli)
    exec python register_cli.py "$@"
    ;;
  bash|sh)
    exec /bin/bash "$@"
    ;;
  *)
    exec "$mode" "$@"
    ;;
esac
