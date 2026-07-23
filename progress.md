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

### MODULO LATENCY ARB Step 0 (validazione paper su feed reali)

**Contesto:** VPS IONOS Germania gia nota all'utente → Step 1 VPS = €0 aggiuntivo.
Gateway: serviva validare edge Guida 2 (Polymarket lagga ~2.7s vs Binance su
contratti crypto 5/15min) prima di investire un euro. Modulo standalone, nessun
integrazione nel main loop (che polla a 20s): Step 0 vuole loop 1s dedicato.

**Cosa è stato fatto (Step 0):**
- Creato `src/latency_arb.py` (~300 righe):
  * BinanceFeed: REST polling BTCUSDT/ETHUSDT (last price + klines 5min per
    momentum delta). No API key, no websocket.
  * PolymarketContractFeed: gamma markets con pattern "bitcoin/eth up or down",
    filter su scadenza 0.5–15 min.
  * LatencyArbDetector: expected_p(UP)=0.5+K·delta_5min, edge vs p_yes,
    signal when |edge|>10pt. Logging su jsonl; resolve auto a expiry con esito
    reale da gamma → WR virt + P&L virt + bucket edge
  * Stats persistente `data/latency_arb_stats.json`, opt-in `POLYMARKET_INSECURE=1`
- Aggiunto `certifi>=2024.7.4` a requirements.txt (trust store VPS)
- py_compile OK. Smoke test locale: Binance OK, gamma 403 Cloudflare da Windows
  (.eulerAngles regionale — su VPS funge)

**Validazione da fare (VPS, Step 0):**
- Deploy modulo su VPS IONOS: git pull, pip install -r requirements.txt,
  nohup python -u src/latency_arb.py > logs/latency_arb.log 2>&1 &
- Run 5–7 giorni, target 200+ signal resolved con WR virt > 70% (Guida 2)
- Logbook + stop conditions in `ARBITRAGE_LATENCY_PLAN.md`

### INTEGRAZIONE start_all.sh — avvio+stop+reset del validatore

**start_all.sh** ora gestisce anche `latency_arb.py` come terzo servizio:
- **start**: nohup avvio + PID in `data/latency_arb.pid` + log `logs/latency_arb.log`
- **stop**: kill_pidfile + pkill fallback `src/latency_arb.py`
- **reset** (clear_trading_state): azzera anche `latency_arb_signals.jsonl`,
  `latency_arb_stats.json`, `daily_halt.json` + log validator vecchi
  (i signal "open" residui non si riferiscono al nuovo run)
- **status**: mostra stato di bot / dashboard / latency_arb
- **logs**: tail live di bot.log + dashboard.log + latency_arb.log

**Deploy Step 0 su VPS IONOS (un solo comando)**:
```
git pull
./start_all.sh restart reset scan
```
→ install deps (certifi nuovo), reset stato vecchio, scan wallet, avvia in
  parallelo bot + dashboard + validatore. Log di domani su `logs/latency_arb.log`
  + stats in `data/latency_arb_stats.json`.

### Files modificati (sessione 2026-07-13 tardi)
- findings.md: sez "Studio 3 guide online" + diagnosi copy-sport (append)
- task_plan.md: Phase CI1-CI5 marker COMPLETE + Decisions Made (aggiornato)
- progress.md: questa sessione
- **ARBITRAGE_LATENCY_PLAN.md (NUOVO)**: roadmap Step 0/1/2/3 con spesa,
  target, stop conditions, logbook vuoto da riempire dopo ogni step
- src/config.py, src/strategies.py, src/simulator.py (codice Phase CI1-CI5)
- src/latency_arb.py (NUOVO Step 0 validator), requirements.txt (certifi)

## Session: 2026-07-17 (LATENCY-ARB — fix resolver rotto + outcomes per nome + stats heartbeat)

### Contesto — output VPS utente (2 giorni di segnali post-fix discovery)
- `wc -l data/latency_arb_signals.jsonl` = 4032
- `cat data/latency_arb_stats.json` = **No such file or directory**
- `grep -c RESOLVE logs/latency_arb.log` = **0**
- `grep -c SIGNAL logs/latency_arb.log` = 2019
- log tail: `[LATENCY-ARB STATS] resolved=0 | WR=0.0% | P&L virt=$0.000 | pending=6`
  costante nel tempo
- 2 es SIGNAL LONG_YES su BTC/ETH con edge +0.14/+0.18 e Δ5m Binance -0.24%/-0.06%
  (contraddizione: edge LONG_YES ma momentum ribassista → già indizio outcomes[0]=DOWN)

### Diagnosi (3 bug)
- **Bug #1 CRITICAL**: `resolve_contract` parseva `outcome`/`resolutionSource`
  come free-text — gamma non espone questi campi per crypto up/down. Risolve
  zero contratti. Campo corretto: `outcomePrices` (JSON-encoded), index max
  = vincitore.
- **Bug #2**: `token_yes = tokens[0]` assumeva outcomes[0]="Up". Polymarket
  ordina spesso alfabetico → tokens[0] = token DOWN → p_yes era p(DOWN).
  Era il sospetto anticipato in `progressi.txt`.
- **Bug #3**: `stats.json` mai scritto senza RESOLVE — `_save_stats()` solo
  dentro `_resolve_pending` post-resolve.

### Fix applicati (src/latency_arb.py)
- `resolve_contract`: parse `outcomePrices` (JSON-encoded). Trovo index max
  prezzo; se hi>=0.95 e lo<=0.05 → vincitore = outcomes[hi_idx]. UP_won sse
  "up"/"yes" in nome; DOWN_won sse "down"/"no". Non assume outcomes[0]=Up.
- `scan_cycle`: match outcomes per NOME via `_find_outcome_idx(outcomes,
  ("up","yes"))` / `("down","no")`. token_up = tokens[up_idx]. p_up_market =
  book_yes(token_up). edge = expected_up - p_up_market. entry_price corretto.
  Signal record ora include `up_idx`, `down_idx`, `outcomes`, `p_up_market`
  (+ alias legacy `p_yes`).
- Aggiunto helper `_find_outcome_idx(outcomes, needles)`.
- Aggiunto `heartbeat_save_stats()` — salva stats.json ogni 60 cicli (~1min)
  con `pending` count + `ts_last_save` — visibile anche prima del primo RESOLVE.
- Loop `run_loop`: chiamata a `det.heartbeat_save_stats()` ogni 60 cicli.

### File nuovo: `tools/debug_resolver.py`
Dumpa i campi gamma RAW per condition_id scaduti (pescati da signals.jsonl o
fallback query gamma closed=true). Stampa anche la derivazione del nuovo
resolver. Da lanciare su VPS **prima** del deploy per confermare che i campi
gamma matchano la mia assunzione (outcomePrices JSON-encoded, hi_idx prezzo >=0.95).

### Decisione: NON tunare K ora (per ora)
Lo scenario `progressi.txt` diceva "dopo primi RESOLVE: se WR>60% K OK, se
~50% outcomes flipped, se <40% K off". **N=0 RESOLVE non ci permette ancora
nessuna delle 3 conclusioni**. Prima fix resolver → 24-48h → 20-30 RESOLVE →
allora delibera K/outcomes. Identico protocollo, solo sbloccato il resolver.

### Validazione
- `python -m py_compile src/latency_arb.py` → OK
- `python -m py_compile tools/debug_resolver.py` → OK
- Test live su VPS da fare (utente):
  1. `git pull` (VPS)
  2. `python tools/debug_resolver.py --max 5` — conferma campi gamma
  3. Se `outcomePrices` compare come ["1","0"] etc → fix corretto, deploy:
     `./start_all.sh restart reset scan` (reset azzera signals.jsonl inquinato
     dai 4032 record del run buggy)
  4. Verifica dopo ~10min: `cat data/latency_arb_stats.json` deve esistere con
     `pending: N, ts_last_save: ...` anche prima del primo RESOLVE
  5. Dopo 24-48h: se RESOLVE count > 20 → analisi WR per decidere K

### Files modificati (sessione 2026-07-17)
- src/latency_arb.py (resolve_contract rewrite + scan_cycle outcomes-per-nome
  + _find_outcome_idx helper + heartbeat_save_stats + loop call)
- tools/debug_resolver.py (NUOVO debug script)
- findings.md (sez "LATENCY ARB Step 0 — Bug resolver (2026-07-17)")
- task_plan.md (Phase CJ1.5 fix resolver)
- progress.md (questa sessione)
- ARBITRAGE_LATENCY_PLAN.md (logbook entry 2026-07-17)

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
### Phase A-G: copy-trading base con filtri, SL/TP, profiler storico (claim 89% ritirato)

## SESSIONE 2025-07-21 (lunedì) — Check VPS post-weekend, inversione trend latency-arb

> **CORREZIONE 2026-07-22 (vedi sessione sotto): l'interpretazione "finestra
> recente 53.4%" di questa sessione è SBAGLIATA.** I 731 resolved NON sono la
> continuazione dei 396 del weekend: le stats sono state azzerate il 20/07 e
> i 731 provengono dal modello v2 (commit c244c67). Prova aritmetica nella
> sessione 2026-07-22 e in findings.md.

### Output VPS (incollato da utente)
- 3 servizi UP: bot PID 2640740, dashboard 2640723, latency_arb 2640761
- LATENCY-ARB: **731 resolved** | WR **43.2%** | P&L virt **+$135.740** (net +$104.896) | pending 2
  - bucket win_10_20: 298/698 = 42.7%
  - bucket win_20_plus: 18/33 = 54.5%
- BOT: Equity **$292.21 (-2.60%)** | Aperte 5 | Chiuse 8 (WR 0%) | tier 3% dd 2.6%
- SYSTEM: RAM 1.6/7.7Gi OK, disco 6% OK

### Confronto con analisi weekend (20/07)
| Metrica | Weekend (396 res) | Lunedì (731 res) | Delta ultimi 335 |
|---------|-------------------|------------------|------------------|
| WR tot  | 34.6%             | 43.2%            | **53.4%** (179W/335) |
| P&L virt| -$17.115          | +$135.740        | **+$152.86** |
| bucket 10-20 | 34.7%      | 42.7%            | ~51% |
| bucket 20+   | 33.3% (9/27)| 54.5% (18/33) | 6/6 nuovi win (n=6, rumore) |

Inversione netta rispetto al verdetto "NO EDGE" del weekend. Possibili cause:
(a) regime di mercato cambiato, (b) codice/config cambiato su VPS fra domenica e
lunedì (git pull? restart? K?), (c) artefatto nel resolver/stats. DA VERIFICARE
prima di qualsiasi decisione: split BTC/ETH + LONG_YES/NO sulla finestra recente,
e `git log` sulla VPS per escludere (b)/(c).

### Bot copy-trading: peggioramento
Equity $300.02 → $292.21. 8 chiuse a WR 0% = tutte perdenti (~-$7.80).
Non catastrofico (-2.6%, dentro tier 3% dd) ma serve dettaglio: quale strategia,
quali reason (SL/exit/resolved).

### Nota importante
Tutto PAPER: latency-arb è validatore virtuale ($0 reali), bot gira su simulatore
$300. Nessun capitale reale perso. "Molto in perdita" = -2.6% paper bot.

### Decisione sospesa
Tabella PROSSIMI_STEP_LUNEDI: 731 resolved, WR 43.2% → zona "strategia non
funziona" (<45%), MA finestra recente 53.4% + P&L positivo → zona "edge
confermato" (>52%). Evidenza conflittuale → protocollo borderline:
**allungare a bucket 20+ ≥ 50-100 trade** prima di decidere soglia/kill.

---
*Update after completing each phase or encountering errors*

---

## SESSIONE 2025-07-20 — Analisi log weekend VPS (post-scarico via git)

### Setup
- Ricevuti via git push da VPS i log weekend in `logs_weekend/` (force-add)
- `latency_arb.log` 19405 righe, `bot.log` 82054, `dashboard.log` 676, `latency_arb_stats.json` 396 resolved
- Creato `tools/split_analysis.py` per join SIGNAL→RESOLVE (regex RESOLVE fix: tolleranza spazi extra `WIN  |`)

### Risultati chiave
- **LATENCY-ARB**: 396 resolved, WR **34.6%**, P&L -$17.115, soglia 0.10
  - Bucket 10-20: WR 34.7% (128/369) | Bucket 20+: WR 33.3% (9/27)
  - BTC WR 30.5% (60/197) | ETH WR 38.7% (77/199)
  - LONG_YES WR 34.4% | LONG_NO WR 34.8% → no bug direzione
  - **Tutti i bucket <40% → NO EDGE.** Tesi latency arb non regge, confermato a 396 trade
- **BOT**: equity $300.00→$300.02 (+0.01%), 14 chiusure WR 36%, copy P&L -$0.18, 6 aperte harvest
- **DASHBOARD**: UP, 200 OK su /api/status ed /api/equity, nessun traceback

### Timestamp anomalo
Log interni scrivono "20/Jul/**2026**" → orologio VPS fuori sync di 1 anno. Per i numeri non conta, per le date sì. Da fixare in STEP 6.

### Prossima azione (richiede decisione utente)
Verdetto STEP 2 → "strategia non funziona, vai a Step 5". Da discutere:
- **5a** alzare soglia 0.15/0.20 → **scartato a priori** (bucket 20+ è 33%, peggiore di 10-20)
- **5b** finestre Δ15m/Δ30m invece di Δ5m → prova concreta, va implementata
- **5c** filtro liquidità (top-50 markets) → prova concreta
- **5d** abbandona latency arb, lascia solo copy/harvest bot a girare → opzione default
- **5e** dashboard realtime /latency → utile per monitoraggio futuro

Recommendazione proposta: STOP latency-arb ora, lasciando girare solo il bot copy/harvest (ha equity piatta, non perde), e spendere energie su 5c+5e oppure 5d puro.

---

## SESSIONE 2026-07-22 — "Inversione" latency-arb spiegata: reset 20/07 + modello v2 (NON continuazione)

### Contesto
Output VPS incollato da utente (731 resolved, WR 43.2%, +$135.74 virt / +$104.90
net; bot $292.21 -2.6%, 8 chiuse WR 0%) + screenshot dashboard incoerente
($298.21, 1 chiusa, ultimo agg. 14:06). Utente percepisce "molto in perdita".

### Scoperta 1 — I 731 resolved sono TUTTI del modello v2, stats azzerate il 20/07
Prova aritmetica (nessun accesso VPS necessario):
- **Bucket impossibile**: win_20_plus lunedì 18/33 vs weekend 9/27. Come
  continuazione il delta sarebbe 9 win su 6 trade → impossibile.
- **Fee impossibili**: gap lordo-netto lunedì = $30.84. Su 731 trade = $0.042/trade
  (coerente: fee=0.07*(1-entry) → entry medio ~0.40). Come continuazione, i 335
  "nuovi" dovrebbero aver pagato (135.74-(-17.11)) - 104.90 = $47.96 = $0.143/trade
  → sopra il massimo fisico della formula (0.07). Assurdo.
- Il log stampa `[LATENCY-ARB STATS] v2` e P&L "net=" — formato che esiste SOLO
  nel v2 (commit c244c67, 20/07 11:00 "Rewrite latency_arb v2: strike+vol model").
⇒ Sequenza reale: 20/07 git pull v2 + `restart reset` → stats azzerate → 731
resolved accumulati dal v2 in ~24-48h (volume plausibile: contratti 5min, loop 1s).

### Scoperta 2 — La sessione 21/07 in progress.md era sbagliata
"Finestra recente 53.4% WR" non esiste: era il delta aritmetico tra due run
NON confrontabili (v1 pre-reset vs v2 post-reset). Corretta con nota in cima
alla sessione.

### Scoperta 3 — Il quadro v2 reale (dal solo output aggregato)
- WR 43.2% con entry medio implicito ~0.36-0.40 → breakeven ~36.5% → **edge
  apparente +6.7pt**, EV netto ~+$0.14 per $1 size (+14%/trade).
- Numeri MOLTO sopra le attese → red flag da auditare: entry al best_ask a
  0.5-3min dalla scadenza su book sottili (ask fantasma?), midpoint fallback
  ottimistico, possibile doppio conteggio da stale/re-detect.
- Bucket v2: win_10_20 42.7% (298/698), win_20_plus 54.5% (18/33, n piccolo).

### Scoperta 4 — Bot e dashboard: nessun mistero
- Il reset del 20/07 ha azzerato anche il portfolio bot → riparte da $300.
- Dashboard vista dall'utente = tab stale del 20/07 14:06 (1 chiusa, $298.21).
- Stato reale lunedì: 8 chiuse tutte LOSS (-$7.79, -2.6%), 5 aperte. Le 4 harvest
  Fed (No @0.946 x2 + Yes @0.943 x2) sono lo STESSO bet duplicato su 2 mercati
  (cluster exposure li vede come 2 eventi). Dettaglio delle 8 chiusure: servono
  log freschi (comandi consegnati all'utente).
- SSH diretto dalla macchina locale fallito (Permission denied publickey) →
  dati freschi via git push dall'utente, come il 20/07.

### Decisione (aggiorna la "Decisione sospesa" del 21/07)
Criterio v2 dichiarato in ARBITRAGE_LATENCY_PLAN ("edge NETTO > 0 a 100+
resolved") è NOMINALMENTE superato (731 res, +$104.90 net). MA prima di Step 1:
1. Audit per-record con tools/analyze_signals.py (calibrazione p_model, Brier,
   split strike_source/z/asset/direzione) sui dati freschi
2. Verifica realismo fill (best_ask reale vs midpoint fallback)
3. Dettaglio 8 chiusure bot
Blocco comandi VPS consegnato in chat. Prossima sessione: analisi output.

### File modificati (sessione 2026-07-22)
- progress.md (correzione sessione 21/07 + questa sessione)
- findings.md (sez "Latency-arb v2: reset 20/07 spiega l'inversione")
- task_plan.md (Phase CJ1.6 audit v2)

---

## SESSIONE 2026-07-22 (parte 2) — Audit v2 → Step 5d STOP

### Input
- Output VPS blocco comandi (git/restart/analyze_signals/8 chiusure) + push
  `logs_monday/` (faa3b24): 1810 signal, 738 resolved, tutti model_version=2.
- Servizi avviati 20/07 09:40 (conferma reset).

### Audit (`tools/audit_v2.py` — NUOVO)
- Top-10 win = 113.9% del P&L netto; trimmed top-5% win = **-$54**
- Bootstrap CI EV/trade include 0; trimmed include 0
- entry≥0.25 & edge≥0.15: n=80, CI include 0
- Strike: 738/738 binance_open; equity API 403
- Fill: 20 win entry<0.15 = +$187 (gonfiano tutto)

### Bot 8 chiusure
2 copy tennis SL (-$2.55) + 6 harvest Fed SL correlato (-$4.73) = -$7.28.
Niente esclusione tennis (soglia ≥5 non raggiunta). Harvest Fed = problema
reale: bet duplicato + SL che rompe hold-to-resolution.

### Decisione FINALE
**Step 5d — abbandona latency-arb per capitale reale.** Phase CJ2 cancelled.
Nessun $50 reali. Validator può restare spento o idle. Focus sul bot
copy/harvest; prossimo lavoro utile = dedup harvest correlato / SL harvest.

### File
- tools/audit_v2.py (NUOVO)
- findings.md (sez AUDIT v2 PROFONDO)
- task_plan.md (CJ1.6 complete, CJ2 cancelled)
- ARBITRAGE_LATENCY_PLAN.md (logbook entry)
- progress.md (questa sessione)

---

## SESSIONE 2026-07-23 - Phase CK avviata

### Obiettivo approvato
Quarantena immediata del bot e nuova validazione prospettica onesta:
OBSERVE di default, COPY unica candidata futura, nessun capitale reale.

### Stato iniziale
- Snapshot VPS remoto: equity/cash $297.0869, 5 chiuse, 0W/5L, 0 aperte.
- Run post-fix aggregati: HARVEST 0W/10L (-$6.83), COPY 0W/3L (-$3.36).
- `git pull --ff-only` fallito correttamente: locale e remoto hanno un commit
  esclusivo ciascuno. Nessun reset eseguito; integrazione con merge preservativo.
- File utente non tracciati `progressi.txt` e `resp.json` lasciati intatti.

### In corso
- Integrare lo snapshot remoto.
- Implementare contenimento, persistenza, misurazione, rischio e dashboard.
- Aggiungere test automatici `unittest` con feed mock.

### Errori di verifica
- Primo `py_compile src\*.py`: PowerShell non espande il glob passato a Python.
  Correzione: usare `python -m compileall`.
- Primo `unittest discover`: i due test legacy importano moduli da `src` senza
  configurare `PYTHONPATH`. Correzione: eseguire con `PYTHONPATH=src` e aggiungere
  una suite autonoma sotto `tests/`.
- Fixture dashboard: `New-Item -LiteralPath` non è supportato dalla PowerShell
  presente. Correzione: `New-Item -Path` su una directory temp esplicita.
- Browser smoke: PID dashboard/bot usavano ancora `BASE_DIR/data`, ignorando
  `POLYMARKET_DATA_DIR`. Corretto entrambi a `DATA_DIR`.

### Implementazione completata
- Snapshot remoto integrato con merge preservativo; file utente non tracciati
  `progressi.txt` e `resp.json` mai modificati.
- Default `observe`; COPY sola scansione, nessuna strategia apre; latency-arb
  non parte salvo opt-in esplicito.
- Ledger v2 con run/signal/event identity, dedup asset+condition, un evento,
  cap 3%, stop-loss block e safety state persistente.
- Entry ask VWAP e mark/exit bid VWAP sulla size intera; niente midpoint o
  slippage statico inventato. Candidate journal JSONL append-only.
- Circuit breaker equity: -$3 daily, -$6 run, quarantena a 3 loss consecutive
  e riattivazione manuale.
- Wallet effettivi persistiti e congelati in paper_validation.
- `restart` sempre conservativo; `new-run` archivia; `reset --force` archivia
  prima di cancellare, sia Linux sia Windows.
- Dashboard: API operative, no-store, peak corretto, max dinamico, evento,
  OBSERVE/HALTED, quarantena e stale banner.
- Backtester declassato a wallet history profiler; claim 89% ritirato.
- Protocollo e valutatore promozione prospettica aggiunti.

### Verifica finale locale
- `compileall`: OK.
- `unittest discover`: 17/17 OK.
- JavaScript dashboard: syntax OK.
- `bash -n start_all.sh`: OK.
- reset senza `--force` e `restart reset`: rifiutati su Linux e Windows.
- Browser smoke su snapshot VPS: OBSERVE, $297.09, -$2.91, peak $300,
  drawdown 1.0%, 0 open, 5 closed, max 2, stale/quarantena visibili,
  nessun errore console su caricamento pulito.

### Rimane esterno al workspace
- Deploy VPS e verifica servizi.
- Conferma zero aperture per 24 ore.
- Raccolta journal per almeno 48 ore prima di qualunque paper_validation.
