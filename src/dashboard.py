"""
Dashboard Web per Polymarket Paper Trading Bot
Interfaccia leggera Flask per monitoraggio real-time
"""
import sys
import json
import os
from datetime import datetime
from pathlib import Path

if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8')

from flask import Flask, render_template, jsonify

# Aggiungi path src
BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))

from simulator import PaperTradingSimulator
from config import BUDGET, STRATEGY, DATA_DIR

app = Flask(__name__, template_folder=str(Path(__file__).parent / "templates"))


def get_portfolio_data():
    """Carica dati portfolio"""
    try:
        sim = PaperTradingSimulator(BUDGET["initial_capital"])
        summary = sim.get_portfolio_summary()
        
        positions = []
        for pid, pos in sim.portfolio.positions.items():
            positions.append({
                "id": pid,
                "market": pos.market_title[:50],
                "outcome": pos.outcome,
                "entry_price": pos.entry_price,
                "current_price": pos.current_price,
                "size": pos.size_usdc,
                "pnl": pos.pnl,
                "pnl_pct": pos.pnl_pct,
                "entry_time": pos.entry_time.strftime("%Y-%m-%d %H:%M"),
                "source_wallet": pos.source_wallet[:10] + "..."
            })
        
        recent_trades = []
        trades_file = DATA_DIR / "trades_log.json"
        if trades_file.exists():
            with open(trades_file, "r") as f:
                all_trades = json.load(f)
                recent_trades = all_trades[-20:]  # Ultimi 20
                recent_trades.reverse()
        
        return {
            "summary": summary,
            "positions": positions,
            "recent_trades": recent_trades,
            "monitored_wallets": get_monitored_wallets(),
            "bot_status": get_bot_status()
        }
    except Exception as e:
        return {
            "summary": {
                "strategy_mode": STRATEGY["mode"],
                "initial_capital": BUDGET["initial_capital"],
                "current_value": BUDGET["initial_capital"],
                "cash": BUDGET["initial_capital"],
                "total_pnl": 0,
                "total_pnl_pct": 0,
                "unrealized_pnl": 0,
                "realized_pnl": 0,
                "open_positions": 0,
                "closed_positions": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0
            },
            "positions": [],
            "recent_trades": [],
            "monitored_wallets": [],
            "bot_status": "unknown",
            "error": str(e)
        }


def get_monitored_wallets():
    """Carica wallet monitorati dal file scan results"""
    results_file = DATA_DIR / "scan_results.json"
    if not results_file.exists():
        return []
    
    try:
        with open(results_file, "r") as f:
            data = json.load(f)
        
        wallets = []
        for w in data.get("wallets", [])[:10]:
            wallets.append({
                "name": w.get("name", "Unknown"),
                "address": w.get("address", ""),
                "roi": w.get("roi", 0),
                "profit": w.get("profit", 0),
                "volume": w.get("volume", 0),
                "trades": w.get("num_trades", 0)
            })
        return wallets
    except Exception:
        return []


def get_bot_status():
    """Verifica se il bot è in esecuzione controllando il file PID"""
    try:
        # Controlla il file PID
        pid_file = BASE_DIR / "data" / "bot.pid"
        if pid_file.exists():
            with open(pid_file, 'r') as f:
                pid = int(f.read().strip())
            
            # Verifica se il processo è ancora attivo
            if sys.platform.startswith('win'):
                import subprocess
                result = subprocess.run(
                    ['tasklist', '/FI', f'PID eq {pid}'],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                return "running" if str(pid) in result.stdout else "stopped"
            else:
                try:
                    os.kill(pid, 0)
                    return "running"
                except OSError:
                    return "stopped"
        return "stopped"
    except Exception:
        return "unknown"


@app.route("/api/equity")
def api_equity():
    """Endpoint readonly: storico equity curve dal file generato dal bot"""
    equity_file = DATA_DIR / "equity_curve.json"
    if not equity_file.exists():
        return jsonify([])
    try:
        with open(equity_file, "r") as f:
            data = json.load(f)
        # Ritorna punti essenziali (utile per il frontend)
        points = []
        for d in data:
            points.append({
                "timestamp": d.get("timestamp"),
                "equity": d.get("equity", 0),
                "cash": d.get("cash", 0),
                "unrealized_pnl": d.get("unrealized_pnl", 0),
                "realized_pnl": d.get("realized_pnl", 0),
            })
        return jsonify(points)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/")
def index():
    """Pagina principale dashboard"""
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    """API endpoint per stato bot"""
    data = get_portfolio_data()
    data["timestamp"] = datetime.now().isoformat()
    return jsonify(data)


@app.route("/api/portfolio")
def api_portfolio():
    """API endpoint per portfolio"""
    try:
        sim = PaperTradingSimulator(BUDGET["initial_capital"])
        summary = sim.get_portfolio_summary()
        return jsonify(summary)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def run_dashboard(host="0.0.0.0", port=5000):
    """Avvia la dashboard"""
    print(f"\n{'='*60}")
    print(f"  POLYMARKET DASHBOARD")
    print(f"  http://{host}:{port}")
    print(f"{'='*60}\n")

    # Scrivi PID per stop/restart puliti da parte degli script
    pid_file = BASE_DIR / "data" / "dashboard.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    with open(pid_file, "w") as f:
        f.write(str(os.getpid()))

    try:
        app.run(host=host, port=port, debug=False)
    finally:
        if pid_file.exists():
            pid_file.unlink()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    run_dashboard(port=port)
