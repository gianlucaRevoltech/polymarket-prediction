"""
Configurazione Polymarket Paper Trading Bot — MULTI-STRATEGY AGGRESSIVO (2026-07-02)

Sessione 2026-07-02 (Phase R-Y): respiro aggressivo per target doubling 1-2 sett.
  - Phase R: sizing 6% partenza, tier 0/10/25/50, max_open 12, reserve 15%
  - Phase S: copy banda soft 0.20-0.80, min_book 25, min_days 0.25, drift 0.08
  - Phase T: harvest cap 30% + 6 pos + fav 0.78-0.985 + early TP +4%
  - Phase U: arb scan 150 markets, min_profit $0.20, ogni ciclo
  - Phase V: wallet rotation 3h + 30 wallet + scanner più ampio
  - Phase W: MOMENTUM strategy (trend-following, price history tracker)
  - Phase X: poll 20s

Vincoli: soft-disable WR<0.55 sui wallet (non rimossi, size dimezzata).
Obiettivo: $300 -> $600 in 10-14gg via sizing aggressivo + alta frequenza + multi-strategy.
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
    # Phase R: sizing AGGRESSIVO — 6% partenza (backtest valida edge), scala veloce
    "max_position_size": 0.06,     # floor: 6% (era 3%)
    "sizing_tiers": [
        # (soglia n_trade_totali, sizing_frac, descr) — Phase R: thresholds bassi
        (0,   0.06, "avviamento 6% (edge backtest-validato)"),
        (10,  0.10, "tier1: +10 trade, sizing 10%"),
        (25,  0.13, "tier2: +25 trade e WR>=55%, sizing 13%"),
        (50,  0.15, "tier3: +50 trade e WR>=60%, sizing 15% (massimo)"),
    ],
    "sizing_wr_gate": 0.50,        # Phase R: gate più permissivo (0.55 -> 0.50)
    "min_position_size": 5.0,      # Minimo Polymarket
    "max_open_positions": 12,      # Phase R: 6 -> 12 (più slot per tutte le strategie)
    "reserve_ratio": 0.15,         # Phase R: 20% -> 15% (più capitale operativo)
    # Risk management (copy): SL/TP
    "stop_loss_pct": -0.08,        # taglia perdenti presto
    "take_profit_pct": 0.20,       # lascia correre vincenti
    "hard_stop_loss_pct": -0.15,   # protezione hard floor
    # Harvest: SL duro + early TP (Phase T: scalp mode per turnover)
    "harvest_hard_stop_pct": -0.04,  # -4% (era -3%, più tollerante con fav_min 0.78)
    "harvest_soft_exit_pct": -0.12,  # se prezzo crolla sotto -12% chiudi (black-swan)
    "harvest_take_profit_pct": 0.04, # Phase T: early TP +4% → scalp, libera capitale
    # Drawdown protection: se drawdown>12% dal peak, sizing auto -50%
    "drawdown_halve_threshold": 0.12,
    "drawdown_halve_factor": 0.5,
    # Equity floor: -5% da initial → blocca aperture nuove
    "equity_floor_pct": -0.05,
    "hard_ruin_pct": -0.20,        # -20% → stop totale (emergenza)
    # Diversificazione (per copy) — Phase R: più permissivo
    "max_positions_per_wallet": 2,     # era 1 → 2 (copy: anche 2 pos/wallet)
    "max_positions_per_category": 4,   # era 2 → 4
    # Dedup anti reopen stesso asset
    "dedup_window_sec": 3600,
}

# Fee Polymarket (CLOB attualmente 0% per mercati non-sport)
FEES = {
    "taker_fee": 0.0,
    "maker_fee": 0.0,
    "gas_estimate": 0.0
}

# Strategia di COPY (engine principale) — Phase S: più aggressivo
STRATEGY = {
    "mode": "copy",
    "min_wallets_consensus": 2,
    "top_wallets": 30,             # Phase V: 20 -> 30 (più wallet monitorati)
    # banda base 0.30-0.70 (edge max backtest)
    "entry_price_min": 0.30,
    "entry_price_max": 0.70,
    # banda soft (Phase S): allentata 0.20-0.80 quando consenso>=2 wallet
    "soft_price_min": 0.20,
    "soft_price_max": 0.80,
    "soft_requires_consensus": 2,
    # anti entrata tardiva (Phase S: drift 0.05 -> 0.08, ingressi meno tardivi OK)
    "max_entry_drift": 0.08,
    # copy-trade puntuale via delta-snapshot PER-WALLET (Phase I)
    "delta_copy": True,
    # filtro scadenza (Phase S: min_days 0.5 -> 0.25, sport intraday 6h+)
    "max_days_to_expiry": 60,
    "min_days_to_expiry": 0.25,
    # filtro liquidita (Phase S: min_book 50 -> 25, spread 3 -> 4 ticks)
    "min_book_size_usdc": 25.0,
    "max_spread_ticks": 4,
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
        "cap_pct": 0.40,           # Phase R: copy resta engine ma lascia spazio a harvest
        "max_single": 0.15,        # Phase R: sizing ladder gate tier3 (era 0.12)
        "max_positions": 6,        # Phase R: 4 -> 6 (max_open=12, lascia slot altre)
    },
    "arb_binary": {
        "cap_pct": 0.30,           # Phase U: 25% -> 30%
        "max_single": 0.15,
        "max_positions": 3,        # Phase U: 1 -> 3
        "min_profit_abs": 0.20,    # Phase U: 0.50 -> 0.20 (micro-arb worthwhile)
        "safety_margin": 0.005,
        "max_days_to_expiry": 14,
        "scan_markets": 150,       # Phase U: 80 -> 150
        "scan_every_cycles": 1,    # Phase U: 2 -> 1 (ogni ciclo)
    },
    "harvest": {
        "cap_pct": 0.30,           # Phase T: 12% -> 30% (engine primario per doubling)
        "max_single": 0.15,        # Phase T: 8% -> 15%
        "max_positions": 6,        # Phase T: 2 -> 6
        "fav_min": 0.78,           # Phase T: 0.85 -> 0.78 (più opportunità)
        "fav_max": 0.985,          # Phase T: 0.975 -> 0.985 (cattura juice residuo)
        "max_days_to_expiry": 30,  # Phase T: 21 -> 30
        "min_book_size": 15.0,     # Phase T: 20 -> 15
        "max_spread_ticks": 3,     # Phase T: 2 -> 3 (più tollerante)
        "scan_markets": 150,       # Phase T: 80 -> 150
        "scan_every_cycles": 1,    # Phase T: 2 -> 1 (ogni ciclo, 20s)
    },
    "arb_cross": {
        "cap_pct": 0.15,
        "max_single": 0.12,
        "max_positions": 2,        # Phase U: 1 -> 2
        "min_profit_abs": 0.50,    # Phase U: 1.00 -> 0.50
        "safety_margin": 0.01,
        "min_outcomes": 3,
        "max_outcomes": 12,
        "scan_events": 25,         # Phase U: 12 -> 25
        "scan_every_cycles": 2,    # Phase U: 5 -> 2
    },
    # Phase W: MOMENTUM strategy (trend-following su prezzo Polymarket)
    "momentum": {
        "cap_pct": 0.20,           # alto turnover, sizing significativo
        "max_single": 0.10,
        "max_positions": 3,
        "min_move_pct": 0.05,      # move >= 5% nella finestra
        "window_cycles": 6,        # ~2min a 20s poll (finestra breve = trend fresco)
        "min_book_size": 30.0,
        "max_spread_ticks": 4,
        "min_days_to_expiry": 0.5,
        "max_days_to_expiry": 60,
        "scan_markets": 100,       # top mercati per volume
        "scan_every_cycles": 1,    # ogni ciclo (aggiorna price history)
        "take_profit_pct": 0.06,   # TP +6% (trend continuation)
        "stop_loss_pct": -0.05,    # SL -5% (inversione → esci)
        "min_volume": 2000.0,      # liquidi solo
    },
    # value-betting gated (Phase Q): disattivato
    "value": {"enabled": False},
}

# Selezione wallet per categoria (scanner) — invariato
CATEGORIES = {
    "active": ["sport", "crypto", "politics", "weather"],
    "specialists_per_category": 5,
    "markets_to_scan": 300,        # Phase V: 200 -> 300
    "holders_per_market": 25,
    "min_overlap": 2,
    "min_realized_roi": 0.20,
    "min_decided": 10,
    "min_win_rate": 0.55,
}

# Wallet Scanner
SCANNER = {
    "min_profit": 500,             # Phase V: 1000 -> 500 (wallet mid-cap più attivi)
    "min_volume": 5000,            # Phase V: 10000 -> 5000
    "min_trades": 8,               # Phase V: 10 -> 8
    "max_age_days": 90,
    "auto_rescan_enabled": True,   # Phase V: False -> True (rotazione automatica)
    "auto_rescan_interval_sec": 3 * 3600,  # Phase V: 6h -> 3h
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
    "poll_interval": 20,           # Phase X: 30 -> 20s (capture più rapido)
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