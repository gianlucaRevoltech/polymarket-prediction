#!/usr/bin/env bash
#
# Polymarket Paper Trading Bot - gestione servizi (Linux / VPS)
#
# Uso:
#   ./start_all.sh [start|stop|restart|install|reset|status|logs|scan] [scan] [reset]
#
#   start    (default) installa deps se serve, ferma istanze precedenti e avvia
#   start scan|reset  forza scan wallet e/o azzera storico prima dell'avvio
#   scan     aggiorna data/scan_results.json (wallet specialisti per categoria)
#   stop     ferma bot + dashboard (via PID file, con fallback)
#   restart  stop + (chiede se azzerare storico) + start
#   restart reset|scan  riavvio con opzioni esplicite (no prompt se reset)
#   install  crea/aggiorna virtualenv e installa requirements
#   reset    ferma tutto e azzera lo stato della simulazione (senza riavviare)
#   status   mostra stato dei servizi
#   logs     segue i log in tempo reale
#
#   Env: SCAN=1 forza scan | RESET=1 azzera storico senza prompt
#
#   Deploy VPS (dopo git pull / copia file):
#     ./start_all.sh restart reset scan
#   Poi il bot aggiorna da solo la lista wallet ogni 6h — niente cron manuale.
#
set -euo pipefail
cd "$(dirname "$0")"

VENV_DIR="venv"
DATA_DIR="data"
LOGS_DIR="logs"
PORT="${PORT:-5000}"
SCAN_TOP="${SCAN_TOP:-20}"
SCAN_RESULTS="$DATA_DIR/scan_results.json"

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

run_wallet_scan() {
  ensure_venv
  mkdir -p "$DATA_DIR" "$LOGS_DIR"
  echo "[SCAN] Wallet specialisti per categoria (top $SCAN_TOP) ..."
  echo "[SCAN] Output anche in $LOGS_DIR/scan_categories.log"
  "$(venv_py)" -u src/scanner.py --mode categories --top "$SCAN_TOP" \
    | tee "$LOGS_DIR/scan_categories.log"
  if [ ! -f "$SCAN_RESULTS" ]; then
    echo "[ERRORE] Scanner completato ma $SCAN_RESULTS non trovato."
    exit 1
  fi
  echo "[SCAN] Fatto: $(grep -c '"address"' "$SCAN_RESULTS" 2>/dev/null || echo 0) wallet in $SCAN_RESULTS"
}

ensure_wallet_scan() {
  local force="${1:-0}"
  if [ "$force" = "1" ] || [ ! -f "$SCAN_RESULTS" ]; then
    if [ ! -f "$SCAN_RESULTS" ]; then
      echo "[SCAN] $SCAN_RESULTS assente: avvio scanner automatico ..."
    else
      echo "[SCAN] Refresh forzato ..."
    fi
    run_wallet_scan
  else
    echo "[SCAN] $SCAN_RESULTS presente (salto; usa './start_all.sh scan' per aggiornare)"
  fi
}

start_services() {
  local force_scan="${1:-0}"
  local reset_flag="${2:-0}"
  if [ "$reset_flag" = "1" ]; then
    clear_trading_state
  fi
  ensure_venv
  mkdir -p "$DATA_DIR" "$LOGS_DIR"
  ensure_wallet_scan "$force_scan"
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
  clear_trading_state
}

clear_trading_state() {
  echo "[RESET] Azzero lo stato della simulazione ..."
  rm -f "$DATA_DIR/portfolio_state.json" "$DATA_DIR/trades_log.json" "$DATA_DIR/equity_curve.json"
  echo "[RESET] Stato azzerato (scan_results.json mantenuto)."
}

has_trading_history() {
  [ -f "$DATA_DIR/portfolio_state.json" ] \
    || [ -f "$DATA_DIR/trades_log.json" ] \
    || [ -f "$DATA_DIR/equity_curve.json" ]
}

prompt_clear_history() {
  local reset_flag="${1:-0}"
  if [ "$reset_flag" = "1" ]; then
    clear_trading_state
    return
  fi
  if ! has_trading_history; then
    echo "[INFO] Nessuno storico da azzerare."
    return
  fi
  if [ -t 0 ]; then
    echo ""
    echo "Trovato storico operazioni (portfolio, trade, equity)."
    read -r -p "Azzerare lo storico e ripartire da budget iniziale? [s/N] " ans
    case "$ans" in
      s|S|si|Si|SI|y|Y|yes|Yes) clear_trading_state ;;
      *) echo "[INFO] Storico mantenuto." ;;
    esac
  else
    echo "[INFO] Storico mantenuto (non interattivo: usa 'restart reset' o RESET=1)."
  fi
}

restart_services() {
  local force_scan="${1:-0}"
  local reset_flag="${2:-0}"
  stop_services
  prompt_clear_history "$reset_flag"
  start_services "$force_scan"
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
shift $(( $# > 0 ? 1 : 0 )) || true

FORCE_SCAN_FLAG=0
RESET_FLAG=0
for arg in "$@"; do
  case "$arg" in
    scan) FORCE_SCAN_FLAG=1 ;;
    reset|fresh) RESET_FLAG=1 ;;
    *)
      echo "Opzione sconosciuta: $arg"
      echo "Uso: $0 [start|stop|restart|install|reset|status|logs|scan] [scan] [reset]"
      exit 1
      ;;
  esac
done
[ "${SCAN:-0}" = "1" ] || [ "${FORCE_SCAN:-0}" = "1" ] && FORCE_SCAN_FLAG=1
[ "${RESET:-0}" = "1" ] && RESET_FLAG=1

case "$ACTION" in
  start)   start_services "$FORCE_SCAN_FLAG" "$RESET_FLAG" ;;
  stop)    stop_services ;;
  restart) restart_services "$FORCE_SCAN_FLAG" "$RESET_FLAG" ;;
  install) ensure_venv; install_deps; run_wallet_scan ;;
  scan)    run_wallet_scan ;;
  reset)   reset_state ;;
  status)  show_status ;;
  logs)    tail -f "$LOGS_DIR/bot.log" "$LOGS_DIR/dashboard.log" ;;
  *) echo "Uso: $0 [start|stop|restart|install|reset|status|logs|scan] [scan] [reset]"; exit 1 ;;
esac
