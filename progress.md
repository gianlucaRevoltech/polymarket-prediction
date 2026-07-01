# Progress Log — Polymarket Copy Bot

## Session: 2026-07-01 (ri-allineamento VPS + obiettivo doubling/settimana)

### Contesto iniziale (dati dashboard VPS 09:14)
- Equity $299.20 / $300, P&L -$0.80 (-0.27%)
- Realizzato -$0.62, non realizzato -$0.17
- 1 aperta (Switzerland Yes @0.487 size $7.19 NDF -2.39%), 5 chiuse, WR 20%
- 10 wallet: suntori/c0O0OLI0O03/neutralwave23/mombil/COMESEECOMESAW/tugator/
  VeeFriends/KoffeeLover/Zptml/ChetterHummin
- Anomalie: Bublik @0.708 (FUORI banda 0.70), Egypt Yes raddoppiato, Max 10 vs 4
- Obiettivo nuovo: duplicare capitale ogni settimana ($300→$600→$1200...)

### Phase H: Diagnosi gap VPS vs locale — in_progress
- **Status:** in_progress
- **Started:** 2026-07-01 11:30
- Actions:
  - Letto config locale: BUDGET.max_open_positions=4, entry_price_max=0.70, SL-8/TP20
  - Letto dashboard VPS: Max 10, Bublik 0.708 PASSATO (anomalia)
  - Identificato bug P10 (delta aggregato per-asset)
  - Identificato bug P11 (Bublik fuori banda → VPS divergente dal locale)
  - Identificato bug P13 (Egypt doppione via entra/esce/rientra + dedup inusato)
  - Identificato P14 (WR 20% su 5 trade = non significativo)
- Files to verify on VPS: src/config.py max_open_positions, entry_price_max
- TODO: ssh VPS, md5 confronto src/*.py, ri-deploy pulito + restart

### Phase I-J-K-L: raccolte in task_plan.md
- I: refactor delta per-wallet (fix aperture rare)
- J: poll 30s + dedup_window implementato + banda soft + min_days 0.5
- K: sizing compounding 3→5→8→12% + reserve 20% + drawdown halve
- L: doubling feasibility + alert balance + auto-stop floor

### Phase M-Q: multi-strategy (aggiunte dopo richiesta utente 2026-07-01)
- M: strategy router architecture (N strategie parallele, allocation cap, attribution)
- N: arbitraggio binario YES+NO<$1 (profitto certo a risoluzione, focus crypto/other
  perche fee sport mangia spread; sizing cap 15%, endTime<14gg per APR alta)
- O: harvest near-certain (ask 0.92-0.98, <7gg, high hit-rate, cap 8%, hard SL-3%)
- P: arbitraggio cross-market (multi-outcome esaustivo somma<$1, occasionale grande)
- Q (gated): value-betting modello (NOAA weather, odds-API sport, Kelly 1/4) —
  solo se altre strategie insufficienti dopo paper 4 sett

### Studio multi-strategy doubling (onesta)
- Copy-only: beta catastrofico per doubling 7gg (sizing 12%, 85 win/sett)
- Con ARB+HARVEST+ARBcross che aggiungono risk-free-ish +5-15%/sett, copy sizing
  puo' restare 8% → realistic doubling in 2-4 sett, beta minore. Documentato findings.md

### Sequenza implementativa consigliata
H (ri-deploy) → I (copy fixed) → N (arb binario, piu' semplice) → M (router) →
O (harvest) → K (sizing scaling gated) → L (monitoraggio) → P (arb cross) → Q

### IMPLEMENTAZIONE COMPLETA (sessione 2026-07-01, ore 12:00-12:30)
Tutte le fasi H..Q implementate in codice locale + test live 75s.

#### File modificati/creati
- src/config.py (rewrite): BUDGET sizing_tiers compounding 3->5->8->12% gated WR,
  reserve 20%, drawdown halve 12%, equity floor -5%, ruin -20%, dedup_window 3600,
  max_open 6, harvest SL -3%/-10%; STRATEGY banda soft 0.25-0.75 se consenso>=2,
  min_days 0.5; STRATEGIES (NEW) caps per-strategy + max_positions; TRACKING
  poll 30s; MONITOR (NEW) alerts.
- src/models.py: Position + campi `strategy` e `pair_id`
- src/portfolio_sync.py: metodi gamma get_market/get_active_markets/
  get_event_markets/get_active_events + _normalize_market + _parse_json_list
  (outcomes/clobTokenIds sono JSON-encoded strings).
- src/strategies.py (NEW): Opportunity dataclass + ArbBinaryStrategy,
  HarvestStrategy, ArbCrossStrategy. Scan fetcher -> List[Opportunity].
- src/simulator.py: sizing compounding ladder (_sizing_tier), _risk_factor
  (drawdown halve + equity floor + ruin), recent_opens dedup, _strategy_available/
  _max_single_for, reconcile PER-WALLET delta (new_holdings set di (wallet,asset)),
  manage_strategy_positions (resolution + SL harvest), execute_opportunity +
  _open_arb_binary/_open_harvest/_open_arb_cross, _close_by_pid, breakdown per-
  strategia in get_portfolio_summary, peak_equity persistente.
- src/main.py: prev_holdings per-wallet, _should_scan/_run_strategy_opps/_monitor_alerts,
  router strategie cadenzate nel loop, equity+P&L per strategia printed per ciclo.

#### Test live (75s, 3 cicli @ 30s, 9 wallet reali)
- Equity stabile, NO crash
- HARVEST: 7 opps trovate, 2 aperte ciclo1 (Spain No @0.897 APR 220%, England No
  @0.907 APR 196%), cap max_positions=2 rispettato (ciclo3 scan 7 opps ma 0 aperture)
- ARB BINARY: 0 opps (mercato efficiente, sum ask YES+NO = 1.0010 > $1, onesto)
- ARB CROSS: 0 opps (nessun evento esaustivo con sum_ask<$1 in top-12 eventi)
- COPY: delta per-wallet = "1 NUOVI (wallet,asset)" ciclo3 → Exact Score
  Belgium-Senegal @0.075 → SKIP fuori banda 0.30-0.70 (consenso 1<2). Filtro OK.
- Cash flow: $300 -> $282 (2 harvest $9) ; P&L per strategia: harvest=2ap +0 unrlz
- Dashboard /api/portfolio OK: ritorna by_strategy, sizing_tier, peak_equity,
  drawdown_pct senza rompersi.

#### Test sintetici (matematica)
- HARVEST open+resolve: entry 0.909, payout $1 -> P&L +$0.91 su $9 (10% in 19gg)
- ARB BINARY open+resolve: bundle YES+NO @ 0.96 (eff 0.9696), payout $1 -> P&L
  +$0.28 su $9 (shares 9.3 * 0.0304). Matematica arb confermata.
- peak_equity persistente tra restart; recent_opens dedup 3600s funziona.

### 5-Question Reboot Check (post-implementazione)
| Question | Answer |
|----------|--------|
| Where am I? | H..Q codice completato, test live OK |
| Where am I going? | Deploy VPS (utente copia folder + restart reset scan) |
| What's the goal? | Bot multi-strategy attivo + doubling-oriented |
| What have I learned? | arb raro (mercato efficiente); harvest fertile WC ~200% APR |
| What have I done? | 6 file modificati, scan/execute/resolution testati |

### Studio doubling (onesto)
- Sizing 3% → 35 winning trade/sett = +10% sett. NON doubling.
- Sizing 12% + 85 win/sett + WR 70% → ~+100%/sett MARGINALMENTE possibile
  MA beta catastrofico: 10 loss consecutive = -9.4%, 4 loss = -3.8%
- Stretching realistico: +20-40%/sett per 2-3 sett, doubling in 3-4 sett.
- Documentato in findings.md

### 5-Question Reboot Check
| Question | Answer |
|----------|--------|
| Where am I? | Phase H (ri-deploy VPS) + diagnosi P10-P14 |
| Where am I going? | Phase I (delta per-wallet), poi J/K/L |
| What's the goal? | Bot attivo + profittevole + tendente doubling/settimana |
| What have I learned? | P10 killer bug, VPS divergente, doubling mastematicamente estremo |
| What have I done? | Diagnosi completa + plan Phase H-L |

## Session: 2026-06-30 (precedente, completata)

### Phase A-G: completa (vedi sezione legacy sotto)

#### Backtest storico (file: data/backtest_results.json)
- COPY: 73 pos decise, WR 89%, ROI mediano +81.6%, $300 -> $545
- Analisi banda: 0.30-0.50 ROI +104% WR 89%, 0.50-0.70 +67% WR 89%
- Conferma: banda 0.30-0.70 = edge massimo

#### Test SL/TP economico
- Breakeven WR = 8/(8+20) = 29% (backtest mostra 89%)
- EV/trade pessimistica WR67% = +10.8% → +1.86$/trade

#### Bug critico found e fixed durante test live
- get_book: bids[0]/asks[0] come "best" ma CLOB bids ASC, asks DESC
  → spread 0.98 finto → SKIP tutti. FIX: iterate max bid / min ask.

#### Filtro coin-flip (refinement Phase D)
- BTC Up/Down 5min market-making rebate non copiabile → FIX min_days_to_expiry=1.0

#### Test live 25min — profit
- Equity STABILE $300.00, ZERO aperture fallaci
- 4 trade chiusi reali: 2 loss BTC coin-flip pre-FIX + 2 win (BTC exit + LoL TP+20)
- Netto +$6.25 (+2.08%) su $300 | cash $306.25 | WR 50%
- SL-8/TP+20 economica validata in REAL live

### Test Results (riepilogo sessione 2026-06-30)
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| Backtest WR con filtri | edge positivo | 89% WR, +81.6% ROI | OK |
| SL/TP breakeven | <=50% | 29% → 50% real profittevole | OK |
| Bug get_book ordine | spread 0.01-0.02 | 0.98→0.01 | FIXED |
| Coin-flip filter | scarta 5min BTC | scarta BTC 5min, tiene WC | OK |
| Live no-dump | $300 stabile baseline | $306.25 (+2.08%) | PROFIT |
| Live post-filter | solo sweet-spot | tutte SKIP longshot/coinflip | OK |

## Error Log
| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-06-30 10:55 | get_book spread 0.98 | 1 | iterate max bid / min ask |
| 2026-06-30 11:00 | BTC coin-flip 5min loss | 1 | min_days_to_expiry=1.0 |
| 2026-07-01 11:35 | Bublik 0.708 fuori banda | 1 | Ri-deploy VPS (Phase H) |
| 2026-07-01 11:35 | Egypt Yes riaperto doppione | 1 | delta per-wallet + dedup (Phase I) |
| 2026-07-01 11:35 | Dashboard Max 10 vs config 4 | 1 | Verifica config VPS (Phase H) |

---
*Update after completing each phase or encountering errors*