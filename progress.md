# Progress Log — Polymarket Copy Bot

## Session: 2026-07-13 (FIX EMERGENZA PERFORMANCE: -5.63%, WR 24%)

### Contesto: dashboard 07/07 mostra disastro
- Equity $283.11 / $300, P&L -$16.89 (-5.63%)
- WR 24% (6W/19L), 19/25 chiusi sono STOP LOSS (76%!)
- Whale: 17% WR -$6.99 | Momentum: 0% WR -$4.20 | Contrarian: 0% WR -$3.00
- ROOT CAUSE: bot entra a prezzi estremi (0.999, 0.036, 0.026) dove SL %
  triggera su rumore di mercato, non su fallimento segnale. Risk/reward invertito.

### Phase CC: Triage — KILL strategie perdenti — COMPLETE
- config.py: enabled=False su whale, momentum, contrarian, sniper, theta
- main.py _should_scan(): gate con STRATEGIES[name].get('enabled', True)
- Attive ora: copy + harvest + arb_binary + arb_cross (solo 4, erano 9)
- 4 posizioni whale aperte (near-certain) lasciate risolvere naturalmente

### Phase CD: SL assoluto (cent) per prezzi estremi — COMPLETE
- simulator.py: stop_loss_abs implementato per harvest, momentum, whale, directional
- Logica: if (cur - entry) <= stop_loss_abs: close (SL in cent, robusto a estremi)
- Harvest: SL -5 cent (era -4% = 3.9 cent a 0.985 = rumore)
- Harvest soft_exit: -15 cent absolute (black-swan)
- Momentum/Whale/Directional: SL -3/-4 cent + fallback SL %

### Phase CE: Entry price bands — no prezzi estremi — COMPLETE
- Whale: entry_price_min 0.15, max 0.85 (era 0-1 = comprava a 0.999!)
- Momentum: entry 0.15-0.85 + min_move 5%->8% (5% di 0.008 = rumore)
- Contrarian: entry 0.10-0.90 sul fade side (era 0.026 = longshot)
- Harvest: fav_min 0.78->0.85, fav_max 0.985->0.95
- strategies.py: filtro entry band in scan() di whale, momentum, contrarian

### Phase CF: Harvest hold-to-resolution — COMPLETE
- harvest_take_profit_pct 0.04 -> 0.0 (early TP disabilitato)
- Harvest ora hold-to-resolution per payout $1 pieno (l'edge reale)
- Harvest cap 30%->25%, max_positions 6->4, max_single 15%->10%

### Phase CG: Sizing conservativo — COMPLETE
- sizing_tiers: 6% -> 3% base, tier1 5%, tier2 8%, tier3 10%
- max_open_positions: 12 -> 8
- reserve_ratio: 15% -> 20%
- max_position_size: 6% -> 3% floor
- kelly_enabled: True -> False
- trailing_stop_enabled: True -> False (triggera su rumore)
- sizing_wr_gate: 0.50 -> 0.45

### Test live (sessione 2026-07-13)
- Bot instanzia OK, no crash
- Strategie gated: copy/arb_binary/harvest = scan_enabled=True
  momentum/whale/sniper/theta/contrarian = enabled=False (skip)
- Sizing 3% = $8.49/trade (era $18 a 6%)
- Entry band harvest 0.85-0.95 (era 0.78-0.985)

### Files modificati (sessione 2026-07-13)
- src/config.py: BUDGET (sizing conservativo, SL abs, Kelly/trailing off),
  STRATEGIES (enabled=False su perdenti, entry bands, stop_loss_abs)
- src/main.py: _should_scan() gate enabled
- src/simulator.py: manage_strategy_positions SL assoluto per tutte le direzionali
  + harvest hold-to-resolution
- src/strategies.py: entry band filter in whale/momentum/contrarian scan()

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

### Phase BB: WHALE strategy — COMPLETE
- Nuova WhaleStrategy: discover whale (25K+ shares holder) + follow BUY recenti
- 25 whale scoperte da top 60 mercati, refresh ogni 1h
- Signal: BUY whale >= $5K negli ultimi 45min, consenso per conditionId
- Score = n_whales * total_usdc_buy (conviction istituzionale)
- TP+10%/SL-6%, cap 25%, max_pos 4, scan ogni 60s
- Test live: discovery OK, signal detection OK (Spain Yes, Egypt No con threshold test)
- UI: whale badge teal, breakdown include whale

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
