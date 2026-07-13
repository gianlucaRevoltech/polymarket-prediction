# Progress Log — Polymarket Copy Bot

## Session: 2026-07-13 TARDI (Studio 3 guide online + diagnosi COPY 0W/3L)

### Contesto post-deploy stamattina (dashboard 14:45)
- Equity $298.28 / $300, P&L realizzato -$1.72 (-0.57%), unrealized +$0.36
- 2 open (Hercog tennis +0.59%, France-Spain O/U -1.93%) | 3 closed
- COPY 0W/3L (0% WR) — trade chiusi: Iasi Herea -16.36% SL, Swiss Collignon
  -9.64% SL, France-Spain O/U -2.28% exit. Tutte entry in banda valida 0.42–0.55.
- I fix CC–CG reggono (sizing 3%, solo 4 strategie, entry band, no Kelly/trailing)
- hedge sanguinamento lento (−0.57%) ma insostenibile: a −0.5%/g x 30 = −$25

### Studio guida_modelli_online.txt (3 guide) — completa
Letta integralmente. Lezioni distillate in findings.md sez "Studio 3 guide online".

**Tesi fondamentale:** le guide descrivono un LATENCY-ARB bot su crypto 5/15-min
(bot 0x8dxd $313→$2.38M, 98% WR) — strategia DIVERSA dalla nostra (wallet-copy su
sport/politics). Non possiamo competere su latenza (20s poll vs 2.7s edge window,
Python non co-locato, paper mode). **NON pivoting** — brucerebbe budget in infra.

**Le 6 lezioni applicabili SENZA spendere:** L1 risk mgmt (daily loss limit),
L2 liquidity ≥$50K, L3 exit fee su SL/TP, L4 arb_binary dead come taker, L5 VWAP
arb (bassa), L6 copy-sport SL fix (nostro, non guide). → Phase CI1-CI5 in task_plan.

### Diagnosi COPY 0W/3L (ONESTA, non dalle guide)
- drift filter NON ha skippato: entriamo a prezzo wallet (avg_price ~price)
- NON è "ingresso tardivo vs wallet". È varianza alta del copy su sport in-play:
  SL -8% su tennis spara su break di game (10–15% move normali su risultato finale)
- Fix (CI5): SL assoluto -5 cent per copy-sport (come harvest), non -8% percentuale

### IMPLEMENTATO Phase CI1-CI5 (tutte e 5, lezioni guide online)

**Phase CI1 — Daily loss limit + halt (risk mgmt hardening)** COMPLETE
- config BUDGET: daily_loss_limit_pct=-0.08, daily_loss_warn_pct=-0.05
- simulator: _today_realized_pnl() filtra closed_positions per exit_time.date=today
  → _daily_halt_check() blocca nuove aperture (copy + execute_opportunity) se
  realized <= -8% capitale. Reset automatico a mezzanotte (date.today()).
- Persistenza data/daily_halt.json + alert DAILY_HALT/DAILY_WARN su alerts.log.

**Phase CI2 — Filtro liquidità mercato ≥$50K per harvest/arb** COMPLETE
- config STRATEGIES: min_market_volume_usdc=50000 in harvest/arb_binary/arb_cross
- strategies.py: _min_market_volume_usdc() helper; Opportunity.market_volume
  popolato in scan di ArbBinary/Harvest/ArbCross da gamma volumeNum
- simulator.execute_opportunity: gate hard su opp.market_volume < min_mv → skip
- copy NON filtrato (segue wallet, mercato gia scelto)

**Phase CI3 — Fee taker su USCITA (SL/TP) nel simulatore** COMPLETE
- simulator._exit_fee_adjusted(pos, exit_price, reason): deduce taker fee su uscita
  (reason != resolved). close_by_asset + _close_by_pid + log ora usano exit_eff.
- pnl mostrato è NETTO delle fee di ingresso + uscita. stampato fee_note su log.
- resolved (settile $1/$0) → NO exit fee (no crossing book).
- Sport a 0.50: uscita costa ~1.5%/leg = -$0.13/trade ora correttamente dedotto.

**Phase CI4 — arb_binary DISABLE in paper** COMPLETE
- config STRATEGIES.arb_binary.enabled=False (taker fee = edge in coin-flip).
- _should_scan gia gestiva enabled gate (Phase CC) — no code change ulteriore.
- Spiega i 0 opp: taker fee = MAX privo dove gap piu grassi; maker non simulabile.
- Arricchito anche arb_binary di min_market_volume_usdc per futura riattivazione.

**Phase CI5 — copy-sport SL assoluto -5c (fix 0W/3L tennis in-play)** COMPLETE
- config STRATEGIES.copy: sport_stop_loss_abs=-0.05, sport_hard_stop_loss_abs=-0.10
- simulator._copy_sl_tp_decision(pos, cur, sl, tp): branch sport (SL assoluto cent)
  vs altri (SL percentuale legacy). reconcile: entrambi i branch usano helper.
- Test: sport cur -2.8c=hold, -5.8c=stop_loss, -10.8c=hard_sl; other -8.1%=stop_loss.

### Sintesi modifiche codebase
- src/config.py: BUDGET (daily_loss_limit/warn), STRATEGIES.arb_binary (enabled=False
  + min_market_volume_usdc), STRATEGIES.harvest/arb_cross (min_market_volume_usdc),
  STRATEGIES.copy (sport_stop_loss_abs + sport_hard_stop_loss_abs)
- src/strategies.py: Opportunity.market_volume; _min_market_volume_usdc() helper;
  filter volume + populate market_volume in ArbBinary/Harvest/ArbCross scan()
- src/simulator.py: import `date`; daily_halt tracking + persistence;
  _today_realized_pnl / _daily_halt_check / _exit_fee_adjusted / _copy_sl_tp_decision;
  close_by_asset + _close_by_pid usano exit_eff (fee netto); reconcile usa helper;
  open_position + execute_opportunity gated su daily halt + market_volume

### Validazione
- py_compile su config/strategies/simulator/main/categories: PASS
- Import di main.Bot (dipendenze cascata): PASS
- Test funzioni critiche: CI1/CI3/CI5 — PASS
  * CI5: sport -2.8c=hold, -5.8c=stop_loss, -10.8c=hard_sl
  * CI3: sport exit 0.650 -> 0.643 (fee 1.05%); resolved no fee; other = 0 fee
  * CI1: daily_halt_check con 0 realized oggi = False (aspettato)

### Files modificati (sessione 2026-07-13 tardi)
- findings.md: sez "Studio 3 guide online" + diagnosi copy-sport (append)
- task_plan.md: Phase CI1-CI5 marker COMPLETE + Decisions Made (aggiornato)
- progress.md: questa sessione
- src/config.py, src/strategies.py, src/simulator.py (codice)

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
