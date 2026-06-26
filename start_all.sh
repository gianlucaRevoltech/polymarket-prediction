#!/usr/bin/env bash
#
# Polymarket Paper Trading Bot - gestione servizi (Linux / VPS)
#
# Uso:
#   ./start_all.sh [start|stop|restart|install|reset|status|logs]
#
#   start    (default) installa deps se serve, ferma istanze precedenti e avvia
#   stop     ferma bot + dashboard (via PID file, con fallback)
#   restart  stop + start
#   install  crea/aggiorna virtualenv e installa requirements
#   reset    ferma tutto e azzera lo stato della simulazione
#   status   mostra stato dei servizi
#   logs     segue i log in tempo reale
#
set -euo pipefail
cd "$(dirname "$0")"

VENV_DIR="venv"
DATA_DIR="data"
LOGS_DIR="logs"
PORT="${PORT:-5000}"

# --- Individua interprete Python di sistema (per creare il venv) ---------------
SYS_PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then SYS_PY="$c"; break; fi
done

venv_py() { echo "$VENV_DIR/bin/python"; }

ensure_venv() {
  if [ ! -x "$(venv_py)" ]; then
    [ -n "$SYS_PY" ] || { echo "[ERRORE] Python non trovato nel PATH."; exit 1; }
    echo "[SETUP] Creo virtualenv in $VENV_DIR ..."
    "$SYS_PY" -m venv "$VENV_DIR"
    install_deps
  fi
}

install_deps() {
  echo "[SETUP] Installo/aggiorno dipendenze ..."
  "$(venv_py)" -m pip install --upgrade pip >/dev/null
  "$(venv_py)" -m pip install -r requirements.txt
}

kill_pidfile() {
  local pidfile="$1"
  if [ -f "$pidfile" ]; then
    local pid
    pid="$(cat "$pidfile" 2>/dev/null || true)"
    if [ -n "${pid:-}" ] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
      sleep 1
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$pidfile"
  fi
}

stop_services() {
  echo "[STOP] Arresto servizi ..."
  kill_pidfile "$DATA_DIR/bot.pid"
  kill_pidfile "$DATA_DIR/dashboard.pid"
  # Fallback mirato (non tocca altri processi Python)
  pkill -f "src/main.py" 2>/dev/null || true
  pkill -f "src/dashboard.py" 2>/dev/null || true
  echo "[STOP] Fatto."
}

start_services() {
  ensure_venv
  mkdir -p "$DATA_DIR" "$LOGS_DIR"
  stop_services

  echo "[START] Dashboard su http://localhost:$PORT ..."
  PORT="$PORT" nohup "$(venv_py)" -u src/dashboard.py >"$LOGS_DIR/dashboard.log" 2>&1 &
  sleep 2

  echo "[START] Bot (mirroring copy/consenso) ..."
  nohup "$(venv_py)" -u src/main.py >"$LOGS_DIR/bot.log" 2>&1 &
  sleep 3

  show_status
  echo ""
  echo "Log live:  ./start_all.sh logs"
  echo "Stop:      ./start_all.sh stop"
}

reset_state() {
  stop_services
  echo "[RESET] Azzero lo stato della simulazione ..."
  rm -f "$DATA_DIR/portfolio_state.json" "$DATA_DIR/trades_log.json" "$DATA_DIR/equity_curve.json"
  echo "[RESET] Stato azzerato (scan_results.json mantenuto)."
}

show_status() {
  echo ""
  echo "=================== STATO ==================="
  for svc in bot dashboard; do
    pidfile="$DATA_DIR/$svc.pid"
    if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile" 2>/dev/null)" 2>/dev/null; then
      echo "  $svc: IN ESECUZIONE (PID $(cat "$pidfile"))"
    else
      echo "  $svc: fermo"
    fi
  done
  echo "  Dashboard: http://localhost:$PORT"
  echo "============================================="
}

ACTION="${1:-start}"
case "$ACTION" in
  start)   start_services ;;
  stop)    stop_services ;;
  restart) stop_services; start_services ;;
  install) ensure_venv; install_deps ;;
  reset)   reset_state ;;
  status)  show_status ;;
  logs)    tail -f "$LOGS_DIR/bot.log" "$LOGS_DIR/dashboard.log" ;;
  *) echo "Uso: $0 [start|stop|restart|install|reset|status|logs]"; exit 1 ;;
esac
