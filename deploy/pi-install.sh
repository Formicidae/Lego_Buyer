#!/usr/bin/env bash
# Lego Buyer on a Raspberry Pi (Raspberry Pi OS Bookworm or newer, 32- or 64-bit, 1 GB RAM or more).
#
#   curl -fsSL https://raw.githubusercontent.com/Formicidae/Lego_Buyer/main/deploy/pi-install.sh -o pi-install.sh
#   bash pi-install.sh
#
# Installs the app as a system service on port 8000, a nightly database backup, and (optionally)
# Tailscale Funnel so phones can reach it from anywhere over HTTPS. Safe to re-run.
set -euo pipefail

REPO="https://github.com/Formicidae/Lego_Buyer.git"
APP_DIR="/opt/lego-buyer"
DATA_DIR="/var/lib/lego-buyer"
ENV_FILE="/etc/lego-buyer.env"
SERVICE="lego-buyer"
PORT=8000
RUN_USER="${SUDO_USER:-$(whoami)}"

say() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }
ask() { local v; read -r -p "$1" v < /dev/tty; echo "$v"; }

if [ "$(id -u)" -eq 0 ]; then echo "Run this as your normal user (it will sudo when needed), not as root."; exit 1; fi

say "System packages"
sudo apt-get update -qq
CHROMIUM_PKG=chromium
apt-cache show chromium >/dev/null 2>&1 || CHROMIUM_PKG=chromium-browser
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq git python3-venv python3-pip sqlite3 "$CHROMIUM_PKG" fonts-dejavu-core
CHROMIUM_BIN="$(command -v chromium || command -v chromium-browser)"

MEM_MB=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
echo "Detected $(uname -m), ${MEM_MB} MB RAM, chromium at ${CHROMIUM_BIN}"
if [ "$MEM_MB" -lt 1500 ] && [ -f /etc/dphys-swapfile ]; then
  say "Low memory: enlarging swap to 1 GB so PDF rendering has headroom"
  sudo sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=1024/' /etc/dphys-swapfile
  sudo systemctl restart dphys-swapfile || true
fi

say "Application code -> $APP_DIR"
if [ -d "$APP_DIR/.git" ]; then
  sudo -u "$RUN_USER" git -C "$APP_DIR" pull --ff-only
else
  sudo mkdir -p "$APP_DIR" && sudo chown "$RUN_USER" "$APP_DIR"
  git clone "$REPO" "$APP_DIR"
fi
cd "$APP_DIR"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

sudo mkdir -p "$DATA_DIR/backups" && sudo chown -R "$RUN_USER" "$DATA_DIR"

if [ ! -f "$ENV_FILE" ]; then
  say "Configuration (stored in $ENV_FILE, readable only by root and the service)"
  RB=$(ask "Rebrickable API key: ")
  BO=$(ask "BrickOwl API key (Enter to skip for now): ")
  PC=$(ask "Site passcode (what you and your wife type on your phones): ")
  SECRET=$(head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 40)
  sudo tee "$ENV_FILE" >/dev/null <<EOF
REBRICKABLE_API_KEY=$RB
BRICKOWL_API_KEY=$BO
APP_PASSCODE=$PC
APP_SECRET=$SECRET
DATA_DIR=$DATA_DIR
CHROMIUM_PATH=$CHROMIUM_BIN
PORT=$PORT
EOF
  sudo chmod 600 "$ENV_FILE"
else
  echo "Keeping existing $ENV_FILE (edit it with: sudo nano $ENV_FILE, then: sudo systemctl restart $SERVICE)"
  grep -q '^CHROMIUM_PATH=' "$ENV_FILE" || echo "CHROMIUM_PATH=$CHROMIUM_BIN" | sudo tee -a "$ENV_FILE" >/dev/null
fi

say "Service"
sudo tee /etc/systemd/system/$SERVICE.service >/dev/null <<EOF
[Unit]
Description=Lego Buyer
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$ENV_FILE
ExecStart=$APP_DIR/.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips=*
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now $SERVICE
sleep 2
sudo systemctl restart $SERVICE
sleep 2
if curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null; then
  echo "Service is up: $(curl -fsS http://127.0.0.1:$PORT/health)"
else
  echo "Service failed to start. Logs:"; sudo journalctl -u $SERVICE -n 30 --no-pager; exit 1
fi

say "Nightly backup (keeps 30 days in $DATA_DIR/backups)"
sudo tee /etc/cron.daily/lego-buyer-backup >/dev/null <<EOF
#!/bin/sh
DB=$DATA_DIR/lego_buyer.sqlite3
[ -f "\$DB" ] || exit 0
sqlite3 "\$DB" ".backup '$DATA_DIR/backups/lego_buyer-\$(date +%F).sqlite3'"
find $DATA_DIR/backups -name 'lego_buyer-*.sqlite3' -mtime +30 -delete
EOF
sudo chmod +x /etc/cron.daily/lego-buyer-backup

LAN_IP=$(hostname -I | awk '{print $1}')
say "Lego Buyer is running on your home network at http://$LAN_IP:$PORT  (also http://$(hostname).local:$PORT)"

if [ "$(ask 'Set up Tailscale Funnel so phones can reach it away from home? [Y/n] ')" != "n" ]; then
  say "Tailscale"
  command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
  echo "If a login link appears below, open it on your laptop and sign in (Google/GitHub/Microsoft all work)."
  sudo tailscale up
  echo "Turning on Funnel (public HTTPS). If Tailscale prints a link to enable HTTPS certificates or Funnel"
  echo "for your account, open it, click Enable, then re-run this script."
  sudo tailscale funnel --bg "$PORT"
  FQDN=$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
  say "Done. Open https://$FQDN on any phone, enter the passcode, and add it to the home screen."
else
  say "Done. Re-run this script any time to add Tailscale later."
fi
echo
echo "Update later with:  bash $APP_DIR/deploy/update.sh"
echo "Logs:               sudo journalctl -u $SERVICE -f"
