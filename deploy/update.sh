#!/usr/bin/env bash
# Pull the latest code and restart the service. No sudo needed: the service runs as you and
# systemd (Restart=always) brings it back after we stop it.
set -euo pipefail
cd /opt/lego-buyer
git pull --ff-only
.venv/bin/pip install -q -r requirements.txt
pkill -f "uvicorn app.main:app" || true
for i in $(seq 1 20); do sleep 1; curl -fsS http://127.0.0.1:8000/health >/dev/null 2>&1 && break; done
curl -fsS http://127.0.0.1:8000/health && echo && echo "Updated: $(git log -1 --format='%h %s')"
