#!/usr/bin/env bash
#
# Polymarket Paper Trading Bot - gestione servizi (Linux / VPS)
#
# Uso:
#   ./start_all.sh [start|stop|restart|new-run|reset|install|status|logs|scan]
#
#   start    (default) installa deps se serve, ferma istanze precedenti e avvia
#   start scan  forza scan wallet prima dell'avvio
#   scan     aggiorna data/scan_results.json (wallet specialisti per categoria)
#   stop     ferma bot + dashboard (via PID file, con fallback)
#   restart  stop + start, conserva sempre tutto lo stato
#   new-run  archivia ledger/config del run corrente, poi avvia un run nuovo
#   install  crea/aggiorna virtualenv e installa requirements
#   reset --force  archivia e poi azzera lo stato (senza riavviare)
#   status   mostra stato dei servizi
#   logs     segue i log in tempo reale
#
#   Env: SCAN=1 forza scan | LATENCY_ARB_ENABLED=1 abilita il validator
#
#   Deploy VPS (dopo git pull / copia file):
#     ./start_all.sh restart
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

  if [ "${LATENCY_ARB_ENABLED:-0}" = "1" ]; then
    echo "[START] Latency-arb validator esplicitamente abilitato ..."
    nohup "$(venv_py)" -u src/latency_arb.py >"$LOGS_DIR/latency_arb.log" 2>&1 &
    echo $! > "$DATA_DIR/latency_arb.pid"
    sleep 2
  else
    echo "[START] Latency-arb: FERMO (default di quarantena)"
  fi

  show_status
  echo ""
  echo "Log live:  ./start_all.sh logs"
  echo "Stop:      ./start_all.sh stop"
}

archive_run() {
  ensure_venv
  mkdir -p "$DATA_DIR/runs"
  local run_id
  run_id="$("$(venv_py)" -c 'import json,pathlib,datetime; p=pathlib.Path("data/portfolio_state.json"); d=json.loads(p.read_text()) if p.exists() else {}; print(d.get("run_id") or ("legacy-"+datetime.datetime.now().strftime("%Y%m%dT%H%M%S")))' 2>/dev/null || true)"
  run_id="$(printf '%s' "$run_id" | tr -cd 'A-Za-z0-9._-')"
  run_id="$(printf '%s' "$run_id" | sed 's/^[._-]*//;s/[._-]*$//')"
  [ -n "$run_id" ] || run_id="legacy-$(date -u +%Y%m%dT%H%M%S)"
  local archive_dir="$DATA_DIR/runs/$run_id"
  if [ -e "$archive_dir" ]; then
    archive_dir="${archive_dir}-$(date -u +%Y%m%dT%H%M%S)"
  fi
  mkdir -p "$archive_dir"
  for file in \
    portfolio_state.json portfolio_state.json.bak trades_log.json \
    equity_curve.json peak_equity.json recent_opens.json daily_halt.json \
    safety_state.json candidate_journal.jsonl monitored_wallets.json \
    wallet_quality.json runtime_status.json; do
    if [ -f "$DATA_DIR/$file" ]; then
      cp -a "$DATA_DIR/$file" "$archive_dir/$file"
    fi
  done
  cp -a src/config.py "$archive_dir/config.py"
  git rev-parse HEAD > "$archive_dir/deployed_commit.txt" 2>/dev/null || true
  printf '{"run_id":"%s","archived_at":"%s"}\n' \
    "$run_id" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$archive_dir/archive_manifest.json"
  echo "[ARCHIVE] Run preservato in $archive_dir"
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
  local force_scan="${1:-0}"
  stop_services
  archive_run
  clear_trading_state
  start_services "$force_scan"
}

clear_trading_state() {
  echo "[RESET] Azzero completamente lo stato della simulazione ..."
  # Portfolio + trade + equity (storico vecchio)
  rm -f "$DATA_DIR/portfolio_state.json" "$DATA_DIR/portfolio_state.json.bak"
  rm -f "$DATA_DIR/trades_log.json"
  rm -f "$DATA_DIR/equity_curve.json"
  # Phase K: peak equity (tracking drawdown) — no peak stale dopo un reset
  rm -f "$DATA_DIR/peak_equity.json"
  # Phase I: recent_opens (dedup anti-reopen) — no blocchi da run vecchi
  rm -f "$DATA_DIR/recent_opens.json"
  rm -f "$DATA_DIR/safety_state.json" "$DATA_DIR/candidate_journal.jsonl"
  rm -f "$DATA_DIR/monitored_wallets.json"
  rm -f "$DATA_DIR/wallet_quality.json" "$DATA_DIR/runtime_status.json"
  # Phase W: price history (momentum tracker) — no stale trend data dopo reset
  rm -f "$DATA_DIR/price_history.json"
  # Phase BB: whale wallet list — no stale whale list dopo reset
  rm -f "$DATA_DIR/whale_wallets.json"
  # Alert log (Phase L) — riparte vuoto
  rm -f "$LOGS_DIR/alerts.log"
  # Phase CJ0: latency arb validator — azzera signal log + stats + pending
  # resolution (i signal "open" vecchi non si riferiscono al nuovo run).
  rm -f "$DATA_DIR/latency_arb_signals.jsonl" "$DATA_DIR/latency_arb_stats.json" "$DATA_DIR/daily_halt.json"
  rm -f "$LOGS_DIR/latency_arb.log" "$LOGS_DIR/latency_arb.log.*"
  echo "[RESET] Stato completamente azzerato."
  echo "[RESET] Mantenuti: scan_results.json (serve come seed; verra' aggiornato con 'scan')."
  echo "[RESET] Cancellati: portfolio_state, trades_log, equity_curve, peak_equity,"
  echo "       recent_opens, *.bak, backup_*, alerts.log,"
  echo "       latency_arb_signals/stats, daily_halt"
}

has_trading_history() {
  [ -f "$DATA_DIR/portfolio_state.json" ] \
    || [ -f "$DATA_DIR/trades_log.json" ] \
    || [ -f "$DATA_DIR/equity_curve.json" ]
}

restart_services() {
  local force_scan="${1:-0}"
  stop_services
  start_services "$force_scan"
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
RESET_FLAG=0
FORCE_FLAG=0
for arg in "$@"; do
  case "$arg" in
    scan) FORCE_SCAN_FLAG=1 ;;
    --force) FORCE_FLAG=1 ;;
    reset|fresh)
      echo "[ERRORE] 'restart reset' non è più supportato. Usa 'new-run' oppure 'reset --force'."
      exit 2
      ;;
    *)
      echo "Opzione sconosciuta: $arg"
      echo "Uso: $0 [start|stop|restart|new-run|install|reset|status|logs|scan] [scan] [--force]"
      exit 1
      ;;
  esac
done
[ "${SCAN:-0}" = "1" ] || [ "${FORCE_SCAN:-0}" = "1" ] && FORCE_SCAN_FLAG=1

case "$ACTION" in
  start)   start_services "$FORCE_SCAN_FLAG" ;;
  stop)    stop_services ;;
  restart) restart_services "$FORCE_SCAN_FLAG" ;;
  new-run) new_run "$FORCE_SCAN_FLAG" ;;
  install) ensure_venv; install_deps; run_wallet_scan ;;
  scan)    run_wallet_scan ;;
  reset)   reset_state "$FORCE_FLAG" ;;
  status)  show_status ;;
  logs)    tail -f "$LOGS_DIR/bot.log" "$LOGS_DIR/dashboard.log" "$LOGS_DIR/latency_arb.log" ;;
  *) echo "Uso: $0 [start|stop|restart|new-run|install|reset|status|logs|scan] [scan] [--force]"; exit 1 ;;
esac
