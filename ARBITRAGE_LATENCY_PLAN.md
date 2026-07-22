# Piano Arbitraggio a Latenza — Polymarket Bot

> Documento di riferimento per le prossime settimane. Aggiornare dopo ogni step.
> Ultimo update: 2026-07-13

## Situazione attuale (14:45 dashboard)

- Budget: **$300 USDC paper**, equity $298.28, P&L realizzato -$1.72 (-0.57%), open 2
- Strategia attiva: **COPY** (0W/3L, WR 0%) + harvest + arb_binary + arb_cross
  (arb_binary disabilitato da Phase CI4, copy/harvest/arb_cross attivi)
- Copy perde su tennis in-play (SL -8% spara su break normali); fix CI5 ha
  convertito SL di copy-sport a assoluto -5 cent (non ancora validato live).
- 5 Phase CI1–CI5 implementate (daily halt, liquidity ≥$50K, exit fee, disable
  arb_binary, copy-sport SL assoluto). Bloccano emorragia, NON generano edge.
- **VPS IONOS Germania già disponibile** — latenza ottima a Binance Frankfurt
  (~20-50ms) e Polymarket (~70-100ms). Costo aggiuntivo Step 1: **€0**.

## TESI: dove sono i veri soldi (lezione delle 3 guide)

Per fare soldi veri serve spostarsi da **copy-trading** (WR 0% attestato) a
**latency arbitrage** su contratti crypto 5/15-min (la strategia del bot 0x8dxd
descritta in Guida 2: $313→$2.38M, 98% WR, 26.738 trade). Meccanismo:
Polymarket lagga di ~2.7s vs Binance → compra il lato ovvio prima che il book
si corregga. **Non riproducibile in paper mode (no fill reale)** ma VALIDABILE
su feed reali perStep 0 → solo detection log, zero capitale.

## Roadmap a step (spesa per step)

| Step | Periodo | Spesa | Capitale | Outcome atteso |
|------|---------|------|----------|----------------|
| 0 | questa settimana | €0 | $0 | validazione paper, 200+ signal → WR>70% |
| 1 | settimana 2 (se Step 0 OK) | €0 | $50 USDC | trading reale bankroll minimo |
| 2 | settimana 4 (se Step 1 stabile) | €0 | $200 (da profitto) | scale sizing |
| 3 | mese 2+ | €0 | $500+ | diversificazione (oracle arb, MM) |

Spesa totale mensile fissa target: **€0/month** (VPS IONOS gia pagata; public
API: Binance, Polymarket, Chainlink via Alchemy free-tier tutti gratis).

### Strategie tagliate (costano troppo / non rentabili)

| Strategia | Costo | Perché no |
|-----------|-------|-----------|
| Co-lo dedicata bare-metal | $200+/mese | Non serve a 2.7s window. Serve solo se <0.5s |
| News-based con Claude API | ~$20/mese API | WR 60-75% vs 85-98% latency arb — no brainer |
| Cross-odds esterni (the-odds-api) | $50/mese | API gating, ROI non provato per retail |
| Market making aggressive | complessità | Reversal risk; FIFA queue hard su retail |

## Step 0 (QUESTA SETTIMANA) — validazione paper su feed reali

**Obiettivo**: confermare che l'edge deto dalla Guida 2 esiste ora, sulla VPS
IONOS, prima di investire $50 USDC reali. **Zero capitale, zero ordini.**

**Cosa è stato fatto (13/07):**
- Creato `src/latency_arb.py` (~300 righe): modulo standalone loop 1s
- BinanceFeed: REST polling BTCUSDT/ETHUSDT (prezzo live + momentum 5min)
- PolymarketContractFeed: fetch contratti `Bitcoin/Ethereum up or down Nmin`
  via gamma con scadenza 0.5–15 min
- LatencyArbDetector: calcolo `expected_p(UP)=0.5+K·delta_5min` vs
  `p_yes(Polymarket)`; signal se `|edge|>10pt`; log in jsonl
- Risoluzione virtuale a expiry: calcolo WR + P&L virtual + buckets per edge
- Stats persistente in `data/latency_arb_stats.json`
- Opt-in `POLYMARKET_INSECURE=1` per trust problematico da locale

**Prossime mosse (Step 0):**
- [ ] **Deploy su VPS IONOS**:
  ```
  ssh root@<vps-ionos>
  cd /path/polymarket-prediction
  git pull
  ./start_all.sh restart reset scan
  ```
  Questo installa deps (`pip install -r requirements.txt` incluso certifi
   nuovo), azzera lo stato vecchio, fa scan wallet, e avvia in parallelo:
  bot + dashboard + **latency_arb validator** (Step 0, no ordini, log in
  `logs/latency_arb.log`). Il validatore è ora integrato in start_all.sh
  (PID in `data/latency_arb.pid`, kill via `./start_all.sh stop`).
- [ ] Lasciar girare 5–7 giorni. Target: **200+ signal resolved**.
- [ ] Ogni sera preleva `data/latency_arb_stats.json` + `logs/latency_arb.log`
  (o `./start_all.sh status` per vedere uého). Domani al primo check:
  aprite i log su un file e me li inoltrate.
- [ ] Verificare target: **WR virtuale > 70%** (soglia Guida 2). Se < 60%,
  tornare a Claude Code con i log e iterare model (tuning K, edge_threshold).
- [ ] Verificare bucket edge: confermare che edge 20+ ha WR > edge 10–20 (se
  no, modello rumore). Conferma diminuzione edge con contratti che scadono
  presto (window compressione).
- [ ] **NO ordini, NO USDC.** Siamo in pure detection mode.

### Comandi rapidi su VPS (memorizza)

| Comando | Cosa fa |
|---------|----------|
| `./start_all.sh restart reset scan` | Reset + scan + avvia bot/dashboard/validator (tutto) |
| `./start_all.sh status` | Stato dei 3 servizi (PID + attivo/fermo) |
| `./start_all.sh logs` | Tail live bot+dashboard+latency_arb |
| `./start_all.sh stop` | Ferma tutto 3 servizi |
| `cat data/latency_arb_stats.json` | Stats validatori (WR virt, P&L virt, bucket edge) |
| `tail -n 200 logs/latency_arb.log` | Ultimo 200 line del log validator |
| `wc -l data/latency_arb_signals.jsonl` | N signal totali loggati |

**Stop / pivot conditions:**
- Se WR < 60% a 100 signal → problema: o il modello è rumore, o latenza troppo
  alta con REST a 1s (ed occorre WebSocket <100ms in Step 1). Pivot: riprogettare
  signal model (e.g., regression su storico Polymarket→Binance) oppure accettare
  REST limitato e Step 1 con WebSocket.
- Se 0 signal raccolti in 24h → problema discovery contratti. Pivot: akka
  pattern matcher, oppure usare webhook Polymarket invece di polling gamma.

**Variabili di tuning (in `LATENCY_ARB` cima al file):**
- `momentum_k` = 2.0 (sensibilità model p(UP)). Range 1.5–3.
- `edge_threshold_pct` = 0.10 (10 punti %). — flag wide, trade narrow (Guida 3)
- `max_minutes_to_expiry` = 15 (finestra contratto)
- `poll_interval_sec` = 1.0 (1s per validation; 100–200ms in Step 1 con WS)

## Step 1 (SETTIMANA PROSSIMA) — trading reale $50 su VPS IONOS

**Precondizione**: Step 0 raccolto 200+ signal con WR > 70%.

**Setup (1 giorno di lavoro):**
- [ ] Wallet Polymarket: MetaMask create-new, fund $50 USDC su Polygon (gas
  per deployment ~$0.5, poi $0.01/tx). **Bankroll non spesa**: recuperabile.
- [ ] `pip install py-clob-client>=0.21` (SDK trading Polymarket open source)
- [ ] L2 credentials: derive via private key (vedi snippet Guida 1):
  ```python
  from py_clob_client_v2 import ClobClient
  temp = ClobClient("https://clob.polymarket.com", key=PK, chain_id=137)
  creds = temp.create_or_derive_api_key()
  ```
- [ ] Estensione `latency_arb.py` → `LatencyArbTrader`: piazzamento ordini reali
  Market/taker (pre-ottimizzazione) → upgrade a Limit/maker Step 2.
- [ ] **Kill switch hard**: -8% daily loss = bot halt (gia CI1, riusato), 
  -20% total = deposita withdraw + halt. Telegram alert (bot token gratis).
- [ ] **Sizing**: fractional Kelly 1/4. Inizia $1.5/trade (3% di $50). Cap 5%
  anche su edge estremo (no over-bet su singolo contratto).
- [ ] Run su VPS IONOS (germania): garantisce latenza ottima. UDP same box del
  main bot (copy disabilitato per Step 1).
- [ ] **PRIMA SETTIMANA**: comparison paper-mode (Step 0 stats correnti) vs
  live (slippage reale, fill falliti). Se divergono > 30%, parla con me.

**Target onesto**: +5–15%/mese su $50 = $2.5–7.5/mese. NON è doubling/settimana:
è 3–5x in 4–6 mesi con compounding. I 7942x di 0x8dxd sono caso eccezionale.

## Step 2 (SETTIMANA 4) — scale sizing con profitto

**Precondizione**: Step 1 con 100+ trade reali, WR > 75%, slippage reale < 30%
del paper.

- [ ] Escalation: $50 → $100 ($50 deposito + $50 da profitto −
  prelievi periodici per proteggersi). Sizing $3/trade.
- [ ] Upgrade a LIMIT orders (maker): 0 fee + 25% rebate (crypto 20%). Stesso
  trade, P&L +30% netto vs taker. Richiede gestione FIFO queue (post-primo
  → cancel + rigioca = back of queue), più sample rate di skip. Ne vale la pena
  solo su Step 1 che gia profitte.
- [ ] Statistiche: edge reale = WR_virtuale(Step 0) − slippage − fill_failure_rate
  − fee. Se edge reale > 5 pt/% → scale, se 2–5 pt → mantieni size, se < 2 → halt.

## Step 3 (MESE 2+) — diversificazione zero-cost

- [ ] **Oracle arb** (Guida 2): confronta prezzo Chainlink on-chain (Alchemy
  free-tier) con settlement Polymarket. Wrappa in detector separato, stesso
  modulo infra. 0 costi extra, opportunità rara ma killer.
- [ ] **Market making su crypto liquidity** (Guida 1): limit bid+ask su
  contratti crypto 5/15min, capture spread + rebate 25%. Rischio inventario
  (_configs separatie risk cap). WR/edge più stabile di latency arb ma più lento.
- [ ] Combo: latency arb (aggressivo) + MM (steady) + oracle (raro).
  Correlazione bassa = drawdown più basso, sizing più aggressivo per strategia.

## Decisione integrata con il copy bot esistente

Il copy bot su paper produce loss (0W/3L), ma le 5 Phase CI1–CI5 lo rendono
"safe-drip": sanguina lentamente ma non blowup. **Opzioni:**

1. **Mantenere attivo copy su paper** durante Step 0/1 come canarino —
   ci dice se il mercato ha edge copy-side da inseguire post-latency-arb-block.
2. **Disabilitare copy** fintantoche Step 1 non è profitte. Risparmia log,
   chiaro focus su latency arb. Default consiglito.

Step 0: lasciare copy attivo in parallelo (usa un altro PID, non interferisce
con `latency_arb.py`). Step 1: disabilitare copy (set STRATEGIA env flag).

## Logbook (compilare dopo ogni step)

| Data | Step | Azione | Risultato | Note |
|------|------|--------|-----------|------|
| 2026-07-13 | 0 | Creato modulo `latency_arb.py` | Compila + run smoke OK | Local Windows-cloudflare blocca gamma; run VPS |
| 2026-07-14 | 0 | Check log VPS dopo ~21h (bot.log/dashboard.log/scan) | Main bot OK: 1W/0L +$1.23 (+0.41%); NO log latency_arb → validatore non partito | Deploy ieri fatto via deploy_polymarket.sh (bot+dashboard only). Manca il `./start_all.sh restart reset scan` per attivare latency_arb.py |
| 2026-07-14 | 0 | Setup git su VPS + avvio validatore | git installato, cartella /root/polymarket-prediction convertita in repo tracking origin (reset --hard 043aacc, working tree clean). 3 servizi attivi: bot PID 2352005, dashboard 2351988, **latency_arb 2352026** | `git pull` ora possibile (niente più copia a mano). Stato trading preservato (harvest Fed $9.04). Da verificare: latency_arb.log sano (no gamma 403/Traceback) |
| 2026-07-14 | 0 | Fix discovery: end_date_max filter + primissimi signal | Diagnostic v3: gamma hard-cappa a 100 risultati, volumeNum ordering nasconde crypto 5/15min. Strategia C (end_date_min/max window) trova 28 short-expiry crypto. Fix `_refresh_active()` | **Primi 6 SIGNAL in <90s**: 4 LONG_YES (edge +0.21/+0.48) + 2 LONG_NO (edge -0.10/-0.12). pending=6 in attesa resolve. stats.json al primo resolve |
| 2026-07-17 | 0 | Check 2 giorni post-fix discovery (utente manda output) | wc -l signals.jsonl = 4032 | grep -c RESOLVE = **0**, grep -c SIGNAL = 2019, stats.json inesistente, pending=6 costante. **Resolver non sta risolvendo némers**. 2 es LONG_YES con Δ5m Binance negativo (-0.24%/-0.06%) → forte indizio outcomes[0]=DOWN (anticipato in `progressi.txt`). Loop: detect → pending → 10min stale→ ri-detect. |
| 2026-07-17 | 0 | Fix 3 bug (resolver, outcomes per nome, stats heartbeat) | `resolve_contract` ora parse `outcomePrices` (JSON-encoded), index max prezzo → vincitore. `scan_cycle` match outcomes per NOME via `_find_outcome_idx`. `heartbeat_save_stats()` ogni 60 cicli. Aggiunto `tools/debug_resolver.py`. py_compile OK. | Da validare su VPS con `python tools/debug_resolver.py --max 5` prima di deploy: conferma `outcomePrices` e' il campo giusto e hi_idx >= 0.95 sui risolti. |
| 2026-07-20 | 0 | Verdetto weekend + riscrittura modello v2 | Weekend: 396 resolved, **WR 34.6%**, P&L virt -$17.11, bucket 10-20 e 20+ identici (~34%) → modello v1 era inerte (K=2 · Δ5m ≈ ±0.004 vs soglia 0.10): segnalava solo p_market lontano da 0.50 e comprava il lato OPPOSTO al flusso informato (fade del favorito). Riscritto `latency_arb.py` **v2**: p_up = Φ(ln(S/strike)/(σ√τ)) con strike da equity API `price-to-beat` (fallback open Binance 1m a inizio finestra), σ da log-return 1m (lookback 30min), finestra operativa 0.5–3min, entry al best ask CLOB, taker fee crypto 0.07·p·(1-p) simulata nel P&L netto. Aggiunto `tools/analyze_signals.py` (split direzione/asset/minuti + EV strategia inversa). | Prima del redeploy: salvare da VPS `data/latency_arb_signals.jsonl` + `logs/latency_arb.log` (il `reset` li cancella) e runnare analyze_signals per conferma ipotesi inversione. Criterio v2: edge NETTO > 0 a 100+ resolved, altrimenti pivot (maker/WS o stop). |
| 2026-07-22 | 0 | Audit v2 su logs_monday (738 resolved) | **EDGE ILLUSORIO.** Top-10 win = 114% del P&L netto (entry 0.07–0.10); trimmed top-5% = -$54; bootstrap CI include 0; entry≥0.25 & edge≥0.15 CI include 0. Strike ufficiale 0/738 (API 403). | **Step 5d: STOP.** Nessun Step 1 / $50 reali. Phase CJ2 cancelled. Focus bot copy/harvest. Tool: `tools/audit_v2.py`. |

---

**Regola d'oro**: nessuna scommessa (capitale reale) senza prima Step 0 con
200+ signal e WR>70% su feed reali. Pancia e dati, non entusiasmo.

**Contatti tecnici/repo**:
- Codice: `src/latency_arb.py` (modulo), `src/config.py` (LATENCY_ARB knobs),
  `data/latency_arb_signals.jsonl` (log), `data/latency_arb_stats.json` (stats)
- Deploy VPS: `vps_manager.sh` (esistente); aggiungere service systemd per
  `latency_arb.py` in `deploy/systemd/` quando stabile
- Guide di riferimento: `guida_modelli_online.txt` (1: fee model, 2: bot 0x8dxd,
  3: VWAP per arb detection)