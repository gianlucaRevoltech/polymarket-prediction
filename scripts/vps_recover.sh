#!/usr/bin/env bash
# Ripristino completo VPS dopo reset/crash/deploy parziale.
# Uso: ./scripts/vps_recover.sh [--systemd]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
USE_SYSTEMD=0

for arg in "$@"; do
  case "$arg" in
    --systemd) USE_SYSTEMD=1 ;;
    -h|--help)
      echo "Uso: $0 [--systemd]"
      echo "  Aggiorna codice, riavvia con scan (senza reset), opzionale systemd."
      exit 0
      ;;
    *) echo "Opzione sconosciuta: $arg"; exit 1 ;;
  esac
done

echo "========== VPS RECOVER =========="
echo "[1/5] Diagnostica pre-ripristino"
./start_all.sh status || true
ls -la data/ 2>/dev/null || mkdir -p data
echo "--- tail bot.log ---"
tail -20 logs/bot.log 2>/dev/null || echo "(nessun log)"

echo "[2/5] Aggiornamento codice (git pull)"
if [ -d .git ]; then
  git pull origin main
else
  echo "[WARN] Non è un repo git — salto pull (assicurati codice aggiornato)"
fi

chmod +x start_all.sh scripts/*.sh 2>/dev/null || true

echo "[3/5] Dipendenze"
./start_all.sh install

if [ "$USE_SYSTEMD" = "1" ]; then
  echo "[4/5] Riavvio via systemd (Restart=always)"
  sudo PROJECT_DIR="$ROOT" ./scripts/install_systemd.sh
else
  echo "[4/5] Riavvio servizi (senza reset, con scan)"
  ./start_all.sh restart scan
fi

echo "[5/5] Verifica post-ripristino"
sleep 3
./start_all.sh status || true
WALLETS=$(grep -c '"address"' data/scan_results.json 2>/dev/null || echo 0)
echo "Wallet in scan_results.json: $WALLETS"
curl -sf "http://127.0.0.1:${PORT:-5000}/api/status" | python3 -c "
import sys, json
d = json.load(sys.stdin)
s = d.get('summary', {})
print('bot_status:', d.get('bot_status'))
print('error:', d.get('error'))
print('wallets:', len(d.get('monitored_wallets', [])))
print('trailing:', s.get('trailing_stop_enabled'))
print('strategies:', len(s.get('active_strategies', [])))
" 2>/dev/null || echo "[WARN] API status non raggiungibile"
echo "========== FINE RECOVER =========="
