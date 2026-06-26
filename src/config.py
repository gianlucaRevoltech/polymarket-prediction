"""
Configurazione Polymarket Paper Trading Bot
"""
import os
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"

# API Endpoints
POLYMARKET_API = {
    "gamma": "https://gamma-api.polymarket.com",
    "clob": "https://clob.polymarket.com",
    "data": "https://data-api.polymarket.com"
}

# Budget e Risk Management
BUDGET = {
    "initial_capital": 300.0,  # Euro
    "max_position_size": 0.10,  # 10% del budget per trade
    "min_position_size": 5.0,   # Minimo Polymarket
    "max_open_positions": 10,
    "reserve_ratio": 0.20  # 20% riserva per opportunità
}

# Fee Polymarket
# NOTA: il CLOB di Polymarket attualmente NON applica trading fee (0%).
# Manteniamo i campi per compatibilita ma il costo reale di esecuzione e' modellato
# come slippage in SIMULATOR["entry_slippage"], non come fee.
FEES = {
    "taker_fee": 0.0,
    "maker_fee": 0.0,
    "gas_estimate": 0.0  # Gas pagato dal relayer Polymarket per l'utente
}

# Strategia di copia
STRATEGY = {
    # "copy": rispecchia le posizioni dei top wallet (soglia consenso = 1)
    # "consensus": apre solo su asset detenuti da >= min_wallets_consensus wallet
    "mode": "consensus",
    "min_wallets_consensus": 2,
    "top_wallets": 15,          # Quanti wallet monitorare (piu wallet = piu chance di consenso)
    # All'avvio registra le posizioni gia esistenti come baseline (non le copia),
    # cosi simuliamo solo gli ingressi NUOVI a prezzo "live" evitando entrate tardive
    "prime_baseline_on_start": True
}

# Wallet Scanner
SCANNER = {
    "min_profit": 1000,  # Profitto minimo $1K (compatibile budget piccolo)
    "min_volume": 10000,  # Volume minimo $10K (non whale)
    "min_trades": 10,     # Minimo 10 trade
    "max_age_days": 90,   # Wallet attivi negli ultimi 90 giorni
    "check_interval": 300  # Controlla ogni 5 minuti
}

# Analyzer - Filtri qualità wallet
ANALYZER = {
    "min_roi": 0.10,           # ROI minimo 10%
    "min_win_rate": 0.50,      # Win rate minimo 50%
    "max_drawdown": 0.30,      # Drawdown massimo 30%
    "min_sharpe": 1.0,         # Sharpe ratio minimo
    "min_consistency": 0.60,   # 60% trade profittevoli
    "min_avg_trade_size": 10,  # Trade medio minimo $10 (compatibile budget 300€)
    "max_avg_trade_size": 1000000,  # Rimosso limite max - whale wallet sono profittevoli
    "prefer_diversified": True # Preferisci wallet diversificati
}

# Simulator
SIMULATOR = {
    "copy_delay": 5,           # Secondi di delay nel copy
    "max_slippage": 0.02,      # 2% slippage massimo
    "entry_slippage": 0.01,    # Slippage applicato in ingresso (peggiora il prezzo di acquisto)
    "min_confidence": 0.70,    # Confidence minima per copiare
    "auto_approve": False      # Richiedi approvazione manuale
}

# Tracking
TRACKING = {
    "poll_interval": 60,       # Controlla activity ogni 60s
    "activity_limit": 100,     # Ultime 100 activity
    "dedup_window": 3600       # Dedup window 1 ora
}

# Logging
LOGGING = {
    "level": "INFO",
    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    "file": LOGS_DIR / "bot.log"
}

# Ensure directories exist
DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
