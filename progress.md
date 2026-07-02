# Progress Log — Polymarket Copy Bot

## Session: 2026-07-02 (config aggressivo + momentum strategy)

### Contesto iniziale (dashboard post-deploy 01/07, ore 07:12)
- Equity $299.19 / $300, P&L -$0.81 (-0.27%)
- 2 aperte (Spain No @0.909, England No @0.918 = HARVEST), 2 chiuse, WR 50%
- 4 trade in 24h: 2 Alphabet Yes (COPY $7.20) + Spain/England No (HARVEST $9)
- 10 wallet fissi (auto-rescan OFF), sizing 3% = $9/trade
- Diagnosi: troppo conservativo per target doubling

### Phase R: Sizing aggressivo + tier veloce — COMPLETE
- Tier 0: 3% → 6% ($18/trade, era $9)
- Tier thresholds: 0/30/60/120 → 0/10/25/50 (scale in 1-2 giorni)
- Tier fracs: 6% → 10% → 13% → 15%
- max_open_positions: 6 → 12
- reserve_ratio: 20% → 15% (cash operativo $255 su $300)
- max_positions_per_wallet: 1 → 2
- max_positions_per_category: 2 → 4
- sizing_wr_gate: 0.55 → 0.50

### Phase S: Copy più aggressivo — COMPLETE
- banda soft: 0.25-0.75 → 0.20-0.80
- min_book_size: 50 → 25
- max_spread_ticks: 3 → 4
- min_days_to_expiry: 0.5 → 0.25
- max_entry_drift: 0.05 → 0.08
- top_wallets: 20 → 30

### Phase T: Harvest aggressivo — COMPLETE
- cap_pct: 12% → 30% ($90 deployabili)
- max_single: 8% → 15%
- max_positions: 2 → 6
- fav_min: 0.85 → 0.78 (cattura Argentina No @0.820 etc.)
- fav_max: 0.975 → 0.985
- max_days_to_expiry: 21 → 30
- min_book: 20 → 15
- max_spread_ticks: 2 → 3
- scan_markets: 80 → 150, scan_every_cycles: 2 → 1
- **Early TP +4%** (harvest_take_profit_pct): scalp mode, libera capitale

### Phase U: Arb più aggressivi — COMPLETE
- arb_binary: cap 30%, max_pos 3, min_profit $0.20, scan 150, ogni ciclo
- arb_cross: scan_events 25, scan_every 2, min_profit $0.50, max_pos 2

### Phase V: Wallet rotation — COMPLETE
- auto_rescan: False → True, interval 6h → 3h
- SCANNER: min_profit 500, min_volume 5000, min_trades 8
- markets_to_scan: 200 → 300

### Phase W: MOMENTUM strategy — COMPLETE
- Nuova strategia trend-following in strategies.py
- PriceHistory tracker (persistente in data/price_history.json)
- MomentumStrategy.update_prices() ogni ciclo, scan() rileva move >=5% in 6 cicli
- Compra lato trending (YES se salita, NO se discesa)
- TP +6% / SL -5%, cap 20%, max_pos 3, sizing 10%
- Collegata in main.py (update + scan + execute)
- Gestione posizioni in simulator.manage_strategy_positions (TP/SL/resolution)
- _open_momentum in simulator

### Phase Z: Wallet swap frequente — COMPLETE
- Nuovo wallet_manager.py: quality refresh 15min, swap perdenti con riserve
- Track per-wallet P&L dai nostri copy trade (on_copy_close hook)
- Swap: WR<0.45 o nostro P&L<0 su >=2 trade -> rimpiazza con reserve
- Test: swap_losers OK, our_pnl tracking OK

### Phase AA: Dashboard P&L — COMPLETE
- _log_close_trade: exit_price, pnl, pnl_pct, reason, strategy, hold_time, win
- UI: trade con badge PROFIT/LOSS, sezione Trade Chiusi, breakdown per strategia
- UI: wallet card con win_rate + status badge (ACTIVE/DISABLED/RESERVE)
- Test: close logging OK, dashboard API OK

### Phase X: Polling — COMPLETE
- poll_interval: 30s → 20s

### Test live (sessione 2026-07-02)
- **Import OK**: config, strategies, simulator, main — no errori
- **Config OK**: sizing_tiers 6/10/13/15%, max_open 12, reserve 15%, harvest cap 30%
- **Harvest scan**: 11 opps (era 7! fav_min 0.78 cattura Argentina No @0.820 APR 442%)
- **Arb binary**: 0 opps (mercato efficiente su top-150, atteso)
- **Arb cross**: 0 opps (raro, atteso)
- **Momentum**: 0 opps ciclo 1 (price history vuota, si riscalda in 6 cicli ~2min)
- **Esecuzione harvest**: 
  - Argentina No @0.820 → size $18.00 (6%!), entry eff 0.8325, shares 21.6 ✓
  - Spain No @0.901 → size $17.98, entry eff 0.9124, shares 19.7 ✓
  - Cash $300 → $264.02 (2 pos, reserve $45 rispettata) ✓
  - Strategy available harvest: $90.00, max_single $45.00 ✓

### 5-Question Reboot Check (post-implementation)
| Question | Answer |
|----------|--------|
| Where am I? | Phase R-W complete, test live OK, pronto deploy |
| Where am I going? | Deploy VPS + monitoraggio 24h (target +5-10% primo giorno) |
| What's the goal? | $300 → $600 in 10-14gg via sizing aggressivo + multi-strategy + wallet rotation |
| What have I learned? | Harvest 11 opps con fav_min 0.78; sizing 6% = $18/trade; momentum serve warmup |
| What have I done? | config aggressivo + momentum strategy + early TP harvest + wallet rotation |

### Files modificati (sessione 2026-07-02)
- src/config.py: BUDGET (sizing/aggressive), STRATEGY (copy aggressive), STRATEGIES
  (harvest/arb/momentum caps), SCANNER (rotation), TRACKING (poll 20s), CATEGORIES
- src/strategies.py: PriceHistory class + MomentumStrategy class + registry
- src/main.py: import MomentumStrategy + update_prices ogni ciclo + scan/execute momentum
- src/simulator.py: _open_momentum + manage momentum TP/SL + harvest early TP +4%
  + breakdown summary include momentum

## Session: 2026-07-01 (precedente, completa)
### Phase H-Q: implementato multi-strategy base (COPY+ARB+HARVEST+ARBcross)
### Deploy VPS 01/07: bot attivo, 4 trade/24h, -$0.81 (troppo conservativo)

## Session: 2026-06-30 (precedente, completa)
### Phase A-G: copy-trading base con filtri, SL/TP, backtest 89% WR

---
*Update after completing each phase or encountering errors*
