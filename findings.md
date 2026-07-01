# Findings & Decisions — Polymarket Copy Bot

> Estensione sessione 2026-07-01 (post-dashboard VPS: WR 20%, poche aperture, obiettivo doubling/settimana).
> Vecchi punti P1-P9 ancora validi (vedi sezione "Diagnosi storica" sotto).

## Requirements (sessione 2026-07-01)
- Aumentare nr. aperture (ora 1/12h, wallet attivissimi)
- Rimediare alla perdita (-$0.80, WR 20% su 5 trade)
- Tendere a doubling $300→$600/settimana, compounding 7gg
- Monitorare balance continuamente + alert
- NON toccare lista wallet curata
- **NUOVO**: diversificare strategie — oltre copy, anche arbitraggio e altre su Polymarket

## Studio strategie complementari su Polymarket (2026-07-01)
Copy-trading da solo ha un tetto. Per il doubling serve diversificazione con
strategie a correlazione bassa. Realisticamente implementabili su Polymarket:

### S1 — Copy-trading (esistente, post Phase I fixed)
- Edge: backtest 89% WR, +81.6% ROI mediano su 73 pos in banda 0.30-0.70
- Risk profil: dipende dal segnale wallet; SL-8/TP-20 breakeven WR 29%
- Capacita sizing: 3-12% foto del capitale, limitato da slippage round-trip
- EV aspettato: +0.85-3.5$/trade a sizing variabile, WR 60-70% reale

### S2 — Arbitraggio binario YES+ NO <$1 (stesso mercato)  ← fase N
- Meccanismo: ogni conditionId ha 2 token; a settlement uno paga $1 altro $0.
  Quindi YES + NO = $1 sempre (identita'). Se best_ask(YES)+best_ask(NO) <
  $1 - fees - safety → compra entrambi → profit = $1 - costo CERTO (risk-free
  modulo fees/refund).
- Fee cruciali: sport = rate*min(p,1-p) ≈ 3% × 0.5 = 1.5% per leg, quindi
  spread_arb deve superare 2*1.5% + safety = ~3.5% per essere profittevole.
  Sport quasi mai arbabile. **crypto/politics/weather/other = 0% fee → fertile**.
- Sizing: min(book_size_yes, book_size_no); cap 15% del portafoglio per singolo
  arb (concentration), rispettando reserve.
- Profilo: risk-free-ish; capitale bloccato fino a resolution. Calcolare APR
  non %. Filtro endTime < 14gg (no capital-lock lungo).
- Rischio residuo: refund/annullamento mercato (raro), fill slippage fra
  quote e execution (paper ok).
- Bottleneck tecnico: ottenere entrambi asset_id YES/NO per conditionId via
  gamma `markets?slug=...` o clob. Poi get_book di ognuno.

### S3 — Harvest near-certain (prezzo 0.92-0.98, scadenza <7gg)  ← fase O
- Meccanismo: esito virtualmente deciso, lato vincente alto; compra lato
  vincente, riscuoti $1 a settlement. Profitto piccolo (es. ask 0.95 → +5%
  su capitale bloccato 3gg = APR ~600%).
- Hit rate alta (vincente gia'); rischio = reversal black-swan (es. sport blowout
  rovesciato, referendum sorprendibile). Filtro: evita politics e referendum
  "sorprendibili", preferisci sport blowout / eventi gia' conclusi fatto.
- Filtro: ask <0.97, spread ≤2 tick, book size >= $20, endTime <7gg, NON redeemable
- SL no standard; hard SL -3% se prezzo <0.90 (esito NON certo come pensavamo)
- Sizing: cap 8% singolo (risk low ma reversal possinile), reserve rispettata

### S4 — Arbitraggio cross-market (multi-outcome esaustuve)  ← fase P
- Meccanismo: evento con N outcome esaustivi e mutuamente esclusivi (es.
  "Chivince GOP 2028 nominee" con candidati A/B/C/D); sum best_ask YES_i DEVE
  essere $1. Se sum_ask < $1 - fees → compra TUTTI → profit = $1 - sum.
- Fertile quando campo chiuso (finite bracket); tornei, nominee, top goalscorer.
- Complessita n-leg: n get_book + n fill, slippage × n, safety 1c.
- Rarissimo ma quando compare percentuale grande. Frequenza empirica da misurare.
- Sizing cap 10%, reserve rispettata

### S5 — Market-making (SKIP)
- Adverse selection retail = danno, rebate non accessibile facilmente. Skip.

### S6 — Value-betting con modello proprio  ← fase Q (gated)
- Weather: NOAA probabilita pubbliche vs prezzo Polymarket → simple MVP
- Sport: odds aggregatori (the-odds-api) implied prob vs prezzo → bet se gap >
  2*(spread+fee)
- Kelly fractional 1/4 sizing (richiede p(win) e payoff noti)
- Sforzo alto (mantenimento modello, raccolta dati), solo se altre insufficienti

## Allocation capitale multi-strategy (paper)
| Strategia | Cap %% | Sizing singolo | Reserve | Note |
|-----------|--------|---------------|---------|------|
| COPY | 50% | 3-12% gated WR | 20% | engine principale post-fix P10 |
| ARB binary | 25% | fino a 15% | shared | risk-free-ish, cash idle |
| HARVEST | 15% | fino a 8% | shared | capital lock breve |
| ARB cross | 10% | fino a 10% | shared | occasionale, grande |
Cash non allocato flussibile. Soum cap 100% + reserve 20% floor mai rotto.
Attribution P&L separata per valutare quale strategia rende /quale fermare.

## Doubling-settimana matematica multi-strategy (oneste revisita)
- Copy solo: sizing 12% + 85 win/sett + WR 70% ≈ doubling MA beta catastrofico.
- Con S2+S3+S4 che aggiungono +5-15%/sett risk-free-ish, il copy sizing puo'
  restare piu' moderato (8%) riducendo beta:
  - Copy 8% × 50 trade/sett × WR70% × EV~1.5$ = +$52 (+17%)
  - ARB binary ~10 pos/sett medium +0.5$*15% sizing = +$5 (+1.7%)
  - HARVEST ~5 pos/sett APR 200% su 8% sizing = +$12 (+4%)
  - ARB cross 1-2/mese occasionale +$10 (+3%)
  - Totale ~+26%/sett → doubling in ~3 settimane (+81%). Piu' realistico.
- Verdetto riveduto: doubling in 7gg E ancora estremamente rischioso, MA con
  multi-strategy doubling in 2-4 sett e' **raggiungibile con beta minore**.

## Diagnosi nuova (P10-P14)

### P10 — Delta-snapshot aggregato per ASSET → aperture pochissime (causa)
main.run_mirror_loop:
```
new_assets = set(aggregate.keys()) - self.prev_assets
```
- `aggregate` è keyed per `asset` (token ID di UN outcome)
- se wallet A detiene "Egypt Yes" gia da ieri → chiave in aggregate
- wallet B entra OGGI in "Egypt Yes" → aggregate ha ancora lo stesso asset key
- delta = aggregate_keys - prev = ∅ → NESSUNA apertura
- ⇒ catturiamo SOLO "asset che NESSUN wallet aveva mai avuto", NON "ingresso nuovo
  di un wallet in asset gia visto". Frequenza aperture ~1/12h.
- Spiega anche EGYPT DOPPIONE: entra→venduto→asset esce→rientra→riaperto (#2 volte)
- FIX: baseline PER-WALLET, delta = {(wallet,asset) NUOVI}; cap per-wallet rimane.

### P11 — Bublik 0.708 aperto FUORI banda 0.70 (anomalia deploy)
locale: `entry_price_max=0.70`; simulator.py controlla `if price > price_max: SKIP`.
Ma dashboard VPS mostra trade Wimbledon Bublik @0.708. ⇒ VPS NON esegue codice locale.
Possibili cause:
- VPS ha versione pre-fix
- config VPS alterato manualmente (utente ha alzato max_open_positions a 10?)
- deploy non aggiornato
→ Phase H PRIORITA 1: ri-deploy pulito.

### P12 — Dashboard Max:10 vs config locale max_open_positions=4
Stesso indizio P11: divergenza config VPS. Utente ha modificato? Verificare.

### P13 — Egypt Yes riapertura (doppione "trade recente")
Lista trade mostra 2 BUY Egypt 07/03 Yes @0.393 (19:44 e 23:12, size $7.22/$9.01).
get_open_assets ha `if self.has_asset(asset): return False` quindi non 2 contemporanee:
è stata APERTA→CHIUSA→RIAPERTA. Possibile flusso:
  ciclo 19:44 wallet entra in Egypt Yes → asset in aggregate NUOVO → aperta size $7.22
  ciclo N wallet ESCE o SL/TP → close_position
  ciclo 23:12 wallet (stesso o altro) rientra → asset re-entra in delta → riaperto $9.01
  Distribuzione size diversa (7.22 vs 9.01) perché cap per-wallet/categoria + soft-
  disable factor diverso per wallet sorgente diverso.
→ Phase I (delta per-wallet) + implementare dedup_window (TRACKING.dedup_window=3600
  gia in config ma NON usato nel codice: bug verbale) riducono questo.

### P14 — WR 20% su 5 trade: statisticamente NON significativo
5 trade chiusi: 1W/4L. Backtest: 89% WR su 73 pos. Breakeven SL-8/TP+20 = 29%.
Sample 5 non giudica la strategia, MA gravity: serve ri-deploy corretto (P11/P12)
e aumentare aperture per raccogliere 30+ trade prima di giudicare edge.

## Diagnosi storica (P1-P9, sessione 2026-06-30) - ancora valida
- P1 — Mirroring copia snapshot, non trade (entrate tardive) → FIX Phase C
- P2 — Dump intero portafoglio al primo snapshot → FIX Phase C baseline
- P3 — Posizioni correlate, nessun filtro direzionale → FIX Phase E cap per cat
- P4 — SL/TP asimmetrici (-30/+50) → FIX Phase E (-8/+20)
- P5 — Filtro win-rate NON enforceato legacy → FIX Phase B (scanner)
- P6 — ROI aggregato inganna: serve win-rate recenti → FIX Phase B (cap per-wallet)
- P7 — Sizing/allocazione subottimale (reserve troppo alta) → Phase F partial
- P8 — Mercati lungo lockdown 2028 → FIX Phase D (max 60gg)
- P9 — Nessun filtro liquidita → FIX Phase D (book + spread)

## Verify-still-true (VPS-specific, da confermare)
- [ ] Codice VPS match locale (md5 src/* + config.py)
- [ ] Bublik 0.708 / Max 10 sono anomalia deploy → ri-deploy fix
- [ ] Frequenza aperture delta per-wallet >3x di delta per-asset (post-fix)
- [ ] dedup_window INUSATO nel codice → implementare

## Technical Decisions (sessione 2026-07-01)
| Decision | Rationale |
|----------|-----------|
| Baseline PER-WALLET per delta-copy | Fix P10 catturando ingressi multi-wallet stesso asset |
| Sizing compounding ladder (3→5→8→12%) | Avvicina doubling senza blow-up; gate su WR>60% post 30 trade |
| poll 30s + dedup_window implementato | Raddoppia capture ingressi real-time + anti reopen stesso asset |
| Reserve 25→20% + auto -50% sizing su -10% dd | Protezione capitale in scaling aggressivo |
| Banda 0.25-0.75 quando consenso>=2 wallet | Extra aperture senza abbandonare zona edge |
| min_days_to_expiry 1.0 → 0.5 | Cattura sport intraday (>12h) senza coin-flip 5min |
| Telegram/alert + equity floor auto-stop | Monitoraggio balance aggressivo richiesto |
| Multi-strategy router (COPY+ARB+HARVEST+ARBcross) | Singola strategia tetto; bassa correlazione miglior doubling |
| ARB binario focus crypto/other (fee 0%) | Sport ha fee 1.5%/leg → arb mangiata; crypto/other fertile |
| HARVEST ask<0.97 endTime<7gg categoria sport | Hit-rate alta; politics/refendum evitati come sorprendibili |
| ARB cross sizing 10% occasionale | Mispricing grande ma raro; n-leg aumenta costo |
| Value-betting gated Phase Q | Sforzo modello elevato; gated se altre strategie non bastano |
| Allocation soft-caps (no silos rigidi) | Cash flussibile dove compare miglior opportunita; cap per-strat |

## Doubling-settimana matematica (onesto, copy-solo)
> Questa stima considera solo copy; con multi-strategy (sezione sopra) e' migliorabile.
- Obiettivo $300 → $600 in 7gg = +100% = ~10.4%/gg compound
- Sizing 3% ($9) e TP+18% netto: P&L/trade ~ +$1.62 vincente, -$0.72 perdente
  - A WR 70% EV ≈ +$0.85/trade → 35 trade/sett = +$30 (+10% sett) NON doubling
- Per doubling servono ~120 trade/sett vittoriosi a sizing 3% (impossibile)
- Sizing 12% ($36) TP+18% netto: +$6.48 win / -$2.88 loss, EV WR70%=+3.5/trade
  → ~85 winning trade/sett = doubling MARGINALMENTE possibile MA beta catastrofico:
  4 loss consecutive = -$11.5 (-3.8%), 10 loss = -$28 (-9.4%)
- **Verdetto**: doubling in 7gg richiede sizing ~12% + ~85 winning trades/sett +
  WR>70%. Realistico STEP: +20-40%/sett per 2-3 sett → doubling in ~3-4 settimane

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| Bug delta aggregato P10 | Phase I: refactor prev_holdings per-wallet |
| Bublik fuori banda 0.708 | Phase H: ri-deploy (VPS divergente) |
| Dashboard Max 10 vs config 4 | Phase H: ri-deploy pulito |
| dedup_window config 3600 inusato | Phase I: implementare in simulator.reconcile |
| WR 20% su 5 trade | Attesa 30 trade post-fix; campione non significativo |

## Resources
- Codice: src/main.py (delta-snapshot), src/simulator.py (reconcile, open_position
  con guardrail banda/scadenza/liquidity/cap), src/portfolio_sync.py (snapshot_wallets
  aggrega per asset), src/config.py (BUDGET/STRATEGY/TRACKING)
- Dati VPS (NON in locale): portfolio_state.json, trades_log.json, equity_curve.json
- Deploy: deploy_polymarket.sh / package_for_vps.sh / vps_manager.sh / check_vps.sh
- API: data-api /positions (snapshot per wallet), /activity (eventi), clob /book

## Visual/Browser Findings
- Screenshot dashboard VPS 2026-07-01 09:14: 1 aperta, 5 chiuse, WR 20%, -$0.80
- Bublik Wimbledon trade @0.708 anomalia deploy
- 10 wallet elencati: suntori/c0O0OLI0O03/neutralwave23/mombil/COMESEECOMESAW/tugator/
  VeeFriendsDownUnder/CoffeeLover/Zptml/ChetterHummin (vs 9 sessione precedente)
- Trade Egypt Yes raddoppiato @0.393 con size $9.01 vs $7.22 (cap-wallet differenza)

---
*Update this file after every 2 view/browser/search operations*