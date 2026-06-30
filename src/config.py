"""
Configurazione Polymarket Paper Trading Bot

Aggiornato (7-fasi fix):
  - Phase D: banda prezzo 0.30-0.70, filtro scadenza 60gg, filtro liquidita
  - Phase E: SL -8% / TP +20%, hard SL -15%, max 1 pos/wallet, max 2/cat
  - Phase F: sizing 3%, max 4 posizioni, reserve 25%
  - Phase B: soft-disable wallet win-rate < 0.55
  - Phase C: copy-trade puntuale via delta-snapshot
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

# Budget e Risk Management (Phase E + F)
BUDGET = {
    "initial_capital": 300.0,
    "max_position_size": 0.03,    # Phase F: 5% -> 3% (finche WR > 50% paper)
    "min_position_size": 5.0,    # Minimo Polymarket
    "max_open_positions": 4,     # Phase F: 6 -> 4 (concentra su segnali migliori)
    "reserve_ratio": 0.25,       # Phase F: 15% -> 25% (fase di fix)
    # Risk management (Phase E): SL/TP simmetrici/favorevoli
    "stop_loss_pct": -0.08,      # Phase E: -30% -> -8% (taglia perdenti presto)
    "take_profit_pct": 0.20,     # Phase E: +50% -> +20% (lascia correre vincenti)
    "hard_stop_loss_pct": -0.15, # Phase E: protezione hard floor (sempre chiuso)
    # Diversificazione reale (Phase E)
    "max_positions_per_wallet": 1,    # max 1 posizione aperta per wallet sorgente
    "max_positions_per_category": 2, # max 2 posizioni per categoria (anti-correlazione)
}

# Fee Polymarket (CLOB attualmente 0% taker permercati non-sport)
FEES = {
    "taker_fee": 0.0,
    "maker_fee": 0.0,
    "gas_estimate": 0.0
}

# Strategia di copia (Phase C + D)
STRATEGY = {
    "mode": "copy",
    "min_wallets_consensus": 2,
    "top_wallets": 20,
    # Phase D: banda prezzo entry ristretta (out favorite-selling e longshot)
    "entry_price_min": 0.30,
    "entry_price_max": 0.70,
    # Phase C: anti entrata tardiva piu stringente
    "max_entry_drift": 0.05,      # 12% -> 5%
    # Phase C: copy-trade PUNTOLE via delta-snapshot (NON mirrorare bag intero)
    "delta_copy": True,
    # Phase D: filtro scadenza mercato (evita capital-lock 2028 elections)
    "max_days_to_expiry": 60,
    # Phase D-refined: minimo durata del mercato (esclude coin-flip 5-min/1h crypto
    # dove i wallet fanno market-making con rebate NON copiabile dal retail).
    # Minimo 1 giorno di scadenza: mercati da almeno 24h di durata residua.
    "min_days_to_expiry": 1.0,
    # Phase D: filtro liquidita all'ingresso
    "min_book_size_usdc": 50.0,   # best bid o ask size minima
    "max_spread_ticks": 3,        # spread massimo in tick ($0.01)
    # Phase B: soft-disable wallet con win-rate basso (NON rimossi, size dimezzata)
    "soft_disable_wr_threshold": 0.55,
    "soft_disable_size_factor": 0.5,
    "min_wallet_win_rate": 0.55,
    # Disattivato: la baseline e' gestita dal delta-snapshot nel main loop
    "prime_baseline_on_start": False,
}

# Selezione wallet per categoria di mercato (specialisti)
CATEGORIES = {
    "active": ["sport", "crypto", "politics", "weather"],
    "specialists_per_category": 5,
    "markets_to_scan": 200,
    "holders_per_market": 25,
    "min_overlap": 2,
    "min_realized_roi": 0.20,   # ROI realizzato storico minimo 20%
    "min_decided": 10,           # posizioni decise minime (anti-fortuna)
    "min_win_rate": 0.55,        # Win rate minimo 55% (ENFORCED su ogni path)
}

# Wallet Scanner
SCANNER = {
    "min_profit": 1000,
    "min_volume": 10000,
    "min_trades": 10,
    "max_age_days": 90,
    # Phase B: auto-rescan DISABILITATO di default per NON sostituire la lista
    # wallet curata (vincente utente: "non cambiare i wallet crea casini").
    # I wallet a basso win-rate sono gestiti dal soft-disable (size dimezzata),
    # non rimossi. Per riscoprire wallet manualmente: python src/scanner.py --mode categories
    "auto_rescan_enabled": False,
    "auto_rescan_interval_sec": 6 * 3600,
    # Phase B: enforce qualita anche sul path legacy scan_all
    "min_win_rate": 0.55,
    "min_decided": 10,
}

# Analyzer - Filtri qualita wallet
ANALYZER = {
    "min_roi": 0.20,
    "min_win_rate": 0.55,
    "max_drawdown": 0.25,
    "min_sharpe": 1.0,
    "min_consistency": 0.60,
    "min_avg_trade_size": 10,
    "max_avg_trade_size": 1000000,
    "prefer_diversified": True
}

# Simulator
SIMULATOR = {
    "copy_delay": 5,
    "max_slippage": 0.02,
    "entry_slippage": 0.01,
    "min_confidence": 0.70,
    "auto_approve": False
}

# Tracking
TRACKING = {
    "poll_interval": 60,
    "activity_limit": 100,
    "dedup_window": 3600
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