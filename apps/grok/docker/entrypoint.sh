#!/usr/bin/env bash
set -euo pipefail

cd /app

# Prefer env overrides for container networking
export DISPLAY="${DISPLAY:-:99}"
export PORT="${PORT:-18425}"
DISPLAY_NUM="${DISPLAY#:}"

cleanup_on_exit() {
  # Best-effort: kill leftover browsers and reap; parent exiting also reaps zombies.
  pkill -9 -f 'chromium|chrome|chrome_crashpad' 2>/dev/null || true
  # clear common temp leftovers
  rm -rf /tmp/grok-register-chrome /tmp/DrissionPage /tmp/org.chromium.* 2>/dev/null || true
  rm -f "/tmp/.X${DISPLAY_NUM}-lock" "/tmp/.X11-unix/X${DISPLAY_NUM}" 2>/dev/null || true
}
trap cleanup_on_exit EXIT INT TERM

# Clear stale Xvfb locks from previous container generation
rm -f "/tmp/.X${DISPLAY_NUM}-lock" "/tmp/.X11-unix/X${DISPLAY_NUM}" 2>/dev/null || true
# Also sweep common display locks left by older images (e.g. :98)
rm -f /tmp/.X[0-9]*-lock 2>/dev/null || true

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
    # Use python -c so PID is python (still may be PID1); reaper thread handles zombies.
    exec python - <<'PY'
import os
import web_app
from browser_session import start_background_reaper

host = os.getenv("HOST", "0.0.0.0")
port = int(os.getenv("PORT", "18425"))
try:
    start_background_reaper(interval_sec=45.0)
except Exception as exc:
    print(f"[entrypoint] reaper start failed: {exc}", flush=True)
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
