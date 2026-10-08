#!/usr/bin/env bash
# Pull the latest code and restart the service.  Usage: bash /opt/lego-buyer/deploy/update.sh
set -euo pipefail
cd /opt/lego-buyer
git pull --ff-only
.venv/bin/pip install -q -r requirements.txt
sudo systemctl restart lego-buyer
sleep 2
curl -fsS http://127.0.0.1:8000/health && echo && echo "Updated: $(git log -1 --format='%h %s')"
