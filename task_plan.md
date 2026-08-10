# Task Plan: Polymarket Bot — FIX EMERGENZA PERFORMANCE (-5.63%, WR 24%)

## Active Phase CU: hardening post-audit shadow (2026-08-10)

- [x] CU1: inventariare contratti scanner/run/simulator/dashboard e test esistenti
- [x] CU2: correggere stop sport sulla basis raw ask con migrazione compatibile
- [x] CU3: congelare domini per run e applicare eligibility wallet-domain
- [x] CU4: persistere quarantena cross-run dei wallet falliti e integrarla nello scan
- [x] CU5: rendere lo shadow portfolio-constrained con equity MTM e circuit breaker
- [x] CU6: esporre metriche e motivi in API/dashboard e aggiornare protocollo operativo
- [x] CU7: test unitari, migrazioni, suite completa, smoke test e audit del diff
- [x] CU8: commit/push e consegna rollout VPS sicuro in OBSERVE

Vincoli:
- nessun capitale reale e nessuna promozione automatica a paper;
- preservare export e modifiche utente non correlate;
- il run shadow corrente resta evidenza diagnostica e non viene riscritto;
- la quarantena usa solo run prospettici conclusi, senza tuning retroattivo.

Errori incontrati:
- primo inserimento CU fallito per mismatch encoding dell'intestazione storica;
  retry applicato usando l'ancora ASCII stabile della fase CT.
- primo inserimento findings CU fallito per la stessa intestazione legacy;
  retry applicato sull'ancora ASCII della sezione audit shadow.
- prima suite mirata CU: 34/35 pass; unico failure atteso nel test che richiedeva
  tre shadow open unconstrained. Aggiornare il contratto a max 2 + reject.
- seconda suite mirata CU: 65/66 pass; il test attendeva drawdown >3%, ma il
  run breaker -$6 su $300 scatta correttamente a circa 2,07%. Corretta la soglia
  del test senza allentare il circuit breaker.
- una patch combinata su protocollo e piano non si e applicata perche usava il
  rendering mojibake invece dei caratteri UTF-8 reali; rilettura esplicita UTF-8
  e retry mirato, senza modifica parziale.

## Active Phase CT: audit shadow 24h (2026-08-10)

- [x] CT1: verificare integrita archivio, commit, run_id e durata
- [x] CT2: verificare salute processi, feed, ledger e timestamp
- [x] CT3: ricostruire candidati e motivi di scarto
- [x] CT4: ricostruire lifecycle shadow, P&L, costi e concentrazioni
- [x] CT5: verificare anomalie di codice/dati e affidabilita dashboard
- [x] CT6: formulare verdetto operativo senza modificare il run

Verdetto: raccolta tecnicamente sana, ma NO-GO per paper e denaro reale. Il
campione shadow perde anche prima delle fee e presenta un bug confermato nella
basis dello stop sport, domini di validazione adattivi/non congelati e
riselezione cross-run del wallet gia fallito. Archiviare questo run come
diagnostica; correggere questi contratti e avviare un nuovo campione indipendente.

Vincoli:
- audit read-only del bundle `polymarket-shadow-20260810T123016Z.tar.gz`;
- nessun restart/new-run e nessuna promozione a paper o capitale reale;
- 24 ore misurano salute/qualita dati, non dimostrano redditivita.

Errori incontrati:
- lettura combinata dei tre planning file superata dal limite di output e
  troncata; riprendere con chunk espliciti fino a EOF prima dell'audit.
- primo inserimento CT fallito per ancora encoding sulla prima intestazione;
  secondo tentativo multi-file fallito sull'intestazione progress legacy.

## Active Phase CS: validazione shadow e blocco real-money (2026-08-09)

- [x] CS1: ripristinare contesto, inventario worktree e contratti esistenti
- [x] CS2: progettare/persistire shadow ledger append-only per COPY eligible
- [x] CS3: seguire mark e chiusure shadow con bid, fee e regole paper identiche
- [x] CS4: completare journal portfolio-gated e metrica `passed_pretrade`
- [x] CS5: rendere impossibile qualunque autorizzazione real-money automatica
- [x] CS6: API/dashboard shadow, statistiche robuste e gate di promozione
- [x] CS7: unittest, migrazioni, smoke test, audit diff e documentazione
- [ ] CS8: commit/push e istruzioni rollout OBSERVE dopo verifica completa

Vincoli:
- nessuna promessa di profitto o crescita giornaliera: il criterio verificabile
  e un EV netto positivo con intervallo di confidenza su campione indipendente;
- nessun capitale reale in questa fase e nessuna riattivazione del run fallito;
- non scegliere soglie retroattive sui soli quattro trade del run CR;
- preservare file e modifiche utente non correlate presenti nel worktree.

Errori incontrati:
- primo aggiornamento di `VALIDATION_PROTOCOL.md` usava il rendering mojibake
  mostrato dal terminale invece dei veri caratteri UTF-8; nessuna patch parziale,
  ripetuto con Unicode corretto e applicato.
- lo smoke combinato ha validato la sintassi JavaScript, poi `bash -n` ha fallito
  perche il comando `bash` risolve al wrapper WSL senza `/bin/bash`; ripetere con
  Git Bash esplicito e poi eseguire separatamente `git diff --check`.
- `rg` con glob Unix (`README*`, `*.md`) su PowerShell ha prodotto errore 123;
  i match nei percorsi espliciti erano validi; retry previsto con `rg --glob`.
- tre patch su `progress.md`/template hanno incontrato ancore non identiche per
  encoding legacy; nessuna modifica parziale ai file coinvolti, retry con ancore
  ASCII o codepoint esatti e applicazione completata.
- due tentativi di aggiornare `progress.md` con un'intestazione non identica
  (anche per encoding legacy) non sono stati applicati; retry su ancora ASCII.
- una patch multi-file per integrare i file shadow in run-state/start script
  aveva un hunk non applicabile; ripetuta con patch separate con esito positivo.
- primo inserimento CS fallito per mismatch dell'encoding nell'intestazione;
  retry con ancora ASCII stabile, senza toccare il contenuto storico.
- primo inserimento findings CS ha incontrato lo stesso mismatch; risolto con
  ancora ASCII sulla sezione audit.
- inventario run-state ha incluso per errore `start_all.ps1` inesistente;
  nessun effetto sui file, proseguire con `start_all.sh` e `tools/run_state.py`.
- prima suite CS: 49/51 pass; due failure deterministiche. Un'asserzione attendeva
  journal v4 invece di v5; il test direct-call OBSERVE non aveva ancora un ledger
  da cui recuperare il run_id shadow al restart. Aggiornati contratto e save run.
- seconda suite CS: 51/51 test superati.

## Active Phase CR: audit paper_validation 48h (2026-08-09)

- [x] CR1: verificare integrita export, commit, run_id e stato HALTED
- [x] CR2: riconciliare ledger, journal, trade log, equity e safety state
- [x] CR3: ricostruire i quattro trade con wallet, latenza, book, fee e uscite
- [x] CR4: distinguere segnale, execution cost, stop policy e anomalie feed
- [x] CR5: valutare candidati bloccati, concentrazione e qualita wallet
- [x] CR6: formulare verdetto e prossimo intervento senza riattivare il run

Verdetto: NO-GO alla riattivazione del run e al denaro reale. Il run e sano ma
fallisce 8/9 criteri di promozione. Prima del prossimo campione servono journal
completo sui portfolio gate, metrica dashboard `passed_pretrade` e tracking
shadow di tutti i candidati validi per misurare EV netto senza il bias del cap 2.

Vincoli:
- analisi read-only dell'export `polymarket-paper-20260809T142445Z.tar.gz`;
- nessuna riattivazione manuale della quarantena e nessun capitale reale;
- quattro trade non bastano per provare edge, ma il guardrail 3-loss termina il run.

Errori incontrati:
- Primo parser journal ha usato il default Windows cp1252 ed e fallito su UTF-8;
  retry previsto con `encoding="utf-8"`, senza modificare i dati sorgente.
- Primo calcolo hurdle sui candidati portfolio-gated assumeva `entry_price`
  sempre valorizzato; alcune righe rejected lo lasciano null. Retry usando
  `costs.entry_price`/ask+fee derivata e scarto esplicito dei record incompleti.
- La lacuna non e recuperabile retroattivamente: i record bloccati hanno perso
  evaluation/costi al momento della scrittura. Le conclusioni quantitative sui
  costi si limitano quindi ai quattro trade effettivamente aperti.

## Active Phase CQ: equity e cash netti delle fee (2026-08-07)

- [x] CQ1: riprodurre la discrepanza tra mark lordo, P&L netto e cash
- [x] CQ2: centralizzare il prezzo liquidabile netto per posizione
- [x] CQ3: valorizzare equity/circuit breaker al bid meno fee di uscita
- [x] CQ4: accreditare alla chiusura i proventi netti, senza doppia fee
- [x] CQ5: migrazione della posizione paper gia aperta e test regressione
- [ ] CQ6: commit, push e restart conservativo del run VPS

Vincoli:
- preservare `run-20260807T141814-a65fb998` e la posizione aperta;
- sulla VPS usare solo `restart`, mai `new-run`;
- nessun ordine reale: il run resta esclusivamente simulato.

## Active Phase CP: audit OBSERVE abbreviato 24h (2026-08-07)

- [x] CP1: verificare integrita bundle, commit, run_id e durata effettiva
- [x] CP2: verificare process health, traceback, errori feed e freschezza stato
- [x] CP3: profilare journal corrente per unicita, eleggibilita e motivi reali
- [x] CP4: auditare prezzi eseguibili, latenza, profondita, eventi e wallet
- [x] CP5: correggere fee dinamiche per-market e journal fail-closed
- [x] CP6: testare regressioni e decidere GO/NO-GO per `paper_validation`

Verdetto: GO condizionato al deploy della correzione fee. Il campione 24h passa
il gate tecnico; il paper deve partire come nuovo run pulito e resta sottoposto
ai criteri minimi 100 chiuse / 30 eventi / 14 giorni.

Vincoli:
- 24 ore sono un gate tecnico abbreviato, non una prova di redditivita;
- nessuna mutazione del run o passaggio automatico al paper durante l'audit;
- solo dati del `run_id` corrente contano nel verdetto.

Errori incontrati:
- Primo aggiornamento combinato dei planning file fallito per mismatch di encoding
  nelle vecchie intestazioni; risolto applicando patch su ancore ASCII stabili.
- Probe locale Gamma fallita prima per decoding Windows, poi per certificato TLS
  locale con hostname mismatch; non verra usata come fonte operativa. Le prove
  ufficiali sono state recuperate dalla documentazione Polymarket.

## Active Phase CO: paginazione feed e copertura source (2026-08-06)

- [x] CO1: paginare `/positions` oltre 200 record fino ai limiti ufficiali
- [x] CO2: ampliare il lookup BUY sorgente senza superare i limiti API
- [x] CO3: ridurre l'impatto dei timeout burst senza creare falsi stati
- [x] CO4: test paginazione, error propagation, dedup e regressioni complete
- [x] CO5: documentazione, commit e push; rollout OBSERVE pronto per VPS

Vincoli:
- il run VPS corrente resta OBSERVE e non viene promosso a paper;
- dashboard protection ancora fuori scope;
- nessun retry che trasformi errori in risposte vuote valide.

## Active Phase CN: hardening pre-paper dopo audit OBSERVE (2026-08-05)

- [x] CN1: distinguere snapshot wallet vuoto valido da errore/timeout
- [x] CN2: preservare baseline wallet durante flapping ed evitare falsi delta
- [x] CN3: impedire false uscite COPY quando il wallet sorgente non è leggibile
- [x] CN4: rendere obbligatorio il trade sorgente verificato e usare il suo prezzo per il drift
- [x] CN5: calcolare top-book e VWAP da un unico snapshot e journal v3 ricostruibile
- [x] CN6: test regressione, smoke test, documentazione, commit e push

Vincoli:
- protezione/autenticazione dashboard esplicitamente fuori scope su richiesta;
- nessun capitale reale e nessuna attivazione automatica di `paper_validation`;
- il run OBSERVE analizzato resta evidenza archiviata e non viene riscritto.

## Active Phase CM: audit campione OBSERVE (2026-08-03)

- [x] CM1: verificare integrità bundle, commit, run_id, periodo e salute servizi
- [x] CM2: profilare journal per decisioni, motivi, wallet, categorie ed eventi
- [x] CM3: analizzare latenze, spread, profondità, VWAP, fee e qualità dati
- [x] CM4: verificare duplicati, copertura temporale, concentrazione e anomalie
- [x] CM5: decidere se promuovere COPY a `paper_validation` o prolungare OBSERVE
- [x] CM6: documentare verdetto e comandi VPS sicuri

Vincoli:
- nessuna modifica al campione VPS durante l'audit;
- `eligible` non equivale a trade profittevole;
- nessun capitale reale;
- decisione basata solo sul run corrente prospettico.

## Active Phase CL: OBSERVE misurabile e health corretto (2026-07-24)

- [x] CL1: pipeline comune di valutazione COPY e journal v2 deduplicato
- [x] CL2: lookup trade sorgente `/activity` e paginazione API max 500
- [x] CL3: timestamp UTC, state age backend e bot health
- [x] CL4: wallet congelati per run e archiviazione completa wallet quality
- [x] CL5: API/dashboard candidate journal e banner separati
- [x] CL6: test, smoke test, documentazione, commit e push

Decisioni bloccate:
- OBSERVE valuta i filtri pre-trade ma non muta portfolio/cash/cooldown.
- `eligible` significa controlli pre-trade superati, non profitto atteso.
- I wallet restano fissi per tutto il run; refresh solo con `new-run scan`.
- Il run VPS `run-20260723T095411-5d89b02d` resta solo diagnostico.

## Goal
Fermare l'emorragia di capitale ($300→$283, -5.63%, WR 24%) e trasformare il bot
in un sistema profittevole. ROOT CAUSE: il bot entra a prezzi estremi (0.999, 0.036,
0.026) dove lo stop-loss percentuale viene triggerato dal RUMORE di mercato, non
dal fallimento del segnale. Risk/reward invertito: gain minuscolo, loss enorme.

## Current Phase
Phase CO feed coverage COMPLETE nel codice → pubblicazione e nuovo OBSERVE 48h

## Phase CK: Arresto perdite e nuova validazione (2026-07-23)

- [x] CK1: integrare snapshot VPS senza perdere storia o file locali
- [x] CK2: execution_mode observe/paper_validation e strategy scan/paper gates
- [x] CK3: run identity, event identity, dedup globale, blocchi e circuit breaker
- [x] CK4: prezzi eseguibili e candidate journal append-only
- [x] CK5: restart/new-run/reset sicuri e dashboard operativa no-cache
- [x] CK6: unittest, migrazioni, smoke test locale e documentazione promozione

Da eseguire dopo il deploy: `restart` conservativo, conferma VPS dello snapshot
$297.09 e osservazione 24h con zero nuove aperture/traceback.

Stato iniziale deliberato: OBSERVE, zero nuove aperture, latency-arb fermo.
COPY resta solo in scansione; HARVEST e tutte le altre strategie restano
disabilitate fino a una decisione futura separata.

## Diagnosi Root Cause (DASHBOARD 07/07)

### I numeri
| Strategia | Open/Closed | Realized P&L | WR | Verdetto |
|-----------|-------------|-------------|-----|----------|
| WHALE     | 4/6         | -$6.99      | 17% | ❌ KILL |
| MOMENTUM  | 0/4         | -$4.20      |  0% | ❌ KILL |
| CONTRARIAN| 0/3         | -$3.00      |  0% | ❌ KILL |
| HARVEST   | 0/8         | -$3.13      | 38% | ⚠️ FIX |
| COPY      | 0/4         | -$1.54      | 50% | ✅ KEEP |
| Arb/cross/sniper/theta | 0/0 | $0 | - | ⏸ DISABLE |

### Il problema FONDAMENTALE: SL percentuale a prezzi estremi

**Esempio Whale (pegzimo):**
- Compra No @ 0.999 (Cape Verde Semifinals) → SL -6% triggera a 0.939
- Max gain: $0.001/share (a $1.00) | Max loss: $0.06/share (a 0.939)
- **Risk:Reward = 60:1 SFASCIO**. Basta un tick di rumore → stop loss.

**Esempio Whale longshot:**
- Compra Yes @ 0.036 (Mexico win) → SL -6% triggera a 0.0338
- 0.002 di move assoluta = stop loss. Il rumore normale è 1-2 tick.
- Risultato: -21.70% (SL hard triggerato oltre il -6% per gap)

**Esempio Contrarian:**
- Compra Yes @ 0.026 (USA win) → SL -4% triggera a 0.025
- 0.001 di move = stop loss. UN TICK.

**Esempio Momentum:**
- Compra No @ 0.992 (Norway win) → SL -5% triggera a 0.942
- Move rilevata: YES sceso da 0.0085 a 0.008 = -5.9% → "momentum!"
- Ma 0.0005 assoluti = rumore puro, non trend.

**Consequenza: 19/25 trade chiusi sono STOP LOSS.** Il 76% delle chiusure è SL.
Non è "sfortuna" — è un difetto strutturale: entrate a prezzi estremi + SL
percentuale = macchina da perdita garantita.

### Altri problemi
1. **9 strategie attive, nessuna validata.** Il precedente “89% WR COPY” era
   un profilo storico in-sample, non una prova di edge eseguibile.
2. **Whale/contrarian non hanno filtro banda prezzo.** Comprano a qualsiasi prezzo
   seguendo la whale, anche 0.999 o 0.02.
3. **Momentum: move detection a prezzi estremi è rumore.** 5% di 0.008 = 0.0004.
4. **Sizing 6-13% amplifica ogni loss.** A 13% sizing, -$1.85 medio perde = -0.8%
   portafoglio per trade. Con WR 24% = drain costante.
5. **Harvest SL -4% su entry 0.985 triggera a 0.946.** 3.9 cent di move = rumore
   normale per near-certain market. In più, early TP +4% lascia juice sul tavolo
   (harvest dovrebbe hold-to-resolution per payout pieno).

## Piano di Fix (Phase CC-CG)

### Phase CK: Fix wallet DISABLED non swappato — swap_losers senza riserve [BUG]
Obiettivo: wallet con status='disabled' (WR<0.45 o nostro P&L<0) restava nella
lista monitorati perche swap_losers ritornava la lista inalterata se non
aveva riserve. Il wallet DISABLED occupava uno slot sprecando risorse e
generando trade perdenti (soft-disable dimezza size ma continua a copiare).

- [x] wallet_manager.py swap_losers: RIMUOVE SEMPRE i losers dalla lista, anche
      senza riserve. Meglio 9 wallet attivi che 10 con 1 perdente.
- [x] wallet_manager.py swap_losers: se la lista scende sotto top_active, stampa
      avviso "rescan necessario"
- [x] main.py _maybe_wallet_quality_refresh: se lista < top_active dopo swap,
      triggera _run_wallet_scan + _reload_monitored_wallets per rifornire la lista
- [x] Sintassi verificata
- **Status:** complete

### Phase CJ: Fix categorizzazione mercati — crypto/weather = 0 [DIAGNOSTICA]
Obiettivo: lo scanner mostrava crypto=0, weather=0, politics=84, sport=5 su 300
mercati. Gli altri 211 finivano in "other". Cause:
1. Keyword crypto troppo strette: " eth " (con spazi) non matcha "ETH/USD", "$ETH"
2. Keyword corte ambigue in politics: "mp" matcha "temperature" (teMPerature),
   "dem" matcha troppe parole. Sopra categorizzava weather come politics!
3. Mancavano token popolari (pepe, shiba, ltc, avax, matic, etc.) e pattern
   comuni (etf, halving, dip to, reach $, close above)
4. Mancavano keyword weather (heat, cold, flood, drought, forecast, °f)
5. Nessun log diagnostico per vedere cosa c'era in "other"

- [x] categories.py: keyword crypto ampliate (token + pattern $eth/eth-/eth/)
- [x] categories.py: rimosse keyword politics ambigue ("mp", "dem")
- [x] categories.py: keyword weather ampliate (heat, cold, flood, drought, °f)
- [x] categories.py: keyword politics ampliate (trump, biden, gop, midterm, caucus)
- [x] scanner.py: log diagnostico — stampa primi 15 mercati "other" per debug
- [x] Test: "temperature in NYC" ora weather (era politics per "mp" match)
- **Status:** complete

### Phase CI: Fix wallet orfani — posizioni copy dopo wallet rotation [EMERGENZA]
Obiettivo: quando un wallet copiato sparisce dalla lista monitorati
(rotazione 3h, quality swap 15min), le posizioni copy NON devono essere
chiuse forzatamente a "exit". Devono essere gestite con SL/TP.

Bug trovato: `reconcile()` riga 588 chiudeva `if asset not in qualifying:
close("exit")`. Ma `aggregate` contiene SOLO asset dei wallet monitorati.
Se il wallet viene rimosso dalla lista, l'asset non e' in aggregate →
chiusura forzata a qualsiasi prezzo.

Inoltre `_reload_monitored_wallets` (auto_rescan 3h) NON resettava
`prev_holdings` → i wallet nuovi venivano trattati come "tutti nuovi" →
copia massiva del loro bag preesistente (bug P2/P10 ricorrente).

- [x] simulator.py reconcile: nuovo parametro `monitored_wallets` set.
  Se `source_wallet` ancora in monitored → asset non in aggregate = wallet
  ha venduto → exit legittimo. Se `source_wallet` NON in monitored →
  wallet rimosso → NON chiudere a exit, gestisci con SL/TP.
- [x] main.py: passa `monitored_wallets=set(self.monitored_addresses)` a reconcile
- [x] main.py _reload_monitored_wallets: reset `prev_holdings = None` quando
  ci sono wallet aggiunti/rimossi (evita dump bag preesistente nuovi wallet)
- **Status:** complete

### Phase CC: Triage — Disabilita strategie perdenti [EMERGENZA]
Obiettivo: fermare il sanguinamento. Disabilitare whale, momentum, contrarian,
sniper, theta. Tenere solo copy + harvest + arb_binary.
- [x] config.py: aggiunto enabled=False a whale/momentum/contrarian/sniper/theta
- [x] main.py: gate ogni strategia con check `STRATEGIES[name].get("enabled", True)`
  in _should_scan()
- [x] Le 4 posizioni whale aperte (Strait of Hormuz, Ghana) vengono lasciate
  risolvere naturalmente (sono near-certain, andranno in profitto a resolution)
- **Status:** complete

### Phase CD: Fix Stop Loss — SL assoluto per prezzi estremi
Obiettivo: lo SL percentuale non funziona a prezzi estremi. Usare SL basato su
**delta prezzo assoluto** (centesimi) invece di percentuale.
- [x] simulator.py: aggiunto stop_loss_abs (es. -0.03 = esci se prezzo scende
  di 3 cent dall'entry) — implementato per harvest, momentum, whale, directional
- [x] Logica: `if (cur - entry) <= stop_loss_abs: close` (SL assoluto in cent)
- [x] Harvest: SL assoluto -0.05 (5 cent) invece di -4%. soft_exit -0.15 absolute
- [x] Copy: mantiene SL percentuale -8% (ok in banda 0.30-0.70)
- [x] Whale: SL assoluto -0.03 (3 cent) + SL % fallback
- **Status:** complete

### Phase CE: Fix Entry Price Bands — niente ingressi a prezzi estremi
Obiettivo: NESSUNA strategia entra sopra 0.95 o sotto 0.05. A questi prezzi
il risk/reward è invertito e lo SL è rumore-trigger.
- [x] config.py: aggiunto entry_price_min/max a whale (0.15-0.85), momentum
  (0.15-0.85), contrarian (0.10-0.90), sniper/theta (SL abs aggiunto)
- [x] strategies.py: whale scan filtra entry band
- [x] strategies.py: momentum scan filtra entry band + min_move 5%->8%
- [x] strategies.py: contrarian scan filtra entry band sul fade side
- [x] Harvest: fav_min 0.78->0.85, fav_max 0.985->0.95 (entry band nativa)
- **Status:** complete

### Phase CF: Fix Harvest — hold-to-resolution, no early TP
Obiettivo: harvest deve tenere fino a resolution per payout pieno $1.
- [x] config.py: harvest_take_profit_pct 0.04 -> 0.0 (early TP disabilitato)
- [x] Harvest: SL assoluto -0.05 (5 cent) invece di -4% percentuale
- [x] Harvest: soft_exit -0.15 absolute (-15 cent) per black-swan protection
- [x] Harvest: cap 30%->25%, max_positions 6->4, max_single 15%->10%
- **Status:** complete

### Phase CG: Sizing conservativo — torna a 3% finché WR<50%
Obiettivo: con WR 24%, sizing 6-13% è suicidio.
- [x] config.py: sizing_tiers 6% -> 3% base, tier1 5%, tier2 8%, tier3 10%
- [x] max_open_positions: 12 -> 8
- [x] reserve_ratio: 15% -> 20%
- [x] max_position_size: 6% -> 3% floor
- [x] Kelly disabilitato (kelly_enabled: False)
- [x] Trailing stop disabilitato (trailing_stop_enabled: False)
- [x] sizing_wr_gate: 0.50 -> 0.45
- **Status:** complete

### Phase CH: Re-validazione e deploy
- [x] Test live locale: bot instanzia OK, strategie gated correttamente
- [ ] Deploy su VPS (utente copia folder + restart reset)
- [ ] Verifica post-deploy: sizing 3%, solo copy+harvest+arb attivi, niente trade a 0.99+
- [ ] Monitorare 24-48h: target WR >45%, P&L flat/positivo
- [ ] Dopo 30 trade: se WR<40% → kill strategy, se WR>55% → scale sizing
- **Status:** in_progress

## NUOVO BLOCCO: Phase CI1-CI5 — Lezioni da 3 guide online (2026-07-13)

Obiettivo del blocco: applicare le lezioni delle 3 guide (guida_modelli_online.txt)
che sono riprodotte SENZA spendere infra/ API. Le guide descrivono latency-arb su
crypto 5/15-min (bot 0x8dxd, 98% WR) — STRATEGIA DIVERSA dalla nostra (wallet-
copy su sport/politics). Non possiamo competere su latenza (20s poll vs 2.7s
edge window). Lezioni azionabili = risk mgmt + liquidity + fee-model + copy-sport.
Vedi findings.md sez "Studio 3 guide online" per dettagli di ciascuna L1–L6.

### Phase CI1: Daily loss limit + daily halt [L1] — risk management hardening — COMPLETE
Obiettivo: Guida 2 dice "Claude +1322% vs OpenClaw liquidato = solo differenza
risk mgmt". Noi abbiamo equity_floor lifetime e ruin, ma non un DAILY counter.
- [x] simulator.py: tracciare realized_pnl_today (reset a mezzanotte via date.today())
- [x] config BUDGET: daily_loss_limit_pct = -0.08 / daily_loss_warn_pct = -0.05
- [x] open_position + execute_opportunity gate: daily_halt blocca nuove aperture
- [x] config MONITOR: daily_loss_warn_pct = -0.05 (warning a -5% giornata)
- [x] Persistenza stato halt in data/daily_halt.json + reset a mezzanotte
- **Status:** complete

### Phase CI2: Filtro liquidità mercato ≥$50K per harvest/arb [L2] — COMPLETE
Obiettivo: Guida 2 esplicito — "solo mercati con >$50.000 liquidità". Noi usiamo
min_book_size (profondità best-level 15–50 USDC), NON volume totale mercato.
- [x] portfolio_sync.py: get_active_markets/get_active_events giá popolano m["volume"]
- [x] simulator.execute_opportunity: gate hard su opp.market_volume < min_market_volume_usdc
- [x] config STRATEGIES: min_market_volume_usdc=50000 in harvest/arb_binary/arb_cross
- [x] Opportunity.market_volume popolato in scan di ArbBinary/Harvest/ArbCross
- [x] copy: NON applicato (segue wallet, mercato gia scelto dal wallet)
- **Status:** complete

### Phase CI3: Fee taker su USCITA (SL/TP) nel simulatore [L3] — COMPLETE
Obiettivo: model)liamo solo fee d'INGRESSO. P&L close `(exit-entry)*shares` non
deducede fee uscita. Su sport a 0.50 uscita costa ~1.5% per leg → -$0.13/trade
nascosto. Resolution-hold (harvest) NON paga fee (settlement non trade).
- [x] simulator._exit_fee_adjusted(pos, exit_price, reason): deduce taker fee per SL/TP/exit
- [x] close_by_asset + _close_by_pid: pnl ora NETTO (entry_fee + exit_fee); log synch
- [x] reason == resolved → NO exit fee (settile $1/$0 no crossing book)
- [x] log: stampa fee_note su riga Exit; trade log usa exit_eff
- **Status:** complete

### Phase CI4: arb_binary → DISABLE in paper [L4] — COMPLETE
Obiettivo: Guida 1 formula `rate·p·(1−p)` → fee MAX a 0.50 dove i gap arb sono
più grassi. arb_binary come TAKER in coin-flip = breakeven netto. Trova 0 opp.
Vivo solo come maker (limit order) — non simulabile onesto in paper (FIFO queue
non esiste; simuliamo fill istantaneo a best_ask = ottimistico).
- [x] config STRATEGIES.arb_binary.enabled=False (taker fee = edge in coin-flip)
- [x] _should_scan gia gestisce enabled gate (Phase CC) — no code change
- [x] anche min_market_volume_usdc=50000 aggiunto per quando verrà riattivato
- Decisione finale: DISABLE (complessità senza valore in paper; maker non simulabile)
- **Status:** complete

### Phase CI5: copy-sport SL assoluto -5c — fix 0W/3L tennis in-play [L6] — COMPLETE
Obiettivo: copy 0W/3L causa tennis in-play dove SL -8% spara su swing normali di
gioco (break = 10–15% move anche su risultato finale corretto). NON è ingresso
tardivo vs wallet (drift filter ok): è alta varianza del copy-in-play.
- [x] config STRATEGIES.copy: sport_stop_loss_abs=-0.05, sport_hard_stop_loss_abs=-0.10
- [x] simulator._copy_sl_tp_decision(pos, cur, sl, tp): helper branch sport vs altri
- [x] reconcile: entrambi i branch (wallet monitorato + wallet rimosso) usano helper
- [x] Test: sport -2.8c=hold, -5.8c=stop_loss, -10.8c=hard_sl; other -8.1%=stop_loss
- [ ] Da validare: se SL assoluto non migliora WR dopo 10 trade sport → fallback
  a esclusione copy su tennis in-play (vedi decision log)
- **Status:** complete

### Decisions Made (aggiornamento 2026-07-13)
| Decision | Rationale |
|----------|-----------|
| NON pivoting a latency arb (Guida 2) | 20s poll vs 2.7s edge window; Python no co-lo; paper mode; edge comprime 12s→2.7s in 2 anni |
| Daily loss limit + halt (CI1) | Guida 2: risk mgmt è l'unica differenza tra +1322% e liqujdatto |
| Market liquidity ≥$50K (CI2) | Guida 2 esplicito; noi solo best-level depth |
| Exit fee su SL/TP (CI3) | P&L realistica; -$0.13/trade nascosto su sport |
| arb_binary DISABLE in paper (CI4) | taker fee = edge in coin-flip; maker non simulabile in paper |
| copy-sport SL assoluto (CI5) | 0W/3L tennis: SL −8% su in-play = rumore; −5 cent più robusto |

## BLOCCO Phase CJ: Latency Arbitrage Step 0 (validazione su feed reali)

Obiettivo: validare edge Guida 2 (Polymarket lagga ~2.7s vs Binance su contratti
crypto 5/15min) SENZA capitale. VPS IONOS Germania gia nota, Step 1 VPS = €0.

### Phase CJ0: Modulo validatore `latency_arb.py` — COMPLETE
- [x] BinanceFeed (REST polling BTC/ETH + momentum 5min, no API key)
- [x] PolymarketContractFeed (gamma markets crypto up/down + scadenza 0.5–15min)
- [x] LatencyArbDetector (expected_p(UP)=0.5+K·delta_5min, edge vs p_yes,
      signal when |edge|>10pt; log jsonl; resolve auto con esito gamma)
- [x] Stats persistente + bucket edge + opt-in POLYMARKET_INSECURE per locale
- [x] py_compile OK, smoke test locale: Binance OK (gamma 403 da Windows via CF)

### Phase CJ1: Deploy + run validazione 5–7 giorni — PENDING
- [ ] Deploy modulo su VPS IONOS (git pull + pip install -r requirements.txt)
- [ ] nohup python -u src/latency_arb.py > logs/latency_arb.log 2>&1 &
- [ ] Monitoring giornaliero per 5–7 giorni via `data/latency_arb_stats.json`
- [ ] Target: 200+ signal resolved con WR virt > 70% (soglia Guida 2)
- [ ] Stop condition: se WR < 60% a 100 signal → tuning K/threshold o pivot WS
- [ ] Logbook in `ARBITRAGE_LATENCY_PLAN.md` compilato dopo ogni step

### Phase CJ1.5: Fix resolver rotto + outcomes per nome — COMPLETE
(risolto e superato da v2 + audit CJ1.6; resolver CLOB path funziona, 738 RESOLVE)

### Phase CJ1.6: Audit risultati v2 — COMPLETE → STOP (Step 5d)
Contesto: run v2 pulito 20–22/07, 738 resolved, WR 43.1%, +$100.50 netto
apparente. Audit `tools/audit_v2.py` su `logs_monday/` dimostra **edge illusorio**.
- [x] Utente push `logs_monday/` (commit faa3b24) + output analyze_signals
- [x] 1810/1810 record `model_version: 2` (zero contaminazione v1)
- [x] Strike: 738/738 `binance_open` — equity API `price-to-beat` 403 ovunque
- [x] Concentrazione: top-10 win = **113.9%** del P&L netto (tutti entry 0.07–0.10)
- [x] Trimmed top-5% win: **-$54.08** (EV -$0.075)
- [x] Bootstrap CI95 EV/trade include 0; trimmed CI include 0
- [x] entry≥0.25 & edge≥0.15: n=80, EV +0.09, CI include 0 → nessun subset robusto
- [x] Bot 8 chiusure: 2 copy tennis SL + 6 harvest Fed SL (bet correlato duplicato)
- [x] **Decisione: Step 5d — NO Step 1 reale. Latency-arb non va a capitale.**
- **Status:** complete

### Phase CJ2: Trading reale $50 — CANCELLED
Precondizione CJ1 fallita (edge paper non sopravvive all'audit). Non aprire
wallet / non piazzare ordini latency-arb.

Vedi `ARBITRAGE_LATENCY_PLAN.md` per Step 2/3 (scaling + diversificazione oracle/MM).

## Phases precedenti (completate, vedi progress.md)
- Phase A-Q: copy base + multi-strategy
- Phase R-BB: config aggressivo + whale/momentum/sniper/theta/contrarian ← MIGLIORATIVO MA FALLITO
- Phase R-Y: deploy aggressivo (causa della perdita attuale)

## Key Questions
1. SL assoluto vs percentuale: quale threshold? Harvest -5 cent ok. Per copy
   in banda 0.30-0.70, -8% = -2.4 a -5.6 cent — ragionevole, tenere %.
2. Entry band 0.08-0.92: troppo stretta? No — a 0.92 max gain 8.7%, a 0.08 max
   gain 1150% (longshot). La banda 0.10-0.90 copre dove le move sono reali.
3. Whale riattivabile? Forse, MA solo con: entry band 0.15-0.85 + SL assoluto
   + validazione 20 trade a 3% sizing. Per ora KILL.
4. Momentum riattivabile? Forse con entry 0.20-0.80 + min_move 10% + SL assoluto.
   Per ora KILL — 0% WR su 4 trade è spietato.

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| KILL whale/momentum/contrarian/sniper/theta | 0-17% WR, -$14.19 totale, non validati |
| SL assoluto per prezzi estremi | SL % triggera su rumore a 0.99/0.02; SL in cent è robusto |
| Entry band 0.08-0.92 | Evita risk/reward invertito; dove SL non è noise-trigger |
| Harvest hold-to-resolution | Early TP +4% lascia juice; payout $1 è l'edge reale |
| Sizing 3% base | WR 24% non giustifica 6-13%; validare prima di scalare |
| Disabilita Kelly + trailing | Troppo aggressivi per WR attuale; riattivare post-validazione |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| Output della revisione diff combinata troncato | 1 | Continuare con diff per file, `git diff --check` e suite completa |
| WR 24%, -$16.89 dopo config aggressivo | 1 | Phase CC-CG: kill perdenti, fix SL, fix entry band, sizing conservativo |
| SL % triggera su rumore a prezzi estremi | 1 | SL assoluto (cent) + entry band filter |
| 9 strategie non validate | 1 | Ridurre a 3 (copy+harvest+arb), validare 30 trade |
| Wallet rotation chiude posizioni copy a "exit" forzato | 1 | Phase CI: reconcile distingue wallet venduto vs wallet rimosso; se rimosso gestisci con SL/TP |
| auto_rescan non resetta prev_holdings → dump bag nuovi wallet | 1 | Phase CI: reset prev_holdings in _reload_monitored_wallets |

## Notes
- Le 4 posizioni whale aperte (Strait of Hormuz No ×3 @0.939-0.998, Ghana No
  @0.999) sono near-certain → risolveranno in profitto. NON chiuderle forzatamente.
- Il problema NON è "poca frequenza" o "sizing troppo basso" come pensato prima.
  È l'opposto: sizing troppo alto + entrate a prezzi estremi + SL rumore-trigger.
- La strategia copy (50% WR, -$1.54) è la migliore. L'edge è reale in banda 0.30-0.70.
  Le altre strategie sono state aggiunte senza backtest e sono la causa della perdita.
- Priorità: PRIMA fermare il sanguinamento (Phase CC), POI fixare la meccanica
  (Phase CD-CE), POI ridurre sizing (Phase CG), POI validare (Phase CH).
