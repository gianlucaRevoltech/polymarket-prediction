#!/usr/bin/env bash
# Installa unit systemd per bot + dashboard (Restart=always, avvio al boot).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
USER_NAME="${SUDO_USER:-$(whoami)}"
PROJECT_DIR="${PROJECT_DIR:-$ROOT}"

if [ "$(id -u)" -ne 0 ]; then
  echo "[ERRORE] Esegui con sudo: sudo ./scripts/install_systemd.sh"
  exit 1
fi

if [ ! -x "$PROJECT_DIR/venv/bin/python" ]; then
  echo "[ERRORE] Virtualenv non trovato in $PROJECT_DIR/venv — esegui prima ./start_all.sh install"
  exit 1
fi

mkdir -p "$PROJECT_DIR/logs"

render_unit() {
  local src="$1" dst="$2"
  sed -e "s|__USER__|$USER_NAME|g" -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$src" > "$dst"
}

echo "[SYSTEMD] Installo unit in /etc/systemd/system/ ..."
render_unit "$ROOT/deploy/systemd/polymarket-bot.service" /etc/systemd/system/polymarket-bot.service
render_unit "$ROOT/deploy/systemd/polymarket-dashboard.service" /etc/systemd/system/polymarket-dashboard.service

systemctl daemon-reload
systemctl enable polymarket-bot polymarket-dashboard

echo "[SYSTEMD] Fermo eventuali istanze nohup/screen (start_all.sh) ..."
if [ -x "$PROJECT_DIR/start_all.sh" ]; then
  "$PROJECT_DIR/start_all.sh" stop || true
fi
pkill -f "src/main.py" 2>/dev/null || true
pkill -f "src/dashboard.py" 2>/dev/null || true
rm -f "$PROJECT_DIR/data/bot.pid" "$PROJECT_DIR/data/dashboard.pid"

echo "[SYSTEMD] Avvio servizi ..."
systemctl restart polymarket-bot
systemctl restart polymarket-dashboard

sleep 2
systemctl --no-pager status polymarket-bot || true
systemctl --no-pager status polymarket-dashboard || true
echo "[SYSTEMD] Fatto. Log: journalctl -u polymarket-bot -f"
