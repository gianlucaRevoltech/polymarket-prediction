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
DATA_DIR = Path(os.environ.get("POLYMARKET_DATA_DIR", str(BASE_DIR / "data")))
LOGS_DIR = Path(os.environ.get("POLYMARKET_LOGS_DIR", str(BASE_DIR / "logs")))

# Modalità di esecuzione fail-safe. `observe` continua a scansionare e a gestire
# le posizioni esistenti, ma vieta ogni nuova apertura. La promozione a
# `paper_validation` è esplicita e resta comunque solo simulata.
EXECUTION = {
    "mode": os.environ.get("POLYMARKET_EXECUTION_MODE", "observe").strip().lower(),
    "paper_size_usdc": 5.0,
    "max_open_positions": 2,
    "event_cap_pct": 0.03,
    "daily_loss_usdc": 3.0,
    "run_loss_usdc": 6.0,
    "max_consecutive_losses": 3,
    "freeze_wallets_in_validation": True,
    "latency_arb_enabled": False,
}
if EXECUTION["mode"] not in {"observe", "paper_validation"}:
    EXECUTION["mode"] = "observe"

# API Endpoints
POLYMARKET_API = {
    "gamma": "https://gamma-api.polymarket.com",
    "clob": "https://clob.polymarket.com",
    "data": "https://data-api.polymarket.com"
}

# Budget e Risk Management
BUDGET = {
    "initial_capital": 300.0,
    # Phase CG: sizing CONSERVATIVO — torna a 3% base finche WR<50%.
    # Era 6% (Phase R), ma WR 24% non giustifica sizing aggressivo.
    "max_position_size": 0.03,     # floor: 3% (era 6%, ridotto per WR 24%)
    "sizing_tiers": [
        # (soglia n_trade_totali, sizing_frac, descr) — Phase CG: conservativo
        (0,   0.03, "avviamento 3% (validare edge prima di scalare)"),
        (15,  0.05, "tier1: +15 trade e WR>=45%, sizing 5%"),
        (40,  0.08, "tier2: +40 trade e WR>=50%, sizing 8%"),
        (80,  0.10, "tier3: +80 trade e WR>=55%, sizing 10% (massimo)"),
    ],
    "sizing_wr_gate": 0.45,        # Phase CG: gate realistico (era 0.50)
    "min_position_size": 5.0,      # Minimo Polymarket
    "max_open_positions": 2,       # validazione: massimo due posizioni totali
    "reserve_ratio": 0.20,         # Phase CG: 15% -> 20% (piu cushion)
    # Risk management (copy): SL/TP
    "stop_loss_pct": -0.08,        # taglia perdenti presto
    "take_profit_pct": 0.20,       # lascia correre vincenti
    "hard_stop_loss_pct": -0.15,   # protezione hard floor
    # Harvest: SL assoluto + hold-to-resolution (Phase CF)
    # Phase CF: SL % era -4% → a 0.985 triggera a 0.946 = rumore. SL assoluto -5 cent.
    "harvest_hard_stop_pct": -0.05,  # Phase CD: -5% fallback (se assoluto non disponibile)
    "harvest_hard_stop_abs": -0.05,  # Phase CD: SL assoluto -5 cent (robusto a prezzi estremi)
    "harvest_soft_exit_pct": -0.15,  # se prezzo crolla sotto -15% chiudi (black-swan)
    "harvest_soft_exit_abs": -0.15,  # Phase CD: soft exit assoluto -15 cent
    "harvest_take_profit_pct": 0.0,  # Phase CF: 0.04 -> 0.0 (hold-to-resolution, payout $1)
    # Phase CC: Trailing stop — DISABILITATO (triggera su rumore a WR 24%)
    "trailing_stop_enabled": False,
    "trailing_stop_pct": -0.03,      # -3% dal peak → chiudi (lock profit)
    "trailing_activate_pct": 0.03,  # attiva trailing solo dopo +3% gain
    "trailing_apply_strategies": ["copy", "harvest"],
    # Phase EE: Kelly fractional sizing — DISABILITATO finche WR<50%
    "kelly_enabled": False,
    "kelly_fraction": 0.25,          # 1/4 Kelly (fractional, anti over-bet)
    "kelly_min_size": 0.03,         # floor sizing Kelly
    "kelly_max_size": 0.20,         # cap sizing Kelly (anti blow-up)
    # Phase FF: Correlation-aware hedging — cluster cap per evento
    "cluster_cap_pct": 0.03,        # validazione: max 3% portafoglio per evento
    "cluster_max_positions": 1,     # una sola posizione aperta per evento
    "cluster_event_key": "event_slug",  # campo per raggruppare (event_slug)
    # Phase HH: Re-entry su continuation
    "reentry_enabled": False,
    "reentry_max": 2,               # max 2 re-entry per posizione originale
    "reentry_size_factor": 0.5,     # size dimezzata ogni re-entry
    "reentry_only_strategies": ["momentum", "whale"],
    # Phase LL: Limit orders simulati (paper)
    "limit_orders_enabled": True,
    "limit_order_ttl_sec": 300,     # order expires dopo 5min
    "limit_order_max_active": 6,    # max limit orders pending simultanei
    # Drawdown protection: se drawdown>12% dal peak, sizing auto -50%
    "drawdown_halve_threshold": 0.12,
    "drawdown_halve_factor": 0.5,
    # Phase CI1 (Guida 2: risk mgmt hardening): DAILY loss limit + halt.
    # Mancava un contatore giornaliero (avevamo solo lifetime floor -5% e
    # ruin -20%). La differenza tra +1322% e liquidazione (Claude vs OpenClaw)
    # era solo il risk mgmt. halt nuove aperture se realized_giornaliero <= -8%
    # e considera -5% come warning. Reset automatico a mezzanotte.
    "daily_loss_limit_pct": -0.08,
    "daily_loss_warn_pct": -0.05,
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
        "scan_enabled": True,
        "paper_enabled": True,
        "cap_pct": 0.40,           # Phase R: copy resta engine ma lascia spazio a harvest
        "max_single": 0.15,        # Phase R: sizing ladder gate tier3 (era 0.12)
        "max_positions": 6,        # Phase R: 4 -> 6 (max_open=12, lascia slot altre)
        # Phase CI5 (fix 0W/3L tennis in-play): copy su sport ha SL -8% che spara
        # su swing normali di gioco (break = 10-15% move anche su risultato finale
        # corretto). -8% su entry 0.42 = -3.4 cent = rumore tennis. Usiamo SL
        # assoluto -5 cent (come harvest) + hard SL assoluto -10 cent per sport.
        "sport_stop_loss_abs": -0.05,
        "sport_hard_stop_loss_abs": -0.10,
    },
    "arb_binary": {
        "scan_enabled": False,
        "paper_enabled": False,
        # Phase CI4 (Guida 1: fee formula `rate*p*(1-p)` = MAX a 0.50 dove i
        # gap arb sono più grassi): arb_binary come TAKER in coin-flip è breakeven
        # netto (fee ~1.5c/leg su 2 leg = -3c vs gap 2-3c). Vivo solo come MAKER
        # (limit order, 0 fee + rebate 25%) — non simulabile onesto in paper (FIFO
        # queue non esiste, simuliamo fill istantaneo a best_ask = ottimistico).
        # Disabilitato in paper;拓to idle complexity senza valore.
        "enabled": False,           # Phase CI4: KILL in paper (taker fee = edge)
        "cap_pct": 0.30,           # Phase U: 25% -> 30%
        "max_single": 0.15,
        "max_positions": 3,        # Phase U: 1 -> 3
        "min_profit_abs": 0.20,    # Phase U: 0.50 -> 0.20 (micro-arb worthwhile)
        "safety_margin": 0.005,
        "max_days_to_expiry": 14,
        "scan_markets": 150,       # Phase U: 80 -> 150
        "scan_every_cycles": 1,    # Phase U: 2 -> 1 (ogni ciclo)
        # Phase CI2 (Guida 2: opera solo in mercati con >$50.000 liquidità)
        "min_market_volume_usdc": 50000.0,
    },
    "harvest": {
        "enabled": False,
        "scan_enabled": False,
        "paper_enabled": False,
        "cap_pct": 0.25,           # Phase CF: 30% -> 25% (meno rischio con WR 38%)
        "max_single": 0.10,        # Phase CF: 15% -> 10%
        "max_positions": 4,        # Phase CF: 6 -> 4
        "fav_min": 0.85,           # Phase CE: 0.78 -> 0.85 (no zone dove SL -5c = rumore)
        "fav_max": 0.95,           # Phase CE: 0.985 -> 0.95 (no near-1 dove gain minuscolo)
        "max_days_to_expiry": 30,  # Phase T: 21 -> 30
        "min_book_size": 15.0,     # Phase T: 20 -> 15
        "max_spread_ticks": 3,     # Phase T: 2 -> 3
        "scan_markets": 150,       # Phase T: 80 -> 150
        "scan_every_cycles": 1,    # Phase T: 2 -> 1 (ogni ciclo, 20s)
        # Phase CF: entry band (doppio controllo con fav_min/max)
        "entry_price_min": 0.85,
        "entry_price_max": 0.95,
        # Phase CI2 (Guida 2: solo mercati con >$50.000 volume). Harvest ha fee
        # minuscola a 0.85-0.95 (rate*p*(1-p) -> 0) + hold-to-resolution = zero
        # exit fee. filtro aggressivo su liquidità per uscite pulite.
        "min_market_volume_usdc": 50000.0,
    },
    "arb_cross": {
        "enabled": False,
        "scan_enabled": False,
        "paper_enabled": False,
        "cap_pct": 0.15,
        "max_single": 0.12,
        "max_positions": 2,        # Phase U: 1 -> 2
        "min_profit_abs": 0.50,    # Phase U: 1.00 -> 0.50
        "safety_margin": 0.01,
        "min_outcomes": 3,
        "max_outcomes": 12,
        "scan_events": 25,         # Phase U: 12 -> 25
        "scan_every_cycles": 2,    # Phase U: 5 -> 2
        # Phase CI2 (Guida 2: liquidità >$50K per uscite pulite su bundle n-leg)
        "min_market_volume_usdc": 50000.0,
    },
    # Phase W: MOMENTUM strategy (trend-following su prezzo Polymarket)
    # Phase CC: DISABILITATA — 0% WR su 4 trade, entra a prezzi estremi dove
    # move detection è rumore (5% di 0.008 = 0.0004 assoluti = rumore puro).
    # Riattivare solo dopo fix entry band 0.15-0.85 + SL assoluto + backtest.
    "momentum": {
        "enabled": False,           # Phase CC: KILL — 0% WR
        "cap_pct": 0.20,           # alto turnover, sizing significativo
        "max_single": 0.10,
        "max_positions": 3,
        "min_move_pct": 0.08,      # Phase CE: 5% -> 8% (meno rumore)
        "window_cycles": 6,        # ~2min a 20s poll (finestra breve = trend fresco)
        # Phase JJ: multi-timeframe — conferma trend su più finestre
        "windows": [3, 6, 12],     # finestre multiple: 1min, 2min, 4min
        "min_windows_confirmed": 2, # almeno 2 finestre confermano stesso verso
        "min_book_size": 30.0,
        "max_spread_ticks": 4,
        "min_days_to_expiry": 0.5,
        "max_days_to_expiry": 60,
        "scan_markets": 100,       # top mercati per volume
        "scan_every_cycles": 1,    # ogni ciclo (aggiorna price history)
        "take_profit_pct": 0.06,   # TP +6% (trend continuation)
        "stop_loss_pct": -0.05,    # SL -5% (inversione → esci)
        "stop_loss_abs": -0.03,    # Phase CD: SL assoluto -3 cent
        "entry_price_min": 0.15,  # Phase CE: no estremi
        "entry_price_max": 0.85,
        "min_volume": 2000.0,      # liquidi solo
    },
    # value-betting gated (Phase Q): disattivato
    "value": {"enabled": False},

    # Phase BB: WHALE strategy — monitora wallet istituzionali (size enorme)
    # Phase CC: DISABILITATA — 17% WR, -$6.99, entra a prezzi estremi (0.999, 0.036)
    # dove SL % triggera su rumore. Riattivare solo dopo fix entry band + SL assoluto
    # + validazione 20 trade a 3% sizing.
    "whale": {
        "enabled": False,           # Phase CC: KILL — 17% WR, entra a 0.999
        "cap_pct": 0.25,           # 25% portafoglio dedicabile a whale following
        "max_single": 0.12,        # sizing per singolo whale trade
        "max_positions": 4,        # max posizioni whale simultanee
        # scoperta whale: holders con posizione >= $25K in un singolo mercato
        "min_whale_position_usdc": 25000,
        "max_whales_tracked": 25,   # quante whale tenere in lista
        "whale_refresh_interval_sec": 3600,  # refresh lista whale ogni 1h
        # signal: whale ha fatto BUY negli ultimi N minuti
        "activity_lookback_min": 45,
        "min_whales_consensus": 1,  # min 1 whale (se >=2 whale stesso mercato = strong)
        "min_whale_buy_usdc": 5000, # compra whale >= $5K per contare come signal
        # filtri mercato (simili copy)
        "max_days_to_expiry": 60,
        "min_days_to_expiry": 0.25,
        "min_book_size": 50.0,
        "max_spread_ticks": 4,
        "min_volume": 5000.0,
        # Phase CE: entry band — no prezzi estremi
        "entry_price_min": 0.15,
        "entry_price_max": 0.85,
        # risk: TP/SL
        "take_profit_pct": 0.10,    # TP +10% (whale move puo' durare)
        "stop_loss_pct": -0.06,     # SL -6% (whale sbagliata -> esci)
        "stop_loss_abs": -0.03,    # Phase CD: SL assoluto -3 cent
        "scan_every_cycles": 3,     # ogni ~60s (3 cicli x 20s)
        "scan_markets": 60,         # top mercati per scoperta whale
    },

    # Phase DD: SNIPER HARVEST — risoluzione imminente <24h, APR astronomica
    # Phase CC: DISABILITATA — 0 trade, complessità inutile finché harvest base non profitte.
    "sniper": {
        "enabled": False,           # Phase CC: KILL — 0 trade, non validata
        "cap_pct": 0.20,
        "max_single": 0.12,
        "max_positions": 4,
        "fav_min": 0.85,
        "fav_max": 0.97,
        "min_hours_to_expiry": 1,     # min 1h residuo (no last-minute illiquido)
        "max_hours_to_expiry": 24,    # max 24h — SNIPER window
        "min_book_size": 30.0,
        "max_spread_ticks": 3,
        "scan_markets": 150,
        "scan_every_cycles": 1,       # ogni ciclo (20s) — window stretta, capture massimo
        "min_volume": 1000.0,
        "take_profit_pct": 0.05,      # early TP +5% se prezzo sale prima di resolution
        "stop_loss_pct": -0.05,       # SL -5% (reversal near-certain)
        "stop_loss_abs": -0.04,    # Phase CD: SL assoluto -4 cent
        "skip_politics": True,        # politics sorprendibili fino all'ultimo
    },

    # Phase GG: THETA / time-decay — "Will X by [date]" dove X NON successo
    # Phase CC: DISABILITATA — 0 trade, non validata.
    "theta": {
        "enabled": False,           # Phase CC: KILL — 0 trade
        "cap_pct": 0.15,
        "max_single": 0.08,
        "max_positions": 4,
        # riconosce mercati con "by <date>" / "before" nel titolo, compra NO
        "no_price_min": 0.55,
        "no_price_max": 0.92,
        "min_days_to_expiry": 2,      # >2gg per far maturare theta
        "max_days_to_expiry": 45,
        "min_book_size": 30.0,
        "max_spread_ticks": 4,
        "scan_markets": 120,
        "scan_every_cycles": 4,       # ogni ~80s (theta lento, no fretta)
        "min_volume": 2000.0,
        "take_profit_pct": 0.08,      # TP +8% (theta matura)
        "stop_loss_pct": -0.06,       # SL -6% (evento accade -> reversal)
        "stop_loss_abs": -0.04,    # Phase CD: SL assoluto -4 cent
        "min_keyword_score": 1,      # keywords "by/before/until/this month/week"
    },

    # Phase II: CONTRARIAN / fade extreme — whale VENDONO mercato estremo 0.95+
    # Phase CC: DISABILITATA — 0% WR su 3 trade, entra a 0.026-0.061 (longshot)
    # dove SL % triggera su 1 tick. Riattivare solo con entry band + SL assoluto + backtest.
    "contrarian": {
        "enabled": False,           # Phase CC: KILL — 0% WR, entra a 0.026
        "cap_pct": 0.10,
        "max_single": 0.06,
        "max_positions": 2,
        "extreme_min": 0.93,          # mercato >= 0.93 (estremo)
        "extreme_max": 0.99,
        "min_whale_sell_usdc": 8000,  # whale SELL >= $8K su estremo
        "activity_lookback_min": 60,
        "min_days_to_expiry": 0.5,
        "max_days_to_expiry": 60,
        "min_book_size": 40.0,
        "max_spread_ticks": 4,
        "scan_every_cycles": 5,       # ogni ~100s
        "scan_markets": 80,
        "min_volume": 5000.0,
        # Phase CE: entry band per il FADE (no longshot a 0.02)
        "entry_price_min": 0.10,
        "entry_price_max": 0.90,
        "take_profit_pct": 0.15,      # TP +15% (reversion estremo = grande)
        "stop_loss_pct": -0.04,       # SL -4% (estremo continua → esci presto)
        "stop_loss_abs": -0.03,    # Phase CD: SL assoluto -3 cent
    },

    # Phase MM: CROSS-MARKET ODDS — gated stub (richiede API odds esterni)
    # Confronta Polymarket vs odds esterni (the-odds-api, Kalshi, Betfair)
    # Bet quando |poly_prob - external_prob| > 2*(spread+fee)
    "cross_odds": {
        "enabled": False,            # GATED: richiede API key esterna
        "cap_pct": 0.15,
        "max_single": 0.10,
        "max_positions": 3,
        "odds_api_key": "",          # inserire chiave the-odds-api per attivare
        "min_edge_pct": 0.04,        # min 4% edge vs esterni
        "scan_every_cycles": 10,
    },
}

# Selezione wallet per categoria (scanner) — invariato
CATEGORIES = {
    "active": ["sport", "crypto", "politics", "weather", "macro", "geopolitics"],
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

# Phase Z: Wallet quality refresh frequente + swap perdenti
# Monitoraggio PIU frequente del full-scan: re-fetch win_rate solo wallet attivi
# e swap immediato se wallet non vincente (WR<0.45 o nostro copy P&L<0 su >=2 trade)
WALLET_MONITOR = {
    "quality_refresh_interval_sec": 900,  # 15min (vs 3h full rescan)
    "swap_wr_threshold": 0.45,            # WR storico Polymarket sotto questo -> swap
    "swap_min_our_trades": 2,             # min trade nostri chiusi per giudicare
    "swap_our_pnl_threshold": 0.0,        # se nostro copy P&L<0 su >=2 trade -> swap
    "reserve_pool_size": 20,              # wallet di riserva qualificati non usati
    "top_active": 15,                     # wallet attivamente monitorati (era 30, ottimizziamo)
    "enabled": True,
    # Phase KK: wallet clustering — raggruppa wallet correlati (stessi mercati)
    # per evitare over-exposure a un cluster di whale che copiano tra loro
    "clustering_enabled": True,
    "cluster_min_overlap": 3,            # min 3 mercati condivisi = stesso cluster
    "max_active_per_cluster": 3,         # max 3 wallet attivi dallo stesso cluster
    "cluster_refresh_interval_sec": 7200, # refresh cluster ogni 2h
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
    # Nessun haircut statico inventato: il paper journal usa best ask/bid e
    # profondità osservati. Un eventuale impatto oltre il best level va misurato.
    "entry_slippage": 0.0,
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
