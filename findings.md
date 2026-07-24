# Findings & Decisions — Polymarket Copy Bot

> Estensione sessione 2026-07-01 (post-dashboard VPS: WR 20%, poche aperture, obiettivo doubling/settimana).
> Vecchi punti P1-P9 ancora validi (vedi sezione "Diagnosi storica" sotto).

## Incident OBSERVE VPS — 2026-07-24

- Deploy confermato su `c2f4d52`; bot e dashboard attivi, latency-arb fermo.
- Il loop completa snapshot ogni ~20 secondi e salva il ledger: nessun crash.
- Il journal contiene 555 righe, tutte `rejected/execution_mode=observe`.
  `open_position()` controlla la modalità prima dei filtri, quindi il campione
  non distingue segnali validi da book/spread/scadenza/drift non validi.
- `saved_at` è UTC naïve; il browser Europe/Rome lo interpreta come ora locale
  e calcola un falso stale di circa due ore.
- Il profiler usa `/activity?limit=1000`; l'API accetta massimo 500. I refresh
  qualità restituiscono HTTP 400 e non producono metriche affidabili.
- Il full rescan automatico è sincrono e interrompe i cicli. Per un campione
  prospettico stabile, wallet e selezione restano congelati nel run.
- `wallet_quality.json` non è attualmente archiviato/azzerato da `new-run`.
- `reconcile()` salva già il ledger a fine ciclo: per health basta calcolare
  l'età sul backend con parsing UTC, senza heartbeat artificiale.
- `source_trade_at` nello snapshot `/positions` non è garantito; il lookup deve
  interrogare `/activity` solo quando appare un nuovo `(wallet, asset)`.
- La valutazione deve distinguere filtri pre-trade puri dai limiti dipendenti
  dal portfolio. In OBSERVE si registra `eligible`, poi si esce prima di ogni
  mutazione; in paper si applicano halt/cap/dedup persistente e si apre.
- Il manifest wallet è oggi congelato solo in paper. Verrà riutilizzato per
  qualunque modalità quando il `run_id` coincide e marcato sempre `frozen`.
- La dashboard dispone già di un unico refresh ogni 10s: `/api/status` conterrà
  il riepilogo leggero e lo stesso ciclo caricherà `/api/candidates?limit=50`
  per la tabella, entrambi `no-store`.
- Il banner attuale fonde OBSERVE e guasti reali. Verrà separato in banner
  informativo OBSERVE e banner rosso basato su `bot_health.stale`/halt reale.

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
- Dato storico ritirato: il precedente 89% WR era in-sample e usava il prezzo
  medio wallet, non il best ask rilevabile; non dimostra edge.
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
5 trade chiusi: 1W/4L. Il vecchio 89% WR su 73 pos non è una validazione
prospettica; breakeven teorico SL-8/TP+20 = 29%.
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

## EMERGENZA 2026-07-07: Dashboard mostra -5.63%, WR 24% — ROOT CAUSE

### Dati dashboard 07/07 07:25
- Equity $283.11 / $300 → P&L -$16.89 (-5.63%)
- Realizzato -$18.86 | Non realizzato +$1.97
- 25 trade chiusi: 6W / 19L = **WR 24%**
- 19/25 chiusi sono **STOP LOSS** (76%)

### Per-strategia
| Strategia | Open | Closed | Realized P&L | WR |
|-----------|------|--------|-------------|-----|
| whale     | 4    | 6      | -$6.99       | 17% |
| momentum  | 0    | 4      | -$4.20       |  0% |
| contrarian| 0    | 3      | -$3.00       |  0% |
| harvest   | 0    | 8      | -$3.13       | 38% |
| copy      | 0    | 4      | -$1.54       | 50% |
| arb/sniper/theta | 0 | 0 | $0 | - |

### ROOT CAUSE: SL percentuale a prezzi estremi = rumore-trigger

Il bot entra a prezzi estremi (0.999, 0.992, 0.036, 0.026, 0.061) dove:
- **SL % triggera sul rumore**, non sul fallimento del segnale
- **Risk/reward invertito**: max gain minuscolo, max loss enorme

**Esempi critici (tutti dalla trade history reale):**
1. Whale No @ 0.999 → SL -6% = trigger a 0.939. Gain max $0.001, loss $0.06. RR 1:60.
2. Whale Yes @ 0.036 (Mexico) → SL -6% = trigger a 0.0338. 0.002 move = 1 tick.
   Risultato: -21.70% (gap oltre SL).
3. Contrarian Yes @ 0.026 (USA) → SL -4% = trigger a 0.025. 0.001 move = 1 tick.
4. Momentum No @ 0.992 (Norway) → SL -5% = trigger a 0.942. YES era a 0.008,
   "momentum" rilevato su move di 0.0005 = rumore puro.
5. Harvest No @ 0.929 (England) → SL -4% = trigger a 0.892. 3.7 cent = rumore
   normale per near-certain market.

### Perché le strategie entrano a prezzi estremi

**Whale:** `scan()` filtra solo `0 < ask < 1`. NESSUNA banda prezzo. Compra a
qualsiasi prezzo la whale ha comprato, anche 0.999.

**Momentum:** `scan()` calcola `move = (last - first) / first`. Se YES va da 0.0085
a 0.008, move = -5.9% → "momentum!". Ma 0.0005 assoluti = rumore. Compra NO
a 0.992 (complemento).

**Contrarian:** fade di mercati estremi 0.93-0.99. Se whale SELL Yes a 0.97,
compra No. Ma No price può essere 0.025-0.06 — longshot con SL a 1 tick.

**Harvest:** fav_min 0.78, fav_max 0.985. A 0.985, SL -4% = 3.9 cent = rumore.
Early TP +4% = 3.9 cent = anche rumore, ma peggio: chiude posizioni che
andrebbero a $1 a resolution, lasciando 96% del juice sul tavolo.

### Bug secondari
- **9 strategie attive**: COPY aveva solo un profiler storico in-sample. Le altre
  MAI backtestate. Whale/momentum/contrarian sono scommesse non validate.
- **Sizing 6-13%**: a WR 24%, ogni loss è -0.8% portfolio. Drain costante.
- **Kelly + trailing stop attivi**: amplificano sizing e chiudono su rumore.
- **max_open_positions 12**: troppi slot, troppo esposizione con WR basso.

### Fix (vedi task_plan.md Phase CC-CG)
1. KILL whale/momentum/contrarian/sniper/theta (0-17% WR, non validati)
2. SL assoluto (cent) per prezzi estremi, non percentuale
3. Entry band 0.08-0.92 per tutte le direzionali
4. Harvest: hold-to-resolution, no early TP, SL assoluto -5 cent
5. Sizing 3% base, 8 pos max, reserve 20%, no Kelly, no trailing
6. Validare 30 trade prima di scalare

---

## Studio 3 guide online (2026-07-13, post-deploy COPY 0W/3L) — NON possiamo spendere

Fonte: `guida_modelli_online.txt` (3 guide). Sintesi onesta di cosa è replicabile
senza spendere un euro in più di infra.

### TESI DI FONDO: le guide descrivono una strategia DIVERSA dalla nostra

La Guida 2 (bot 0x8dxd, $313→$2.38M, 98% WR, 26.738 trade) è un **latency-
arbitrage bot su contratti crypto 5/15-min Polymarket**. Meccanismo:
- Bot monitora Binance WebSocket in tempo reale (<50ms latenza).
- Polymarket lagga il book CLOB vs CEX di ~2.7s (erano 12s nel 2024).
- Quando BTC si muove 0.6% in 30s → Polymarket ancora a quote vecchie →
  compra il lato "ovvio" prima che il book si corregga → exit o hold-to-resol.
- 200–500 trade/giorno, sizing Kelly fractional, kill switch -40% drawdown.

**NON È ciò che fa il nostro bot.** Noi siamo un WALLET-COPY bot (poll 20s) su
sport/politics/weather. NON possiamo competere su latenza:
- 20s poll vs 2.7s edge window → gap chiuso 10 volte prima che noi guardiamo.
- Python non co-locato vs HFT bot con infra dedicata.
- Paper mode → non piazziamo ordini reali; il fill/non-fill reale non è testabile.
→ **CONCLUSIONE: NON pivoting al latency arb.** Anche simulandolo in paper, gli
HFT bot che competono per la stessa gap chiuderebbero sempre prima di noi. La
finestra sta comunque comprimendosi (12s→2.7s in 2 anni). È un business a tempo.

### Le 4 strategie della Guida 2 (per contesto)
| Strategia | WR | Infra richiesta | Rilevante per noi? |
|-----------|-----|----------------|-------------------|
| Latency arb | 85–98% | Binance WS + sub-100ms + co-lo | ❌ no (velocità) |
| Oracle arb | 78–85% | Feed Chainlink vs contract | ⚠️ maybe, medio sforzo |
| News-based | 60–75% | Claude API per ogni news ($$) | ❌ no (spend API) |
| Market making | 2–5%/mo | FIFO queue priority + real ordini | ❌ no (maker, paper) |

Le 3 strategie non-latency o sono a basso WR (news) o richiedono infra/API a
pagamento o real-order capability (maker). Combaciando con il vincolo "non
spendere", restano fuori. Aggiungiamo a "value" gated se tutto il resto fallisce.

### LEZIONI APPLICABILI A NOI SENZA SPENDERE (priority-ordered)

#### L1 — Gestione rischio = unica vera differenza (Claude vs OpenClaw) ★★★★
Guida 2 è esplicita: la differenza +1322% vs liquidazione NON fu la strategia,
ma il risk management. Parametri raccomandati:
- max singola posizione: 8% portafoglio (noi 3% floor, OK più conservativi)
- daily loss limit: **-20% con stop automatico giornata** (noi NON abbiamo
  un daily counter: abbiamo equity_floor -5% lifetime e ruin -20% lifetime)
- kill switch totale: **-40% drawdown** (noi ruin -20%, più stretti — OK)
- Telegram alert a ogni soglia (noi log_only, mancano notifiche)
→ **Azione: aggiungere DAILY loss counter + daily halt.** È il gap più concreto.

#### L2 — Filtro liquidità >$50.000 per strategie NON-copy ★★★
Guida 2: "Opera solo in mercati con >$50.000 di liquidità. I mercati più piccoli
non possono assorbire uscite pulite, il bid-ask spread si mangia i gain."
Noi usiamo min_book_size 15–50 USDC (profondità del best level, NON liquidità
totale mercato) e min_volume 1000–5000 (volume mercato, ma <<$50K).
→ **Azione: filtro market liquidity/volume >= $50K per harvest + arb.**
  Per copy non si applica (segue wallet, il wallet ha scelto mercato liquido).
  Aggiungiamo config `min_market_volume_usdc: 50000` e fetch da gamma volume.

#### L3 — Fee taker su USCITA (slippage+fee su SL/ TP close) ★★★
Guida 1: taker fee mangia l'edge OGNI volta che crossing il book. Noi modelliamo
fee solo in INGRESSO: `eff_price_with_fee = price * (1+fee_frac)`. P&L close è
`pnl = (exit - entry) * shares` → **fee di uscita non dedotta**.
Per harvest hold-to-resolution (settle $1/$0) NON c'è fee (è settlement, non trade).
Per copy/sport con SL/TP early-exit: la fee di uscita va dedotta o la P&L è
ottimistica. Su sport a 0.50 → uscita costa ~1.5% per leg → su $8.95 size = -$0.13
per trade. Su 3 trade = -$0.40 cumulato “nascosto” che peggiora il nostro -1.72 reale.
→ **Azione: dedurre taker_fee_fraction anche sull'exit_price nelle chiusure
  SL/TP (non sulle resolution). Modifica in simulator.close_position.**

#### L4 — Guida 1: fee formula `rate · p · (1−p)` ★★
Già implementato in categories.taker_fee_fraction (sport rate 0.03).
- La fee è MAX a p=0.50 (coin-flip) → ~0 agli estremi (0.05/0.95).
- **CONFIRMA harvest 0.85–0.95: fee minuscola** (rate·0.05·0.95 = 0.0014 = 0.14%).
  + hold-to-resolution = nessuna exit fee → edge pulito. ✓ BEST allineato.
- **CONFIRMA arb_binary morto come taker**: gap 2–4c in coin-flip dove
  fee = 1.5c/leg → su 2 leg fee 3c vs gap 3c = breakeven netto. Spiega 0 opp.
  Vivo solo come maker (limit order, 0 fee + rebate 25%) — non simulabile onesto
  in paper (FIFO queue fill non esiste, simuliamo fill istantaneo a best_ask).
→ **Azione: disabilitare arb_binary in paper (trova 0, complexity inutile)
  OPPURE tenerlo come monitor-only (log gap without open).** Spiega l'ostilità.

#### L5 — VWAP per arb detection (Guida 3) ★
Guida 3: non usare last-tick (mente). VWAP = `Σ(price·size) / Σsize` su finestra
stretta con carry-forward. Flag a 2c, **trade a ≥5c**, skip se qualunque leg >0.95,
skip se leg senza trade nella finestra. “Detect wide, act narrow.”
Noi usiamo best_ask dal book (book-ask sum = cost reale per prendere entrambi i
leg, più conservativo per valutare profitto post-take). Questo è ragionevole; la
VWAP serve a DETECT mispricing da transazioni reali.
→ **Azione (bassa priorità): per arb_cross, fetch trades recenti (data-api
  /trades) e calcola VWAP per confronto con sum-book. Flag-a-2c / act-5c filter.**
  Priorità bassa: arb trova 0 opp con threshold 20–50c. Se scendiamo a 5–7c
  come maker servirebbe VWAP per validare. Ma non siamo maker.

#### L6 — Maker vs taker (Guida 1): core, ma non applicabile in paper ★
Limit order = 0 fee + rebate 25% (crypto 20%). Market order = pay fee. Per arb:
“maker arb keep gap + rebate; taker arb lose gap.” MA maker richiede vincere la
FIFO queue, ordini early + hold posizione. In paper non esiste queue / fill reale.
→ **Azione: DOC. Quando/ se passiamo a real trading, TUTTI gli arb devono essere
  limit-order (maker). Annotato, non implementabile ora.**

### DIAGNOSI REALE COPY 0W/3L (non dalle guide, dai nostri numeri)
I 3 trade chiusi sono tennis in-play (Iasi/Swiss) + France-Spain O/U. Entry in
banda VALIDA (0.42–0.55). Drift filter NON ha skippato: prezzo nostro = avg_price
wallet (entro 8%). → NON è "ingresso tardivo vs wallet".
La causa è la Natura del copy su tennis in-play:
- I wallet che copiamo sono momentum-chaser su match in corso → alta varianza.
- SL -8% su tennis in-play è TROPPO STRETTO: un break di game muove il prezzo
  10–15% anche quando il risultato finale è quello previsto inizialmente.
- SL assoluto (-8% su 0.42 = -3.4 cent) su swing normali di tennis spara subito.
→ **Azione (non dalle guide): per copy-sport, usare SL più lato (−12% o assoluto
  −5 cent) OPPURE escludere copy su tennis/ sport in-play, OPPURE time-stop
  (se non risolve entro N min, esci senza SL%).** Da sperimentare in paper.

### PRIORITÀ DI IMPLEMENTAZIONE (date le guide)
1. **L1 daily loss limit/halt** — concrete, alto valore, zero sforzo
2. **L2 liquidity filter ≥$50K** per harvest/arb
3. **L3 exit fee** nel simulatore (P&L realistica)
4. **Diagnosi copy-sport SL** (L6 nostro): SL assoluto o esclusione tennis in-play
5. **L4 disabilitare/monitor-only arb_binary** (spiega 0 opp, semplifica)
6. **L5 VWAP arb_cross** (bassa, solo se abbassiamo threshold arb)

NO-mapping: latency arb, oracle arb, news-based, market-making, value-betting
esterno → tutti gated / fuori scope fino a che budget ridotto e paper mode.

## LATENCY ARB Step 0 — Bug resolver (2026-07-17)

**Situazione dopo 2 giorni (15-17/07) di validatore attivo post-fix discovery**
(dati forniti da utente via `progressi.txt`):
- `latency_arb_signals.jsonl` = 4032 righe totali
- `latency_arb_stats.json` = **INEXISTENTE** (file mai creato)
- log stats: `resolved=0 | WR=0.0% | P&L virt=$0.000 | pending=6` (costante)
- grep count: `RESOLVE=0`, `SIGNAL=2019`
- 2 es SIGNAL: `LONG_YES edge=+0.14 p_yes=0.355 Δ5m Binance=-0.24%` (ETH),
  `LONG_YES edge=+0.18 p_yes=0.315 Δ5m Binance=-0.06%` (BTC) — entrambi 5.4min
  alla scadenza

**Interpretazione**: impossibile giudicare il model K/outcomes[0] ora — N=0
resolves significa WR=0 non per rumore model ma per **resolver rotto**. Loop
negativo: detect → pending → 10 min stale cleanup → ri-detect (cid non in
pending) → pending di nuovo → ... spiega il pattern 2019 SIGNAL / 6 pending
constante / 0 RESOLVE.

### Bug #1 (CRITICAL): `resolve_contract` non parse `outcomePrices`
Il vecchio codice faceva:
```python
txt = (m.get("outcome") or m.get("resolutionSource") or "").lower()
if "yes" in txt or "up" in txt: return True
if "no" in txt or "down" in txt: return False
```
Ma gamma NON espone `outcome`/`resolutionSource` come free-text per i crypto
up/down. Il campo corretto è **`outcomePrices`** (JSON-encoded string tipo
`'["1","0"]'`) — l'index con valore ~1 e' il vincitore. Risultato: `result=None`
sempre → dopo 600s stale → drop → ri-detect.

### Bug #2: `outcomes[0]="Up"` assunto senza verificare
Vecchio: `token_yes = c["tokens"][0]; p_yes = book_yes(token_yes)`. Ma Polymarket
spesso ordina alfabeticamente → `outcomes=["Down","Up"]` → tokens[0] = token
DOWN → `p_yes` era in realtà `p(DOWN)`. Questo era il sospetto anticipato in
`progressi.txt`. Fix: match per NOME via `_find_outcome_idx(outcomes, ("up","yes"))`
e `_find_outcome_idx(outcomes, ("down","no"))` → token UP esplicito, p_up_market
ottenuto da book_yes(token_up).

Esempio numerico pre-fix: edge=+0.14, p_yes=0.355, Δ5m=-0.24% su ETH.
- se outcomes=["Up","Down"]: p_yes=0.355=p(UP) → market molto bearish → expected_up
  = 0.5+2*(-0.0024)=0.495 → edge=0.495-0.355=+0.14 → LONG_YES → si compra UP
  a 0.355 credendo che valga 0.495. **Ma Δ5m=-0.24% dice ETH scende: UP dovrebbe
  scendere, non salire. Contraddizione interna**. Peggio: la somma edge+momentum
  e' incoerente — K=2 muove appena 0.005 il model, edge dominato dalla posizione
  di p_yes sotto 0.5 (che nell'es. e' effetto del mercato gia' bearish).
- se outcomes=["Down","Up"]: p_yes=0.355=p(DOWN) → market pensa DOWN=35.5% →
  p(UP)=0.645 → expected_up=0.495 → edge_corretto=0.495-0.645=-0.15 → LONG_NO
  (compra DOWN a 0.355). **Caso opposto — sensato**. Differenza: il segno flip
  non tanto per K (piccolo) ma perche p_up_market cambia da 0.355 a 0.645.

Conferma: **K=2 e' anche troppo piccolo** per spostare il model in modo
significativo (Δ5m=-0.24% × K=2 = -0.005 = 0.5 pt). Su 5min crypto up/down
le move di 0.3-1% sono normali, K dovrebbe essere ~10-30 per lasciare
impronta al momentum. Ma prima di tunare K serve RESOLVE funzionante per
avere WR feedback.

### Bug #3: `stats.json` mai scritto senza RESOLVE
`_save_stats()` era chiamato solo dentro `_resolve_pending()` dopo un resolve
riuscito. Fix: aggiunto `heartbeat_save_stats()` chiamato ogni 60 cicli (~1min)
che scrive stats.json con `pending` count + `ts_last_save` — cosi' anche senza
resolve possiamo auditare via `cat data/latency_arb_stats.json`.

### Fix applicati a `src/latency_arb.py`
- `resolve_contract`: parse `outcomePrices` (JSON-encoded), trovo index max,
  se hi>=0.95 e lo<=0.05 → winner_name=outcomes[hi_idx] lowercased → UP_won
  sse "up"/"yes" in nome, DOWN_won sse "down"/"no". Non assume outcomes[0]=Up.
- `scan_cycle`: match outcomes per NOME via `_find_outcome_idx`. token_up =
  tokens[up_idx]. p_up_market = book_yes(token_up). edge = expected_up -
  p_up_market. entry_price = p_up_market (LONG_YES) | 1-p_up_market (LONG_NO).
  Signal record ora include `up_idx`, `down_idx`, `outcomes`, `p_up_market`
  (alias legacy `p_yes` = p_up_market).
- `_find_outcome_idx(outcomes, needles)` helper (ritorna primo index che matcha).
- `heartbeat_save_stats()` + chiamata ogni 60 cicli nel loop.

### Script di debug `tools/debug_resolver.py`
Carica condition_id scaduti da signals.jsonl (o fallback query gamma
closed=true), per ognuno dumpa i campi gamma RAW (closed, outcomePrices,
outcomes, outcomeMetas, umaResolutionStatus, bestBid/Ask, etc.) + stampa la
derivazione del nuovo resolver. Da lanciare su VPS **prima** del deploy per
validare che i campi gamma matchano la mia assunzione.

### Decisione post-fix (identica a quella anticipata in `progressi.txt`)
**NON tunare K ora. NON fixare outcomes[0] come guess** — ora lo facciamo per
nome. Lasciamo girare 24-48h con il nuovo resolver. Altri 20-30 RESOLVE:
- WR > 60% su LONG_YES → model OK, K=2 va bene, outcomes matchati corretti
- WR ~50% o caotica → model rumore o K troppo piccolo → prova K=10
- WR <40% sistematico → K completamente off, rethink model (regression
  storico Polymarket→Binance)

---

*Update this file after every 2 view/browser/search operations*
---

## SESSIONE 2025-07-20 — ANALISI LOG WEEKEND VPS (STEP 2-4 PROSSIMI_STEP_LUNEDI)

### Sorgente dati
- Log scaricati da VPS via git push in `logs_weekend/` (force-add per bypass .gitignore)
- `latency_arb.log` (19405 righe), `bot.log` (82054), `dashboard.log` (676), `scan_categories.log` (70), `latency_arb_stats.json` (396 resolved)
- **Attenzione**: timestamp interne log dicono "20/Jul/**2026**" → clock di sistema VPS SBALLATO di 1 anno. I numeri contano, le date label sono da ignorare per il timing reale.

### STEP 2 — LATENCY-ARB VALIDATOR (il più importante)

| Metrica | Valore |
|---------|--------|
| edge_threshold (loop start) | **0.10** |
| resolved totali | **396** (>>soglia 200 prevista) |
| win totali | 137 |
| **WR totale** | **34.6%** |
| P&L virtuale cumulata | **-$17.115** |
| pending (ultime) | 10 |

### Bucket per edge (dal log STATS)
| Bucket | n | win | WR |
|--------|---|-----|-----|
| win_10_20 (|edge|<0.20) | 369 | 128 | **34.7%** |
| win_20_plus (|edge|>=0.20) | 27 | 9 | **33.3%** |

**Entrambi i bucket sotto 40%, sotto random 50%, simili tra loro** → alzare la soglia a 0.15/0.20 NON cambia il verdetto (il bucket 20+ è *peggiore* del 10-20).

### Split per ASSET (join SIGNAL→RESOLVE, vedi `tools/split_analysis.py`)

| Asset | n | win | WR |
|-------|---|-----|-----|
| BTC (Bitcoin) | 197 | 60 | **30.5%** |
| ETH (Ethereum) | 199 | 77 | **38.7%** |

- **BTC pesantemente sotto** random (30.5%) — la SIGNAL su BTC è *anti-correlata* all'esito.
- **ETH meglio ma comunque <45%** — nessun edge reale.
- SIGNAL BTC vs ETH: 1059 vs 1060 → sampling perfettamente bilanciato, il split è rappresentativo.

### Split per DIREZIONE (dal log RESOLVE direttamente)

| Side | n | win | WR |
|------|---|-----|-----|
| LONG_YES | 186 | 64 | **34.4%** |
| LONG_NO | 210 | 73 | **34.8%** |

- Direzioni **identiche** (~34.6% entrambe) → nessun bug "direzione short invertita". Model long/short coerente ma entrambi sbagliano.

### Matrice ASSET × DIREZIONE

| Asset × Side | n | win | WR |
|---------------|---|-----|-----|
| BTC LONG_YES | 86 | 26 | 30.2% |
| BTC LONG_NO | 111 | 34 | 30.6% |
| ETH LONG_YES | 100 | 38 | 38.0% |
| ETH LONG_NO | 99 | 39 | 39.4% |

- Righe BTC: 30.2/30.6 (uniformemente basse)
- Righe ETH: 38.0/39.4 (uniformemente medie-basse)
- **Nessun subset con WR > 45%** → nessuna slice su cui il validator abbia edge.

### Verdetto tabella STEP 2 (riga "50-100 trade, <45%")
> **Strategia non funziona. Vai a Step 5.**

Siamo ben oltre 100 trade (396), WR 34.6%, tutti i bucket sotto 40% — verdetto **confermato con margine ampio**: la **tesi del latency arb su Polymarket non regge** con edge=0.10, Δ5m, K=2, feed Binance.

### STEP 3 — BOT COPY/TRADING

| Metrica | Inizio | Ultimo |
|---------|--------|--------|
| Equity | $300.00 | **$300.02 (+0.01%)** |
| Aperte | 0 | 6 |
| Chiuse | 0 | 14 |
| WR chiuse | 0% | **36%** (≈5 win / 14) |
| tier / dd | 3% / 0.0% | 3% / 0.3% |

- Date Snapshot attive ogni ~25s, bot UP mentalmente vivo a "07:48:54" (label 2026)
- Strategie attive: **copy** (2ap/14cl P&L -$0.18), **harvest** (4ap/0cl), arb_cross (0 opportunità)
- `[SKIP] Cap wallet raggiunto (2) per 0x510904c9` → cap posizioni/wallet attivo, niente aperture runaway
- Equity piatta: 14 chiusure a WR 36% producono netto -$0.18 su 4 giorni → copy "non perde" ma non ha mai davvero operato (slippage)

### STEP 4 — DASHBOARD
- Log UP su 0.0.0.0:5000, 200 su `/api/status` e `/api/equity` a 07:48-07:49
- `[SIMULATOR] Stato ripristinato: $247.62 cash, 6 aperte, 14 chiuse` → cash + unrealized = equity $300
- Nessun traceback, nessun 500 → dashboard sana. GUI richiede tunnel SSH + browser tuo per verifica visuale.

### File generati/modificati
- `tools/split_analysis.py` (nuovo) — join SIGNAL×RESOLVE via edge+action per split asset

---

## LATENCY-ARB v2 — Il reset del 20/07 spiega l'"inversione" (2026-07-22)

### Il fatto
Output lunedì: 731 resolved, WR 43.2%, P&L virt +$135.74 (net +$104.90),
bucket win_10_20 298/698=42.7%, win_20_plus 18/33=54.5%. Sembrava un'inversione
del run weekend (396 res, WR 34.6%, -$17.11). **NON lo è.**

### Prova aritmetica che le stats sono state azzerate (731 = tutto v2)
| Test | Come continuazione (731=396+335) | Verdetto |
|------|----------------------------------|----------|
| Bucket win_20_plus | delta = 18-9=9 win su 33-27=6 trade | IMPOSSIBILE (9>6) |
| Fee implicite nuovi trade | (135.74+17.11) - 104.90 = $47.96 su 335 = $0.143/trade | IMPOSSIBILE (max fisico fee = 0.07*(1-entry) < 0.07) |
| Fee implicite se 731 tutti nuovi | $30.84/731 = $0.042/trade → entry medio ~0.40 | COERENTE |

In più: la riga `[LATENCY-ARB STATS] v2 | ... (net=...)` esiste solo nel codice
v2 (commit c244c67 del 20/07 11:00, "Rewrite latency_arb v2: strike+vol model").
Il run weekend stampava `[LATENCY-ARB STATS] resolved=... | WR=...` senza "v2"
né "net". ⇒ deploy v2 + `restart reset` il 20/07 → contatori ripartiti da zero.

### Lettura corretta dei numeri v2 (aggregati)
- Entry medio implicito ~0.36-0.40 → breakeven WR = entry ≈ 36.5%
- WR 43.2% > 36.5% → edge apparente +6.7pt; EV netto ≈ +$0.143 per $1 size
- **Red flag**: +14%/trade è enorme. Sospetti da auditare prima di crederci:
  1. entry simulata al best_ask a 0.5-3min dalla scadenza su book sottili —
     l'ask visto via REST può essere stale/ghost (non fillabile in reale)
  2. `best_ask()` fa fallback su midpoint (ottimistico) se /price fallisce
  3. possibile doppio conteggio detect→stale→re-detect sullo stesso contratto
- Audit tool già pronto: `tools/analyze_signals.py` (sezione calibrazione v2:
  reliability table p_model vs WR, Brier, split strike_source/z/distanza strike)

### Bot e dashboard: spiegati dallo stesso reset
- Reset 20/07 ha azzerato anche portfolio bot → dashboard "Capitale $300,
  1 chiusa, $298.21, agg. 14:06" = **tab stale del 20/07 pomeriggio**
- Stato reale lunedì: $292.21, 8 chiuse tutte LOSS (-$7.79), 5 aperte
- Le 4 posizioni harvest Fed sono lo stesso bet duplicato: "Fed increase 25bps"
  No @0.946 (x2) ≈ "no change in Fed rates" Yes @0.943 (x2). Cluster exposure
  li tratta come 2 eventi separati → correlazione nascosta ~$36 sullo stesso esito
- Dettaglio 8 chiusure: richiede bot.log fresco (comandi consegnati)

### Cosa serve per la decisione Step 1 vs stop
1. Output `analyze_signals.py` su `data/latency_arb_signals.jsonl` fresco
2. Conferma `model_version: 2` su tutti i record (nessuna contaminazione v1)
3. Reliability table: se p_model calibrato (gap ~0) e P&L netto positivo
   distribuito (non concentrato in pochi outlier) → v2 credibile → Step 1
4. Se P&L concentrato in entry a prezzi bassissimi o strike_source fallback →
   probabile artefatto di fill → fix modello prima di qualsiasi Step 1

---

## AUDIT v2 PROFONDO — EDGE ILLUSORIO (2026-07-22, logs_monday)

Sorgente: `logs_monday/` (commit faa3b24), tool `tools/audit_v2.py`.
738 resolved, tutti model_version=2, reset 20/07 09:40.

### Numeri aggregati (già noti)
| Metrica | Valore |
|---------|--------|
| WR | 43.1% |
| entry medio | 0.397 |
| P&L lordo / netto | +$131.62 / +$100.50 |
| EV/trade netto | +$0.136 |
| strike_source | **738/738 binance_open** (equity API mai OK) |
| Brier | 0.203 (skill debole; sovraconfidenza -5/−14pt ovunque) |

### Concentrazione (killer)
- Top-10 win = **+$114.44 = 113.9% del P&L netto totale**
- Tutte le top-10: entry 0.07–0.10 (longshot), payout ~+$9–13 su $1 size
- Trimmed senza top 5% delle win (15 trade): **-$54.08** (EV -$0.075)
- Trimmed top 1%: ancora +$61; top 10%: -$139

### Bootstrap CI 95% (10k resample)
| Filtro | n | EV/trade | CI95% | Significativo? |
|--------|---|----------|-------|----------------|
| all | 738 | +0.136 | [-0.007, +0.292] | NO (include 0) |
| trimmed 5% | 723 | -0.075 | [-0.177, +0.032] | NO |
| entry≥0.25 | 512 | +0.018 | [-0.079, +0.116] | NO |
| \|edge\|≥0.15 | 99 | +0.509 | [+0.038, +1.064] | sì ma longshot-driven |
| entry≥0.25 & edge≥0.15 | 80 | +0.088 | [-0.148, +0.327] | NO |

### Fill realism
- Spread implicito ask_up+ask_down-1: mediano **-0.01** (718/738 negativi)
- Ghost proxy (entry<0.15 & opposite ask>0.90): 34 trade, P&L +$44
- 20 win a entry<0.15 = **+$187** netto → gonfiano l'intero risultato

### Strike ufficiale
- `https://polymarket.com/api/equity/price-to-beat/{slug}` → **403 Forbidden**
  (locale con verify=False; su VPS 0/738 successi → stesso fallimento)
- Fallback Binance open 1m è l'unico strike usato: modello valuta un contratto
  potenzialmente diverso dallo strike Chainlink reale

### Bot — 8 chiusure (trades_log.json)
| # | Strat | Mercato | Reason | P&L |
|---|-------|---------|--------|-----|
| 1 | copy | Kalinina/Quevedo tennis | stop_loss | -$1.46 |
| 2 | copy | Estoril van de Zandschulp | stop_loss | -$1.09 |
| 3-6 | harvest | Fed no-change Yes / Fed +25bps No (x2 ciascuno) | stop_loss | -$3.64 |
| 7-8 | harvest | stessi mercati Fed (riaperti @0.86) | stop_loss | -$1.10 |
| **Tot** | | | | **-$7.28** |

- Solo **2/8** sono copy tennis → soglia CI5 (≥5) per esclusione tennis **NON raggiunta**
- **6/8 harvest Fed**: stesso esito economico duplicato su 2 mercati + riaperture;
  SL assoluto -5c (e soft) ha sparato su wobble ~9c di un near-certain. Harvest
  non sta hold-to-resolution come designato in Phase CF.

### Verdetto
> **EDGE PAPER NON ROBUSTO. Step 5d: abbandona latency-arb per capitale reale.**
> Il +$100 netto è un artefatto di 10–20 longshot win a entry 0.07–0.10 su book
> sottili, non fillabili in reale. Nessun subset filtrato (entry band + edge)
> ha CI bootstrap sopra zero. Strike ufficiale non recuperabile.

### Azioni
1. **NO Step 1** ($50 reali latency-arb) — Phase CJ2 cancelled
2. Latency-arb validator: stop o lascia in idle (zero valore operativo)
3. Bot: focus copy+harvest; priorità successiva = dedup harvest Fed / cluster
   correlato + rivedere se SL -5c su harvest near-certain è troppo stretto
4. Tennis copy: monitorare, non escludere ancora (n=2 insufficiente)

---

## Phase CK - diagnosi di contenimento (2026-07-23)

- HARVEST ha riaperto le stesse due condition dopo il cooldown di un'ora:
  `recent_opens` non è un vincolo di unicità per le posizioni ancora aperte.
- I due mercati Fed condividono l'evento `fed-decision-in-july-181`, ma Position
  non conserva `event_slug`; cluster/exposure usa market_slug o condition_id.
- Il backtest COPY usa storia wallet e prezzo medio wallet sulla medesima
  finestra: è un profiler storico, non una prova out-of-sample di edge.
- Ingresso COPY e mark/exit usano midpoint/fallback ottimistici; la validazione
  deve usare ask in ingresso, bid in uscita e costi osservati.
- Il daily halt esistente usa realized P&L e si resetta; mancano run halt,
  quarantena persistente per loss streak e blocco condition dopo stop-loss.
- Dashboard ricostruisce peak dal valore corrente e mostra wallet presi dai
  primi risultati scanner, non il gruppo effettivamente monitorato.
- `restart reset` elimina evidenza; il nuovo contratto operativo deve separare
  restart conservativo, new-run archiviato e reset esplicito `--force`.

Decisione: l'implementazione parte da OBSERVE e non modifica lo stop-loss
HARVEST, perché HARVEST resta disabilitata e non ha edge dimostrato.

### Esito implementazione CK

- Lo snapshot legacy viene migrato senza inventare `event_slug`: cash/equity
  $297.0869, 5 chiuse, peak corretto $300, drawdown 0.971%.
- Per i nuovi segnali, l'identità evento arriva prima dai metadati Gamma; i due
  mercati Fed sono `macro` e condividono `fed-decision-in-july-181`.
- Il fill paper attraversa l'intera profondità: ask VWAP in BUY e bid VWAP in
  SELL/mark. Se la size non è interamente fillabile, il candidato è scartato.
- Il journal registra anche gli scarti con motivo, top-of-book/depth, wallet,
  sorgente/detection timestamp, costi e identità run/signal/evento.
- La promozione può solo autorizzare un altro run paper indipendente; il campo
  `real_money_authorized` del valutatore resta sempre `False`.
