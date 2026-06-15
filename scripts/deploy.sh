#!/usr/bin/env bash
# Deploy the home gateway to an Incus container (mirrors the unifi-gate pattern).
# Usage: ./scripts/deploy.sh [container_name]
# Idempotent: creates the container on first run, then pushes code + restarts.
set -euo pipefail

CONTAINER="${1:-home}"
PROJ="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE=/opt/home
INSTANCE="${HOME}/development/home-instance"

echo "==> Deploying home to container: $CONTAINER"

# 1. Create the container on first run
if ! incus info "$CONTAINER" >/dev/null 2>&1; then
  echo "==> Creating container (ubuntu/24.04)..."
  incus launch images:ubuntu/24.04 "$CONTAINER"
  incus config set "$CONTAINER" boot.autostart true
  echo "    waiting for network..."
  for _ in $(seq 1 30); do
    incus exec "$CONTAINER" -- getent hosts archive.ubuntu.com >/dev/null 2>&1 && break
    sleep 1
  done
  echo "==> Installing python..."
  incus exec "$CONTAINER" -- apt-get update -qq
  incus exec "$CONTAINER" -- apt-get install -y -qq python3 python3-venv >/dev/null
  incus exec "$CONTAINER" -- mkdir -p "$REMOTE"
  incus exec "$CONTAINER" -- python3 -m venv "$REMOTE/.venv"
fi

# 2. Push application code (replace homed/ + static/ wholesale)
echo "==> Pushing code..."
incus exec "$CONTAINER" -- rm -rf "$REMOTE/homed" "$REMOTE/static"
incus file push -qr "$PROJ/homed"  "$CONTAINER$REMOTE/"
incus file push -qr "$PROJ/static" "$CONTAINER$REMOTE/"
incus file push -q  "$PROJ/requirements.txt" "$CONTAINER$REMOTE/requirements.txt"

# 3. Dependencies
echo "==> Installing dependencies..."
incus exec "$CONTAINER" -- bash -c "cd $REMOTE && .venv/bin/pip install -q -r requirements.txt"

# 4. Config — only install if absent (never clobber a tuned deployment)
if ! incus exec "$CONTAINER" -- test -f "$REMOTE/home.toml"; then
  echo "==> Installing home.toml from instance config..."
  incus file push -q "$INSTANCE/config/home.container.toml" "$CONTAINER$REMOTE/home.toml"
fi

# 5. Systemd service inside the container
echo "==> Syncing service..."
incus exec "$CONTAINER" -- tee /etc/systemd/system/home.service >/dev/null <<EOF
[Unit]
Description=Home control gateway
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$REMOTE
ExecStart=$REMOTE/.venv/bin/python -m homed --config $REMOTE/home.toml
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
incus exec "$CONTAINER" -- systemctl daemon-reload
incus exec "$CONTAINER" -- systemctl enable -q home
incus exec "$CONTAINER" -- systemctl restart home

# 6. Verify
sleep 3
echo "==> Service: $(incus exec "$CONTAINER" -- systemctl is-active home)"
incus exec "$CONTAINER" -- curl -s -o /dev/null -w "    container /api/home -> HTTP %{http_code}\n" --max-time 6 http://localhost:8099/api/home || true
IP=$(incus list "$CONTAINER" --format csv -c 4 | cut -d' ' -f1)
echo "==> Deploy complete. Container IP: $IP (service on :8099)"
