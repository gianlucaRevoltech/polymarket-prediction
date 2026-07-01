"""
Configurazione Polymarket Paper Trading Bot — MULTI-STRATEGY (2026-07-01)

Estensioni vs versione precedente (Phase A-G):
  - Phase I: delta per-wallet, dedup_window implementato
  - Phase J: poll 30s, min_days 0.5, banda soft 0.25-0.75 con consenso>=2
  - Phase K: sizing compounding ladder (gated WR), reserve 20%, dd halve
  - Phase L: monitoraggio balance + alert + equity floor auto-stop
  - Phase M-N-O-P: multi-strategy router (COPY + ARB binary + HARVEST + ARB cross)
  - Phase Q: value-betting gated (deprecato finche non serve)

Vincoli: NON sostituire la lista wallet curata (soft-disable WR<0.55).
Obiettivo: tendere al doubling settimanale via diversificazione strategie.
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
    "initial_capital": 300.0,
    # sizing compounding ladder (Phase K): parte conservativo, scala per tier
    "max_position_size": 0.03,     # floor: 3% (fino a 30 trade paper)
    "sizing_tiers": [
        # (soglia n_trade_totali, sizing_frac, descr)
        (0,   0.03, "avviamento 3% (WR non ancora misurabile)"),
        (30,  0.05, "tier1: +30 trade, edge inizia a misurarsi"),
        (60,  0.08, "tier2: +60 trade e WR>=55%"),
        (120, 0.12, "tier3: +120 trade e WR>=60% (massimo)"),
    ],
    "sizing_wr_gate": 0.55,        # WR minimo per salire di tier (altirmenti resta)
    "min_position_size": 5.0,      # Minimo Polymarket
    "max_open_positions": 6,       # Phase J: 4 -> 6 (dopo Phase I cattura molto piu)
    "reserve_ratio": 0.20,         # Phase K: 25% -> 20% (piu capitale operativo)
    # Risk management (copy/harvest): SL/TP
    "stop_loss_pct": -0.08,        # taglia perdenti presto
    "take_profit_pct": 0.20,       # lascia correre vincenti
    "hard_stop_loss_pct": -0.15,   # protezione hard floor
    # Harvest: SL duro diverso (near-certain, se -3% esce, esito NON era certo)
    "harvest_hard_stop_pct": -0.03,
    "harvest_soft_exit_pct": -0.10,  # se prezzo <0.90 dall'entry chiudi (black-swan)
    # Drawdown protection (Phase K/L): se drawdown>12% dal peak, sizing auto -50%
    "drawdown_halve_threshold": 0.12,
    "drawdown_halve_factor": 0.5,
    # Equity floor (Phase L): -5% da initial → blocca aperture nuove (solo gestione posizioni)
    "equity_floor_pct": -0.05,
    "hard_ruin_pct": -0.20,        # -20% → stop totale (emergenza)
    # Diversificazione reale (per copy)
    "max_positions_per_wallet": 1,
    "max_positions_per_category": 2,
    # Dedup anti reopen stesso asset (Phase I): entro N secondi non riaprire
    "dedup_window_sec": 3600,
}

# Fee Polymarket (CLOB attualmente 0% per mercati non-sport)
FEES = {
    "taker_fee": 0.0,
    "maker_fee": 0.0,
    "gas_estimate": 0.0
}

# Strategia di COPY (engine principale)
STRATEGY = {
    "mode": "copy",
    "min_wallets_consensus": 2,
    "top_wallets": 20,
    # banda base 0.30-0.70 (edge max backtest)
    "entry_price_min": 0.30,
    "entry_price_max": 0.70,
    # banda soft (Phase J): allentata 0.25-0.75 quando consenso>=2 wallet (extra aperture)
    "soft_price_min": 0.25,
    "soft_price_max": 0.75,
    "soft_requires_consensus": 2,
    # anti entrata tardiva (Phase C)
    "max_entry_drift": 0.05,
    # copy-trade puntuale via delta-snapshot PER-WALLET (Phase I)
    "delta_copy": True,
    # filtro scadenza
    "max_days_to_expiry": 60,
    "min_days_to_expiry": 0.5,     # Phase J: 1.0 -> 0.5 (sport intraday >12h, no coinflip 5min)
    # filtro liquidita
    "min_book_size_usdc": 50.0,
    "max_spread_ticks": 3,
    # soft-disable wallet WR basso
    "soft_disable_wr_threshold": 0.55,
    "soft_disable_size_factor": 0.5,
    "min_wallet_win_rate": 0.55,
    "prime_baseline_on_start": False,
}

# Strategie complementari (Phase M): allocation caps sul portafoglio.
# "cap_pct" = frazione del portafoglio dedicata a quella strategia (soft: il cash
# non usato e' disponibile ad altre strategie). "max_single" = size massima per
# singolo trade di quella strategia.
STRATEGIES = {
    "copy": {
        "cap_pct": 0.50,           # copy = engine principale
        "max_single": 0.12,        # sizing ladder gate tier3
        "max_positions": 4,        # lascia slot a harvest/arb (max_open=6)
    },
    "arb_binary": {
        "cap_pct": 0.25,           # risk-free-ish, deploy cash idle
        "max_single": 0.15,
        "max_positions": 1,        # risk-free raro, 1 slot
        "min_profit_abs": 0.50,    # profitto minimo assoluto $0.50 (no micro)
        "safety_margin": 0.005,    # 0.5c di sicurezza sui fill
        "max_days_to_expiry": 14,  # APR alta; no capital-lock lungo
        "scan_markets": 80,        # quanti mercati attivi scansionare/ciclo
        "scan_every_cycles": 2,    # ogni 2 cicli (60s*2=120s) — bilanciamento load
    },
    "harvest": {
        "cap_pct": 0.12,
        "max_single": 0.08,
        "max_positions": 2,        # non saturare slot con harvest (lascia copy)
        "fav_min": 0.85,           # lato vincente >= 0.85
        "fav_max": 0.975,          # non tutto il juice gia' prezziato
        "max_days_to_expiry": 21,   # Phase O: 7 -> 21 (cattura WC blowout 19gg)
        "min_book_size": 20.0,
        "max_spread_ticks": 2,
        "scan_markets": 80,
        "scan_every_cycles": 2,
    },
    "arb_cross": {
        "cap_pct": 0.10,
        "max_single": 0.10,
        "max_positions": 1,        # n-leg raro: una posizione alla volta
        "min_profit_abs": 1.00,    # n-leg piu' raro, profitto piu' sostanzioso
        "safety_margin": 0.01,     # 1c per n-leg
        "min_outcomes": 3,         # almeno 3 gambe elsefiltro
        "max_outcomes": 12,
        "scan_events": 12,         # eventi esaustivi da controllare/ciclo
        "scan_every_cycles": 5,
    },
    # value-betting gated (Phase Q): disattivato finche altre non basta
    "value": {"enabled": False},
}

# Selezione wallet per categoria (scanner) — invariato
CATEGORIES = {
    "active": ["sport", "crypto", "politics", "weather"],
    "specialists_per_category": 5,
    "markets_to_scan": 200,
    "holders_per_market": 25,
    "min_overlap": 2,
    "min_realized_roi": 0.20,
    "min_decided": 10,
    "min_win_rate": 0.55,
}

# Wallet Scanner
SCANNER = {
    "min_profit": 1000,
    "min_volume": 10000,
    "min_trades": 10,
    "max_age_days": 90,
    "auto_rescan_enabled": False,
    "auto_rescan_interval_sec": 6 * 3600,
    "min_win_rate": 0.55,
    "min_decided": 10,
}

# Analyzer
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
    "poll_interval": 30,           # Phase J: 60 -> 30s
    "activity_limit": 100,
    "dedup_window": 3600,
}

# Monitoring / Alert (Phase L)
MONITOR = {
    "equity_floor_pct": -0.05,     # blocca nuove aperture sotto -5%
    "drawdown_halve_pct": 0.12,    # -50% sizing sotto -12% dal peak
    "ruin_pct": -0.20,             # stop totale
    "weekly_target_pct": 0.20,     # alert se +% settimana < 20%
    "alert_log_path": LOGS_DIR / "alerts.log",
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