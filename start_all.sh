#!/usr/bin/env bash
#
# Polymarket Paper Trading Bot - gestione servizi (Linux / VPS)
#
# Uso:
#   ./start_all.sh [start|stop|restart|new-run|preflight-paper|paper-start|reset|install|status|logs|scan]
#
#   start    (default) installa deps se serve, ferma istanze precedenti e avvia
#   start scan  forza scan wallet prima dell'avvio
#   scan     aggiorna data/scan_results.json (wallet specialisti per categoria)
#   stop     ferma bot + dashboard (via PID file, con fallback)
#   restart  stop + start, conserva sempre tutto lo stato
#   new-run --mode observe scan  crea un run esplicito con coorte congelata
#   preflight-paper  verifica tecnica/safety senza modificare il run
#   paper-start  transizione OBSERVE -> PAPER EXPERIMENTAL con la stessa coorte
#   install  crea/aggiorna virtualenv e installa requirements
#   reset --force  archivia e poi azzera lo stato (senza riavviare)
#   status   mostra stato dei servizi
#   logs     segue i log in tempo reale
#
#   Env: SCAN=1 forza scan | LATENCY_ARB_ENABLED=1 abilita il validator
#
#   Deploy VPS (dopo git pull / copia file):
#     ./start_all.sh restart
#   I wallet restano congelati nel run; aggiornarli solo con `new-run scan`.
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
  kill_pidfile "$DATA_DIR/latency_arb.pid"
  # Fallback mirato (non tocca altri processi Python)
  pkill -f "src/main.py" 2>/dev/null || true
  pkill -f "src/dashboard.py" 2>/dev/null || true
  pkill -f "src/latency_arb.py" 2>/dev/null || true
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
  validate_wallet_scan
}

validate_wallet_scan() {
  local wallet_count minimum_required
  wallet_count="$("$(venv_py)" -c 'import json,pathlib; p=pathlib.Path("data/scan_results.json"); d=json.loads(p.read_text(encoding="utf-8")); print(len({str(w.get("address", "")).lower() for w in d.get("wallets", []) if w.get("address")}))')"
  minimum_required="$(PYTHONPATH=src "$(venv_py)" -c 'from config import EXECUTION; print(int(EXECUTION.get("minimum_monitored_wallets", 5)))')"
  echo "[SCAN] Fatto: $wallet_count wallet in $SCAN_RESULTS (minimo $minimum_required)"
  if [ "$wallet_count" -lt "$minimum_required" ]; then
    echo "[ERRORE] Cohort insufficiente: $wallet_count/$minimum_required wallet."
    echo "         Il run non viene avviato; le soglie qualitative restano invariate."
    return 2
  fi
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
  validate_wallet_scan
}

start_services() {
  local force_scan="${1:-0}"
  ensure_venv
  mkdir -p "$DATA_DIR" "$LOGS_DIR"
  if [ "$force_scan" = "1" ] || [ ! -f "$SCAN_RESULTS" ]; then
    run_wallet_scan
  fi
  PYTHONPATH=src "$(venv_py)" tools/run_state.py ensure-current
  stop_services

  echo "[START] Dashboard su http://localhost:$PORT ..."
  PORT="$PORT" nohup "$(venv_py)" -u src/dashboard.py >"$LOGS_DIR/dashboard.log" 2>&1 &
  sleep 2

  echo "[START] Bot (mirroring copy/consenso) ..."
  nohup "$(venv_py)" -u src/main.py >"$LOGS_DIR/bot.log" 2>&1 &
  sleep 3

  if [ "${LATENCY_ARB_ENABLED:-0}" = "1" ]; then
    echo "[WARNING] LATENCY_ARB_ENABLED ignorata: latency-arb resta in quarantena."
  fi
  echo "[START] Latency-arb: FERMO (quarantena obbligatoria)"

  show_status
  echo ""
  echo "Log live:  ./start_all.sh logs"
  echo "Stop:      ./start_all.sh stop"
}

archive_run() {
  ensure_venv
  PYTHONPATH=src "$(venv_py)" tools/run_state.py archive
}

reset_state() {
  local force="${1:-0}"
  if [ "$force" != "1" ]; then
    echo "[ERRORE] reset richiede --force. Lo stato non è stato modificato."
    exit 2
  fi
  stop_services
  archive_run
  clear_trading_state
}

new_run() {
  local mode="${1:-observe}"
  local force_scan="${2:-0}"
  stop_services
  if [ -f "$DATA_DIR/run_manifest.json" ] || has_trading_history; then
    archive_run
  fi
  clear_trading_state
  if [ "$force_scan" = "1" ] || [ ! -f "$SCAN_RESULTS" ]; then
    run_wallet_scan
  else
    validate_wallet_scan
  fi
  PYTHONPATH=src "$(venv_py)" tools/run_state.py create \
    --mode "$mode" --cohort-file "$SCAN_RESULTS"
  start_services 0
}

clear_trading_state() {
  ensure_venv
  PYTHONPATH=src "$(venv_py)" tools/run_state.py clear --force
}

has_trading_history() {
  [ -f "$DATA_DIR/portfolio_state.json" ] \
    || [ -f "$DATA_DIR/trades_log.json" ] \
    || [ -f "$DATA_DIR/equity_curve.json" ]
}

restart_services() {
  stop_services
  start_services 0
}

preflight_paper() {
  ensure_venv
  PYTHONPATH=src "$(venv_py)" tools/paper_preflight.py
}

paper_start() {
  ensure_venv
  if ! preflight_paper; then
    echo "[BLOCK] Preflight fallito: il run OBSERVE resta invariato."
    return 2
  fi
  local cohort_tmp
  cohort_tmp="$(mktemp)"
  cp "$DATA_DIR/monitored_wallets.json" "$cohort_tmp"
  stop_services
  archive_run
  clear_trading_state
  if ! PYTHONPATH=src "$(venv_py)" tools/run_state.py create \
      --mode paper_validation --cohort-file "$cohort_tmp"; then
    rm -f "$cohort_tmp"
    echo "[BLOCK] Creazione del run paper fallita; servizi lasciati fermi."
    return 2
  fi
  rm -f "$cohort_tmp"
  start_services 0
  echo "[VERIFY] Attendo due cicli completi del nuovo run ..."
  sleep 50
  if ! PYTHONPATH=src "$(venv_py)" tools/paper_preflight.py \
      --post-start --skip-synthetic; then
    echo "[BLOCK] Verifica post-start fallita: arresto immediato dei servizi."
    stop_services
    return 2
  fi
  echo "[OK] PAPER EXPERIMENTAL attivo. Edge non ancora dimostrato; denaro reale disabilitato."
}

show_status() {
  echo ""
  echo "=================== STATO ==================="
  for svc in bot dashboard latency_arb; do
    pidfile="$DATA_DIR/$svc.pid"
    if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile" 2>/dev/null)" 2>/dev/null; then
      pid_val="$(cat "$pidfile" 2>/dev/null)"
      echo "  $svc: IN ESECUZIONE (PID $pid_val)"
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
FORCE_FLAG=0
RUN_MODE="observe"
while [ "$#" -gt 0 ]; do
  case "$1" in
    scan) FORCE_SCAN_FLAG=1 ;;
    --force) FORCE_FLAG=1 ;;
    --mode)
      [ "$#" -ge 2 ] || { echo "[ERRORE] --mode richiede observe o paper_validation"; exit 2; }
      RUN_MODE="$2"
      shift
      ;;
    reset|fresh)
      echo "[ERRORE] 'restart reset' non è più supportato. Usa 'new-run' oppure 'reset --force'."
      exit 2
      ;;
    *)
      echo "Opzione sconosciuta: $1"
      echo "Uso: $0 [start|stop|restart|new-run|preflight-paper|paper-start|install|reset|status|logs|scan] [--mode observe|paper_validation] [scan] [--force]"
      exit 1
      ;;
  esac
  shift
done
if [ "$RUN_MODE" != "observe" ] && [ "$RUN_MODE" != "paper_validation" ]; then
  echo "[ERRORE] Modalita non valida: $RUN_MODE"
  exit 2
fi
[ "${SCAN:-0}" = "1" ] || [ "${FORCE_SCAN:-0}" = "1" ] && FORCE_SCAN_FLAG=1

case "$ACTION" in
  start)   start_services "$FORCE_SCAN_FLAG" ;;
  stop)    stop_services ;;
  restart)
    if [ "$FORCE_SCAN_FLAG" = "1" ]; then
      echo "[ERRORE] restart non puo cambiare la coorte; usa new-run --mode observe scan."
      exit 2
    fi
    restart_services
    ;;
  new-run) new_run "$RUN_MODE" "$FORCE_SCAN_FLAG" ;;
  preflight-paper) preflight_paper ;;
  paper-start) paper_start ;;
  install) ensure_venv; install_deps; run_wallet_scan ;;
  scan)    run_wallet_scan ;;
  reset)   reset_state "$FORCE_FLAG" ;;
  status)  show_status ;;
  logs)    tail -f "$LOGS_DIR/bot.log" "$LOGS_DIR/dashboard.log" "$LOGS_DIR/latency_arb.log" ;;
  *) echo "Uso: $0 [start|stop|restart|new-run|preflight-paper|paper-start|install|reset|status|logs|scan]"; exit 1 ;;
esac
