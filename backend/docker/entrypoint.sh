#!/bin/sh
# Container entrypoint: virtual display, optional remote browser (noVNC), then the API.
set -eu

# First boot: seed the editable profile from the example.
if [ ! -f "$JOBHUB_PROFILE" ]; then
    cp /app/profile.example.yaml "$JOBHUB_PROFILE"
    echo "entrypoint: created $JOBHUB_PROFILE from profile.example.yaml - edit it in the app (Settings > Profile)"
fi
chmod 700 /app/.secrets 2>/dev/null || true

# Xvfb gives Chromium a screen even for "visible" runs (login_platform, headless=false).
Xvfb "$DISPLAY" -screen 0 1280x900x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
sleep 1
fluxbox >/tmp/fluxbox.log 2>&1 &

# Remote browser for logging in to job sites from the phone. Only reachable through Caddy (/browser/*, basic auth).
if [ -n "${JOBHUB_VNC_PASSWORD:-}" ]; then
    x11vnc -storepasswd "$JOBHUB_VNC_PASSWORD" /tmp/vncpass >/dev/null 2>&1
    x11vnc -display "$DISPLAY" -rfbauth /tmp/vncpass -forever -shared -localhost -rfbport 5900 -quiet >/tmp/x11vnc.log 2>&1 &
    websockify --web /usr/share/novnc 6080 127.0.0.1:5900 >/tmp/novnc.log 2>&1 &
    echo "entrypoint: remote browser on :6080 (proxied at /browser/)"
fi

# Moli headless CDP engine (~40MB RAM vs Chromium ~400MB)
if command -v moli >/dev/null 2>&1; then
    moli serve --port 9222 --layout --resource >/tmp/moli.log 2>&1 &
    echo "entrypoint: moli CDP server running on 127.0.0.1:9222"
fi

exec uvicorn api:create_app --factory --app-dir /app/hub --host 0.0.0.0 --port 8000 \
    --proxy-headers --forwarded-allow-ips='*' --workers 1
