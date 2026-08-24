# Findings & Decisions — Polymarket Copy Bot

## Hardening successivo al NO-GO (2026-08-24)

- Il prossimo campione non può essere promosso se dipende da una sola fonte:
  cap prospettico 20 aperture shadow/wallet, almeno 5 wallet distinti e quota
  massima 20% delle chiusure per wallet.
- Il vincolo sul P&L positivo per wallet resta, ma non sostituisce la nuova
  concentrazione sul numero di trade: entrambi devono passare.
- Il BUY sorgente deve coprire almeno la size paper. `usdcSize` è autorevole;
  `size` è shares e non può essere usato come notional di fallback.
- La frizione certa è un gate autonomo: ask VWAP + entry fee contro bid VWAP -
  exit fee non può produrre perdita immediata oltre il 2,5% della size.
- Il journal v6 rende auditabile il consenso che mancava nel run fallito;
  Position e shadow v3 mantengono gli stessi campi dopo restart.
- Gli HTTP 429 non autorizzano retry aggressivi o snapshot parziali: il client
  applica pacing/cooldown e continua a fallire chiuso. La soglia ufficiale
  `/positions` è 150 richieste/10s, ma il margine serve per throttling e traffico
  condiviso osservati sulla VPS.
- Queste misure riducono errori e selezione concentrata; non creano né
  garantiscono edge. Il rollout resta `OBSERVE` e richiede un nuovo campione.

## Audit prospettico 14 giorni - integrità bundle (2026-08-24)

- Bundle: `exports/polymarket-validation-20260824T122121Z.tar.gz`, 1.060.980
  byte, SHA-256 `9D44C58D2018450B8CFE71D742A5746F62EDD03B1F4660404D125743CE5CF72F`.
- Archivio tar valido, estratto in una directory temporanea esterna al repo.
- Commit VPS `eebf50e3b2396bf19f367d5fc6361a348e82c4df`, branch `main` allineato a
  `origin/main`; bot e dashboard attivi, latency-arb fermo.
- Ambiente del processo bot: `POLYMARKET_EXECUTION_MODE=observe`.
- Presenti ledger/journal candidate e shadow v2, curva equity MTM, manifest
  wallet, registro quarantene, runtime, scan, API snapshot e log completi.
- I tre file di errore curl sono vuoti; snapshot API acquisiti correttamente.
- Run coerente `run-20260810T174414-f6d13226`, OBSERVE, portfolio paper intatto
  a $300 con zero aperture/chiusure; runtime al ciclo 56.079, fase idle e stato
  aggiornato circa 9 secondi prima dell'export.
- Shadow v2 già terminato dal breaker: 4 chiuse, 0W/4L, P&L netto
  `-$4,402640`, equity/cash `$295,597360`, drawdown massimo 1,468% e halt
  persistente `copy: 3 consecutive shadow losses`.
- Il manifest è congelato su 6 wallet e domini `geopolitics/politics`; il
  vecchio wallet fallito `0x970367...` è escluso. Il nuovo wallet
  `0xb1ca909...` ha prodotto quattro perdite ed è ora in quarantena cross-run.
- Candidate journal: 1.201 record v5, tutti dello stesso run, 67 eligible e
  1.134 rejected. Shadow journal: 4 open, 4 close e 63 reject portfolio/safety.
- Durata run→export 13,776 giorni; copertura candidati 13,356 giorni. Il gate
  dei 14 giorni non è quindi formalmente raggiunto, anche se il run è già
  terminato economicamente dal breaker e non può raccogliere nuovi ingressi.
- I 67 eligible sono tutti `politics`, tutti dal solo wallet `0xb1ca909...`,
  su 48 eventi/56 condition/64 asset. Tutti hanno BUY sorgente, tx hash,
  evento, book e fee schedule completi; latenza mediana 10,44s, p95 21,46s.
- Copertura shadow perfetta: i 67 signal_id eligible hanno esattamente una
  decisione shadow (4 opened, 63 rejected), senza mancanti o extra.
- Le quattro perdite sono tutte stop-loss su quattro eventi politici distinti:
  `-$0,4851`, `-$0,9145`, `-$1,6654`, `-$1,3377`.
- Decomposizione: movimento lordo raw ask→exit `-$3,7996`; fee ingresso
  `$0,2955` e uscita `$0,3075`; totale riconciliato `-$4,4026`. Anche senza
  fee il segnale è nettamente negativo.
- La curva shadow conserva gli ultimi 10.000 punti: gap massimo 26,62s e zero
  gap >60s. Il buffer è interamente post-halt, quindi la continuità pre-halt va
  verificata dal log/runtime e dai lifecycle journal, non dal solo ring buffer.
- Log bot: 56.079 snapshot e un solo avvio, zero traceback. Sono presenti 295
  cicli con almeno un wallet non leggibile (0,526% dei cicli), dominati da 696
  risposte HTTP 429 su `/positions`, più 6 timeout, 4 HTTP 500 e 2 disconnect.
  In 55 cicli tutti i 6 wallet erano temporaneamente non leggibili. La baseline
  è stata sempre preservata: non risultano falsi ingressi/uscite, ma il rate
  limiting può far perdere segnali brevi e va corretto prima di un altro run.
- Dashboard: zero traceback e zero risposte 500; due richieste ostili di SQL
  injection hanno ottenuto 404. L'esposizione pubblica resta un rischio noto.
- Dei 67 eligible, 5 arrivano prima del terzo stop (4 opened e 1 rifiutato per
  max posizioni); gli altri 62 vengono correttamente respinti dall'halt.
- I cinque wallet diversi da `0xb1ca909...` producono complessivamente 37
  candidati e zero eligible; il cohort è quindi totalmente dipendente da un
  solo wallet, ora fallito e quarantinato.
- Le quattro perdite nette equivalgono a -9,70%, -18,29%, -33,31% e -26,75%
  della size. Due stop sono avvenuti dopo movimenti a gap molto oltre la soglia:
  lo stop limita il rischio ma non garantisce il prezzo d'uscita.
- Frizione immediata sui 67 eligible, prima ancora della fee di uscita: entry
  economica meno bid mediana 1,99 cent; mark immediato medio `-$0,2233` e
  mediano `-$0,2121` per size $5 (circa 4,2-4,5%). Un segnale COPY deve quindi
  superare un hurdle elevato solo per pareggiare.
- Il valutatore esportato restituisce EV `-$1,10066/trade`, limite inferiore
  bootstrap CI95 a cluster evento `-$1,50155`, drawdown 1,468% e NO-GO.
  Passano solo `intended_domains_frozen` e drawdown <=3%; falliscono gli altri
  8 gate, inclusi P&L, CI, 100 chiusure, 30 eventi, 14 giorni e 30 trade/dominio.
- La concentrazione positiva per evento/wallet è impostata conservativamente
  al 100% quando non esiste alcun profitto positivo; il relativo fallimento non
  è interpretabile come concentrazione di guadagni, ma non cambia il verdetto.
- Il candidate journal v5 non persiste `num_holders`/consenso. Il reconcile lo
  calcola e lo passa al valutatore, ma viene scritto solo nel trade log paper;
  poiché OBSERVE non apre trade paper, il consenso dei 67 eligible non è
  auditabile retrospettivamente. Va aggiunto al journal prima del prossimo run.
- Tredici eligible hanno source notional < $5; due delle quattro aperture
  copiavano BUY da $4,40 e $1,70 con size fissa $5 e sono le due perdite più
  grandi. Tuttavia anche le due aperture con source notional $118/$314 sono
  perdenti: un filtro $5 avrebbe limitato danno, non dimostrato edge.

### Verdetto Phase CV

- **NO-GO** a `paper_validation` e a qualunque capitale reale. Il run fallisce
  8 dei 10 gate formali e il segno economico è negativo anche prima delle fee.
- Il breaker e la quarantena hanno funzionato: hanno impedito 62 ulteriori
  aperture e il wallet fallito non sarà selezionabile nel prossimo scan.
- Aspettare non recupera questo run: lo shadow è halted, ha solo 4 chiusure e
  nessuna nuova apertura; le 67 eligibility non sono lifecycle/P&L.
- Un nuovo scan identico non basta: selezionare ripetutamente vincitori storici
  e scartare prospetticamente i perdenti rischia survivor bias. Prima del nuovo
  campione servono pacing/backoff sui 429, consenso persistito nel journal,
  guardrail sul source notional e un contratto che impedisca a un solo wallet di
  costituire il 100% del campione promozionale.

## Revisione finale del diff

- Le modifiche sono coerenti con il contenimento richiesto: il run corrente non cambia wallet in modo adattivo, mentre i run successivi escludono quelli in quarantena persistente.
- Lo shadow v2 introduce vincoli comparabili al paper (`$300`, size `$5`, massimo 2 posizioni, una per evento) e breaker basati sull'equity mark-to-market.
- API e dashboard espongono ora anche conteggio e top reason dei candidati shadow rifiutati; il journal completo resta la fonte append-only.
- Suite finale: 67 test superati; nessun errore di compilazione Python o di sintassi JavaScript/Bash.
- Il drawdown usato per la promozione conserva il peggiore tra quello ricostruito dai trade chiusi e quello MTM persistito.
- I segnali rifiutati dai vincoli portfolio non vengono riaperti in ritardo quando si libera uno slot: la decisione prospettica è unica e persiste dopo restart.
- La classificazione dominio usa i metadati mercato arricchiti dal fetcher prima dei gate; il manifest v1 con domini vuoti fallisce chiuso per il nuovo run.

## Phase CU - contratti da preservare

- Compatibilita obbligatoria con ledger state v3, candidate journal v5,
  `entry_best_ask` gia persistito e fee schedule per-market fail-closed.
- Il run corrente non va mutato: i nuovi campi devono avere migrazioni legacy e
  diventare autorevoli solo dal prossimo `new-run scan`.
- Latency-arb, HARVEST e tutte le strategie non-COPY restano disabilitate; il
  lavoro CU non deve riaprire accidentalmente percorsi di esecuzione.
- I file utente non tracciati e tutti gli export sono evidenza: nessuna pulizia,
  riscrittura o inclusione automatica nei commit.
- Punti di estensione principali: `main.py` crea il manifest wallet;
  `simulator.py` gestisce lifecycle/safety/shadow; `validation.py` calcola gate;
  `run_state.py` e `start_all.sh` archiviano lo stato; dashboard/API leggono il
  riepilogo del simulatore.
- Lo shadow attuale e deliberatamente unconstrained: apre ogni eligible senza
  cash/cap/evento. CU lo sostituira con un portfolio shadow separato ma soggetto
  agli stessi limiti del paper, mantenendo journal append-only e dedup restart.
- `monitored_wallets.json` contiene gia metadata completi copiati dallo scan ed
  e il luogo naturale per congelare domini per wallet e domini del run.
- `Position.entry_best_ask` separa gia raw ask da `entry_price` economico: il
  fix stop non richiede schema Position nuovo, solo fallback conservativo per
  ledger legacy privi del campo.
- La safety paper e separata e non va riusata direttamente dallo shadow: lo
  shadow necessita contatori/cash/equity propri per non alterare il portfolio.
- `shadow_state` v1 salva solo posizioni/chiuse: per un replay constrained v2
  servono cash iniziale/corrente, peak, max drawdown, start equity, halt,
  loss streak, blocked conditions e una curva equity MTM persistente.
- `_open_shadow_candidate` oggi marca il signal come visto solo quando apre. Nel
  nuovo contratto ogni eligible deve produrre una decisione shadow (`opened` o
  `rejected` con portfolio gate) e il signal deve restare deduplicato anche se
  rifiutato, per evitare aperture tardive dopo liberazione di uno slot.
- `evaluate_copy_run` calcola drawdown dalla sola sequenza P&L realizzata; per lo
  shadow constrained deve accettare il max drawdown MTM persistito dal ledger,
  mantenendo il valore legacy solo come fallback.
- Lo scanner salva oggi una sola `category` per wallet e scarta le ulteriori
  specializzazioni durante il dedup round-robin. Va persistito `categories`
  completo; il manifest deve trasformarlo in `allowed_domains` immutabile.
- La quarantena cross-run non puo vivere nei file cancellati da `new-run`:
  usare un registro prospettico separato, copiarlo negli archivi come evidenza
  ma preservarlo durante clear/new-run. Il run corrente non cambia cohort.
- Soglia iniziale non adattiva: tre perdite shadow consecutive dello stesso
  wallet lo rendono non selezionabile dai run successivi; il record conserva
  run, timestamp e motivazione, senza rimuoverlo dal manifest corrente.
- Dashboard shadow oggi mostra soltanto cohort/P&L/EV/CI/gate: aggiungere cash,
  equity, deployed, max 2, drawdown MTM, halt e intended domains, mantenendo
  `real_money_authorized=false`.
- Il test legacy `shadow_tracks_every_pretrade_pass` codifica esplicitamente
  tre shadow open oltre il cap paper: va sostituito con il nuovo contratto
  2 open + terzo `rejected/max_open_positions`, senza alterare il paper ledger.
- Migrazione shadow v1: ricostruire cash da initial - size di tutte le aperture
  + proventi netti delle chiuse, ma impostare un halt permanente del run legacy;
  le posizioni ancora aperte continuano a essere gestite fino alla chiusura.
- Ogni mark shadow deve aggiornare peak/max drawdown e circuit breaker prima del
  salvataggio; open/close registrano inoltre punti della curva equity separata.
- La UI passa oggi a `updateRiskDashboard` soltanto il summary paper; la
  quarantena cross-run e nel payload top-level. Va passata esplicitamente alla
  funzione per rendere visibile conteggio/errore del registro.

## Audit shadow 24h - integrita iniziale (2026-08-10)

- Bundle `exports/polymarket-shadow-20260810T123016Z.tar.gz`, 291.877 byte,
  SHA-256 `8780A12903F14EA2C9521C6294840E26A6768B1D08E3D49643FCE311741905A6`.
- Archivio tar valido e completo: commit/git status, config, snapshot API,
  ledger/journal candidate+shadow, manifest wallet, runtime/equity e tre log.
- Estratto in directory temporanea esterna al repository per audit read-only.
- Run coerente `run-20260809T150817-a2d0f4b3`, commit `f5660fa`, OBSERVE,
  cash $300, zero paper open/closed; runtime ciclo 3403, idle, stato fresco.
- Continuita perfetta: 3.403 punti equity, gap massimo 27,58s, zero gap >60s;
  zero traceback, FEED, HTTP 400/429 o errori nei log.
- Journal candidati: 106/106 JSON v5 validi e unici, 38 passed-pretrade e 68
  rejected. Shadow: 38 open lifecycle, 32 close e 6 ancora aperte; copertura
  1:1, zero segnali mancanti/extra/duplicati.
- Risultato shadow chiuso: 3W/29L (WR 9,375%), realized -$16,0056,
  EV -$0,5002/trade; sei open a -$1,332, totale mark-to-market -$17,3376.
- Decomposizione 32 chiusi: raw ask->exit -$8,4830 e fee entrata+uscita
  $7,5226; riconciliazione al floating point. Il segnale perde anche pre-fee.
- Close reason: exit 0W/13L -$5,238; stop 0W/16L -$15,291; take-profit
  3W/0L +$4,524. Durata mediana 30,4 minuti.
- Concentrazione estrema: 37/38 eligible e tutti i 32 chiusi provengono da
  `0x970367...` (AnonymousUsername); 37 sport e un solo geopolitics.
- Correlazione non spiega il segno: prendendo solo il primo trade dei 16 eventi
  chiusi si ottiene 0W/16L e -$10,454; bootstrap event-cluster CI95 EV
  completamente negativo `[-$0,728, -$0,279]`.
- Anche la controfattuale con max 2 e una posizione/evento resta negativa:
  5 chiuse, 1W/4L, -$1,896. Con quarantena a tre loss si sarebbe fermata a
  0W/3L e -$2,203, quindi i guardrail paper avrebbero limitato il danno.
- Nessun filtro descrittivo sul notional sorgente salva il cohort: source >=$5
  produce 2W/24L e -$15,243; source >=$100 produce 2W/11L e -$5,014.
- Lo shadow unconstrained ha raggiunto 12 posizioni simultanee ($60 virtuali):
  il suo drawdown non e direttamente confrontabile con il paper max 2, ma EV,
  WR e CI a cluster restano nettamente negativi anche dopo decorrelazione.
- Otto dei 16 stop sport sono avvenuti con discesa raw ask->bid inferiore a
  5 cent (tipicamente 4 cent). Il codice va verificato: potrebbe confrontare il
  bid raw con `entry_price` fee-inclusive invece che con `entry_best_ask` raw.
- Conferma nel codice: `_copy_sl_tp_decision` calcola per sport
  `delta = raw_bid - pos.entry_price`; `entry_price` include fee, mentre il
  contratto/documentazione dichiara uno stop su movimento assoluto di mercato.
  Il campo corretto `entry_best_ask` e gia persistito ma non viene usato.
- I test non coprono questa interazione fee+stop: le fixture senza fee fanno
  coincidere raw ask ed entry economica, nascondendo la regressione.
- Il wallet dominante `0x970367...` era gia 0W/2L (-$1,105) nel paper precedente
  ed e stato riselezionato dal nuovo scan per metriche storiche in-sample. Manca
  un registro prospettico cross-run che impedisca di riproporre wallet gia
  falliti; non va pero rimosso adattivamente dal run shadow corrente.
- Il gate domini e adattivo: `get_shadow_summary` deriva `intended_domains` dalle
  categorie chiuse, invece di congelarle a inizio run. Qui il manifest seleziona
  specialisti politics/geopolitics, ma 37/38 segnali validi sono sport e il gate
  mostra comunque `intended_domains=['sport']` con controllo dominio true.
- Durata effettiva al bundle: 21,37 ore (non 24); journal candidati copre 19,25
  ore. Il dato e comunque sufficiente a trovare regressioni tecniche, non a
  soddisfare il gate temporale di 14 giorni.
- I 38 passed-pretrade sono completi 38/38: source BUY/tx/timestamp, evento,
  asset, bid/ask, VWAP, livelli, scadenza e fee schedule; zero book crossed,
  zero fill incompleto e latenza 1,54-29,49s (mediana 16,40s).
- Costi immediati elevati ma coerenti: entry economica meno bid mediano 2,25c,
  p95 4,22c; fee ingresso $4,434 sui 38 segnali ($0,117 medio).
- Due soli wallet producono tutti i candidati: `0x970367...` genera 58 record e
  37 eligible; `0xb1ca...` genera 48 record e uno eligible. Gli altri sei wallet
  congelati non producono alcun delta journalizzato nel periodo.
- Ripetizioni/correlazione: 38 segnali su 32 asset, 28 condition e 20 eventi;
  alcuni eventi includono moneyline, first-inning e anche lati opposti nel tempo.
- I log confermano un solo avvio, baseline corretta, ciclo continuo e nessun
  errore dashboard/backend. Lo stato tecnico del collector e affidabile.

## Phase CS - vincoli ripristinati

- Il piano storico contiene ancora note obsolete come "COPY edge reale"; sono
  superseded dagli audit CR/CP e non devono guidare nuove aperture.
- Le decisioni vigenti sono: latency-arb, HARVEST e strategie alternative
  restano disabilitate; COPY non passa al reale senza campione indipendente.
- Il run paper fallito non va riattivato. Lo sviluppo CS deve produrre evidenza
  shadow completa senza mutare cash, portfolio, cooldown o safety state.
- Le vecchie fasi aggressive puntavano esplicitamente a raddoppiare in 10-14
  giorni e hanno prodotto drawdown/perdite; sizing, wallet rotation e target di
  rendimento di quelle fasi sono evidenza negativa, non requisiti da recuperare.
- Il codice ha gia avuto simulazioni ottimistiche e claim invalidati da fee,
  fill e selezione in-sample. Il nuovo shadow ledger deve riusare solo ask/bid,
  profondita e fee per-market gia validati, senza midpoint o payout inventati.
- Anche le stime storiche di EV/WR atteso e le strategie definite
  "risk-free-ish" sono state successivamente invalidate dai run e non sono
  assunzioni ammissibili. Ogni metrica CS deve essere derivata dal nuovo run.
- Il simulatore ha gia un punto unico `evaluate_copy_candidate` e mark/exit fee
  aware riusabili. Lo shadow state puo quindi essere aggiunto senza duplicare i
  filtri pre-trade e senza entrare nel Portfolio reale/paper.
- `new-run` archivia una lista esplicita di file: ogni nuovo ledger/journal
  shadow dovra essere aggiunto a archive/reset per evitare contaminazione run.
- Design scelto: ogni `signal_id` che supera i controlli pre-trade apre una
  posizione shadow da $5, indipendente da cash, cap, evento e quarantena paper.
  Segnali correlati restano registrati ma le statistiche conserveranno eventi
  distinti e concentrazione, evitando di contarli come prove indipendenti.
- Le posizioni shadow useranno lo stesso entry ask VWAP+fee e lo stesso exit bid
  netto fee del paper. Saranno chiuse su vendita wallet, SL/TP o risoluzione,
  senza chiamare `_record_close_risk` e senza mutare portfolio/cooldown/safety.
- Persistenza prevista: `shadow_state.json` atomico e `shadow_journal.jsonl`
  append-only, entrambi legati al `run_id` e inclusi in new-run/reset/export.
- `main.run_mirror_loop` ha gia aggregate, delta, successful/failed wallet e un
  unico `reconcile` per ciclo: la gestione shadow puo vivere in `reconcile`
  senza un secondo snapshot wallet o modifiche al polling.
- Esistono due percorsi di archive/reset (`start_all.sh` e `tools/run_state.py`):
  entrambi devono includere i file shadow e i test devono coprirli.
- Documentazione ufficiale corrente: `POST /books` restituisce order book per
  piu token in un'unica richiesta (fino a 500 token); rate limit `/books` 500
  richieste/10s. Il fetcher locale ha solo `GET /book`: aggiungere `get_books`
  permette mark shadow eseguibili senza moltiplicare le round-trip HTTP.
- Il payload batch usa `asset_id`, bids/asks completi e la stessa struttura del
  book singolo; il parser va centralizzato per evitare divergenze best bid/ask.
- Ricerca statica del percorso di esecuzione: nel repository non risultano
  chiavi private, creazione/invio ordini o modalita live. I soli mode accettati
  restano `observe` e `paper_validation`; gli evaluator e la dashboard espongono
  sempre `real_money_authorized: false`.
- Un fallimento completo del batch CLOB deve preservare gli ultimi mark e
  attendere il ciclo seguente; il fallback per-position creerebbe fan-out e
  potrebbe confondere un outage con una risoluzione.
- Audit del diff: le risoluzioni gia segnalate `redeemable` dall'aggregate
  devono essere processate prima dell'early-return su outage CLOB; altrimenti
  un mercato risolto potrebbe restare shadow-open fino al ritorno del book.
- Il bootstrap per singolo trade puo essere troppo ottimistico quando piu
  segnali appartengono allo stesso evento. Prima del rollout va valutato un
  bootstrap a cluster evento, preservando l'EV per trade ma campionando eventi.

## Audit paper 48h - input iniziale 2026-08-09

- Dashboard: run `run-20260807T141814-a65fb998`, PAPER_VALIDATION, HALTED per
  `copy: 3 consecutive losses`, equity/cash $299.11, zero posizioni aperte.
- Risultato grezzo: 4 chiuse, 1W/3L, P&L netto -$0.89; sequenza delle prime tre
  chiusure negativa, mentre il successivo win era gia aperto prima dell'halt.
- Journal dashboard: 265 decisioni utili = 4 opened + 261 rejected; `eligible=0`
  non significa zero segnali validi, perche le aperture sono contate a parte.
- Dopo la quarantena almeno 27 candidati sono stati rifiutati dal safety gate;
  il run non puo piu raccogliere aperture senza riattivazione manuale.
- Export da auditare: `exports/polymarket-paper-20260809T142445Z.tar.gz`.
- Archivio leggibile, 508.167 byte, struttura completa: ledger + backup,
  journal, trade/equity/safety/wallet/runtime, config e log bot/dashboard.
- Timestamp interni arrivano fino al 09/08 16:24; safety/trade log si fermano
  correttamente all'ultima chiusura del 08/08 22:37.
- Integrita: SHA-256 `C8066A85776254CEBE5A2AEFF579F6E8A2C5AB80921DE297AB4E313D8A2373A3`;
  commit VPS `e006529`, branch main allineato, bot/dashboard attivi e arb fermo.
- Ledger v3 riconciliato: cash/equity $299.105170, 4 chiuse, P&L matematico
  -$0.894830, zero aperte. Journal 269/269 JSON validi, tutti v4 e stesso run.
- Journal decisioni: 4 opened, 4 closed e 261 rejected; 265 signal_id distinti.
  Le quattro ripetizioni sono le coppie lifecycle open/close, non doppi segnali.
- Salute: ciclo 7.382, runtime fresco, zero traceback/errore/HTTP 400/429;
  soltanto due timeout feed gestiti.
- Sequenza: Rio -$0.789, Minnesota -$0.506, Houston -$0.599, Gemini +$0.999.
  I primi tre stop hanno attivato la quarantena; Gemini era gia aperto e ha
  continuato a essere gestito fino al take-profit, come previsto.
- Decomposizione totale: i quattro prezzi raw ask->exit producono appena
  +$0.045; fee ingresso $0.465 + fee uscita $0.475 trasformano il risultato in
  -$0.895. Dal prezzo sorgente, il movimento lordo vale +$0.500 ma $0.455 di
  svantaggio source->nostro ask e $0.940 di fee consumano tutto l'edge.
- Costo round-trip fee medio $0.235 per trade da $5 (4.70% della size), prima
  dello spread/ritardo rispetto al wallet. Il campione non prova edge negativo,
  ma dimostra che l'edge raw osservato e insufficiente a coprire i costi taker.
- EV netto osservato -$0.224/trade. Con soli quattro trade il Wilson CI95 del
  win rate e circa 4.6%-69.9% e il bootstrap esatto della media circa
  [-$0.718, +$0.599]: il segno dell'edge resta statisticamente indeterminato e
  il limite inferiore richiesto per la promozione e nettamente sotto zero.
- Il valutatore di promozione del repository, eseguito sul ledger esportato,
  restituisce `eligible_for_paper_promotion=false` e `real_money_authorized=false`.
  Falliscono 8 gate su 9: 4/100 trade, 4/30 eventi, 2.00/14 giorni, P&L e CI
  negativi, concentrazione positiva 100% su un evento/wallet e domini 2+2/30.
  Passa soltanto il drawdown <=3% (misurato dal valutatore 0.63%).
- Root cause meccanica sport: Minnesota source/bid 0.61, ask 0.62, entry con fee
  0.63178, stop raw 0.58; Houston 0.52/0.53/0.542455, stop raw 0.49. Il bid si
  muove soltanto -3 cent, ma la policy confronta il bid con l'entry fee-inclusive
  e vede circa -5.2 cent, attivando lo SL assoluto configurato a -5 cent.
- Wallet paper: `0xc8ab...` 1W/1L, +$0.210; `0x9703...` 0W/2L, -$1.105.
  Houston nasce da un BUY sorgente di soli $1.08, poi copiato con size $5.
- Equity continua: 7.419 punti, gap max 33.3s, zero gap >60s; massimo $300.28,
  minimo intraday $297.86, finale $299.11. Nessun daily/run loss breaker.
- Prima dell'halt: 97 candidati, 4 opened, 21 passati ai controlli ma bloccati
  da max positions. Dopo: 168 candidati, di cui 27 passati ai controlli ma
  bloccati dalla quarantena; la pipeline continua correttamente a osservare.
- Correzione importante: i 21+27 portfolio-gated sono valutati completamente
  in memoria, ma `_journal` viene chiamato senza `evaluation`; il record perde
  VWAP, entry, fill e costi. Non sono quindi auditabili come eligible dal file.
  Va corretto il journal prima del prossimo run.
- I candidati arrivati al portfolio gate/apertura sono 52 su 35 eventi e 42
  condition: 39 sport, 10 other, 3 politics. Tutti provengono da soli due
  wallet (`0x9703...` 38, `0xc8ab...` 14); 21 sono bloccati dal limite di due
  posizioni e 27 dalla quarantena. Fee schedule: 47 rate 0.05, 5 rate 0.04.
- La size del trade sorgente e presente in tutti i 52, ma varia molto: 37/52
  sono almeno $5 e 28/52 almeno $10. Il trade Houston aperto nasce da $1.08:
  un eventuale filtro sul notional sorgente va validato prospetticamente e non
  scelto ora per eliminare retroattivamente una perdita.
- Il manifest congelato eredita metriche storiche in-sample molto forti
  (`0x9703...` WR 100% su 19 decise), ma nel paper quel wallet fa 0W/2L e
  -$1.105. E un'ulteriore conferma che il profiler storico non prova edge COPY.
- Pagina ufficiale Polymarket Houston-San Diego: risultato finale Astros 6,
  Padres 3. L'outcome copiato Padres era perdente a risoluzione; lo stop ha
  limitato una perdita che altrimenti sarebbe arrivata vicino alla size intera.
- Pagina ufficiale Polymarket Minnesota-Milwaukee: risultato finale Twins 8,
  Brewers 6. Anche l'outcome copiato Brewers era perdente a risoluzione; lo
  stop ha limitato una perdita che altrimenti sarebbe arrivata alla size intera.
- Tenere fino a settlement i due sport perdenti avrebbe prodotto circa -$10
  invece di -$1.105: gli stop hanno risparmiato circa $8.90. Il difetto del
  riferimento fee-inclusive va corretto semanticamente, ma non giustifica
  allargare o rimuovere gli stop sulla base di questo campione.
- La dashboard calcola `eligibility_rate` usando solo la decisione `eligible`;
  in paper i candidati validi diventano `opened`, quindi mostra 0 eligible pur
  avendo quattro aperture. Serve un conteggio separato `passed_pretrade` =
  eligible + opened, senza presentarlo come profitto potenziale.
- La pagina Rio non ha restituito dati utilizzabili e la ricerca Gemini trovava
  un mercato correlato ma non la stessa condition/scadenza. Non attribuire a
  questi due trade un esito finale non verificato: valgono le uscite registrate.

## Follow-up OBSERVE 2026-08-06 — copertura feed

- Run v3 sano: 0 traceback, 28 candidati, 2 eligible, entrambi con sorgente
  valida; 26 reject, inclusi 13 `source_trade_unavailable` e 2 stale.
- Quattro episodi `[FEED]`: burst iniziale con 9 timeout consecutivi, poi 2,
  4 e un timeout isolato. La baseline è rimasta preservata e i cicli sono
  ripresi, quindi il fix CN ha impedito falsi delta/exit.
- La documentazione ufficiale Data API conferma che `/positions` accetta
  `limit` fino a 500 e `offset` fino a 10000. Il bot usa oggi una sola pagina
  da 200: per wallet grandi può omettere posizioni e produrre variazioni
  artificiali al bordo della pagina.
- La documentazione ufficiale aggiornata conferma che `/activity` ha pagine
  stabili, `limit<=500`, `offset<=5000`, filtri server-side `type=TRADE` e
  `side=BUY`. Portare il singolo lookup da 100 a 500 non richiede pagine extra
  e riduce i falsi `not_found` per wallet ad alta frequenza.
- `/trades` consente `user`, `side`, finestre temporali e fino a 10000 record,
  ma non filtra direttamente per asset. Non viene introdotto come fallback in
  questa fase: una pagina activity 500 è più mirata e conserva un solo endpoint.
- Il lookup BUY usa oggi una sola pagina `/activity` da 100. Il 46,4% dei nuovi
  delta del run non trova la sorgente; questi candidati sono bloccati in modo
  sicuro, ma la copertura del campione è insufficiente.
- Decisione: aggiungere paginazione positions, portare activity a 500 e
  mantenere la semantica fail-closed. Non promuovere ancora a paper.
- Il circuit breaker conta solo errori transitori consecutivi (timeout,
  connection error, HTTP 408/425/429/5xx); un successo azzera il contatore.
  Dopo tre fallimenti rinvia i wallet restanti come `unknown`, preservandone
  la baseline e limitando la durata di un ciclo durante un outage.

## Implementazione Phase CN — decisioni tecniche (2026-08-05)

- Compatibilità pubblica preservata: `get_positions`, `get_recent_buy` e
  `snapshot_wallets` restano wrapper legacy; i nuovi call-site usano risultati
  strutturati con stato `ok/not_found/error`.
- Un wallet al primo fetch riuscito viene baselinato senza generare delta; un
  errore successivo non cancella la sua baseline. Solo uno snapshot riuscito
  può provare una vendita e autorizzare una chiusura `exit`.
- La valutazione COPY richiederà un BUY sorgente verificato, recente e con
  transaction hash. Il prezzo del BUY sarà la base del filtro drift.
- Top book, profondità e VWAP verranno derivati dallo stesso payload CLOB per
  eliminare incoerenze e ridurre le richieste da tre a una per candidato.
- Il journal passa a v3 aggiungendo stato/prezzo/size sorgente, osservazione del
  book, scadenza e livelli consumati; i lettori restano compatibili con v1/v2.
- La protezione della dashboard resta deliberatamente invariata in questa fase.

## Audit OBSERVE 2026-08-03 — integrità iniziale

- Bundle: `polymarket-observe-20260803T152827Z`, 9 file, circa 13.7 MB
  non compressi; journal 4.84 MB e bot log 6.41 MB.
- Commit VPS corretto: `ec7071087ddf1b89ffc1cad2f3de88ddc754be66`,
  branch `main`; bot e dashboard attivi, latency-arb fermo.
- Run corrente: `run-20260724T081332-57a5bf9c`, modalità `observe`, wallet
  congelati dal 24/07, snapshot raccolto il 03/08 alle 15:28 UTC: circa 10,3
  giorni di osservazione, non ancora 14 giorni pieni.
- Salute al prelievo: ciclo 40.512, fase `idle`, ledger/runtime aggiornati 14
  secondi prima del bundle, nessun errore runtime.
- Portfolio invariato: capitale/cash $300, zero posizioni aperte e chiuse, come
  richiesto da OBSERVE. `baseline_done=true`; la lunga lista baseline non è
  attività del bot ma stock iniziale dei wallet.
- Manifest coerente e congelato: 12 wallet effettivamente monitorati. Mancano
  dal bundle `wallet_quality.json`, `safety_state.json` e `trades_log.json`,
  plausibilmente perché non creati in questo run senza esecuzioni/halt.

### Journal e filtri

- 4.484 righe JSON valide, tutte journal v2, stesso run e strategia COPY;
  4.484 `signal_id` unici: dedup perfetto nel file, zero righe duplicate.
- 4.436 rejected (98,93%) e 48 eligible (1,07%). Motivi principali:
  `expiry_too_near` 3.380, banda prezzo 567, drift 250, book non eseguibile
  162, scadenza lontana 63, top-depth 12, spread 2.
- Il filtro scadenza elimina tutta l'attività crypto 5m: 3.740 segnali, quasi
  tutti prodotti da tugator. È comportamento intenzionale ma gonfia del 83,4%
  il volume grezzo senza generare opportunità COPY utilizzabili.
- I 48 eligible coprono 38 asset, 36 condition, 29 eventi, 5 wallet e categorie:
  geopolitics 14, other 14, sport 9, macro 7, politics 4; crypto/weather zero.
- Concentrazione eligible elevata: ArmageddonRewardsBilly 33/48 (68,75%),
  AnonymousUsername 9, denizz 4, Logan 1, TwoEyes 1. Sette wallet congelati non
  hanno prodotto alcun candidato eligible.
- Prezzi eligible ragionevoli: ask 0,31–0,70, mediana 0,545; spread mediano 1
  cent, p90 2,3 cent, massimo 3,6 cent. La size $5 è interamente eseguibile al
  top level in tutti i 48 casi, quindi VWAP coincide col best price; profondità
  minima bid/ask rispettivamente 34,37/57,90.
- Fee positive soprattutto sullo sport: fee fraction media 0,24%, p95 1,40%.
- Latenza eligible: n=37 con trade sorgente, mediana 15,84s, p90 25,25s,
  massimo 57,95s. Undici eligible non hanno timestamp/tx sorgente.
- Globalmente 330/4.484 record non hanno `transaction_hash/source_trade_at`;
  35 rejected hanno latenze >120s, incluso un outlier ~95 giorni. Nessun
  eligible supera 60s, ma va chiarita la causa dei fallback/stale source trade.
- Frequenza giornaliera precipita da 349–622 candidati/giorno (24–31 luglio) a
  44 il 1 agosto, 58 il 2 e 18 fino alle 14:24 UTC del 3 agosto: verificare nei
  log se è attività wallet reale o degrado del feed.

### Salute log e concentrazione economica

- `bot.log` contiene esattamente 40.512 snapshot, uno per ciclo, un solo avvio,
  nessun traceback, nessun HTTP 400 e nessun errore di ciclo. Solo 25 errori
  isolati: 21 timeout testuali (18 timeout positions effettivi) e 4 HTTP 429
  sul lookup BUY; incidenza trascurabile rispetto ai cicli.
- Il crollo dei candidati dall'1 agosto coincide con la scomparsa dell'attività
  crypto di tugator (3.739 segnali fino al 31/07, uno il 01/08, zero dopo), non
  con un arresto del bot: il 03/08 continua a vedere ~931 asset ogni 22 secondi.
- I 330 source mancanti non derivano solo dai quattro 429: sono soprattutto
  nuovi asset per cui `/activity?limit=100` non trova un BUY corrispondente.
  Distribuzione: ChetterHummin 130, Armageddon 125, Logan 48, altri 27.
- I 35 source stale >60s sono tutti rejected; gli outlier enormi sono nuovi
  snapshot di posizioni acquistate giorni/mesi prima, non segnali eligible.
- I 48 eligible non equivalgono a 48 aperture paper: 36 condition e 29 eventi.
  Nove condition ricompaiono più volte e 11 eventi hanno più segnali.
- Esistono segnali contraddittori sullo stesso evento/condition (es. ceasefire
  Yes e No; Fed September +25bps Yes poi No, insieme a no-change Yes). Il cap
  globale una posizione/evento impedirebbe esposizioni simultanee, ma l'ordine
  di arrivo determinerebbe quale tesi viene copiata.
- Caso più concentrato: evento “largest company end of August” 6 eligible, di
  cui Apple No quattro volte e NVIDIA Yes due volte, tutti dallo stesso wallet.
  Questo conferma che il conteggio raw non misura opportunità indipendenti.
- Il helper locale `get_market()` espone `closed` ma scarta `outcomePrices` e
  altri campi di risoluzione Gamma; l'audit retrospettivo deve quindi leggere la
  risposta Gamma raw e mappare outcome/token senza alterare il bot.
- La documentazione ufficiale conferma che Gamma è pubblico e che `outcomes` e
  `outcomePrices` sono array 1:1. Il primo accesso locale è stato bloccato da un
  certificato proxy con hostname mismatch; il tentativo ha prodotto 0/36
  risposte, quindi non è stato usato per alcuna metrica o verdetto.
- Disabilitare la verifica TLS non risolve l'accesso locale: Gamma restituisce
  HTTP 403 per tutte le condition. Di conseguenza il bundle, da solo, non
  consente di misurare esiti/mark successivi; contiene solo lo snapshot
  pre-trade. La decisione non deve usare i P&L null stampati dal tentativo.

### Difetti bloccanti scoperti dal campione

- Confermata nel codice la causa del flapping: `get_positions()` trasforma ogni
  errore HTTP/timeout in `[]`; `snapshot_wallets()` non comunica quali wallet
  sono falliti; `main` sostituisce comunque `prev_holdings` con lo snapshot
  incompleto. Al recupero, tutte le vecchie posizioni del wallet diventano
  falsi delta “nuovi”. I cluster di 130 record ChetterHummin, 48 Logan e 10
  Treadmilled senza source combaciano con questo comportamento.
- `reconcile()` continua a valutare il candidato anche quando
  `get_recent_buy()` non trova un BUY: usa hash fallback e può classificarlo
  `eligible`. Nel campione accade 11 volte su 48 (22,9%). Questi record non
  dimostrano un segnale nuovo e non devono poter aprire in paper.
- Eliminando i candidate privi di source o con latenza >60s restano 37 eligible,
  30 asset, 29 condition, 25 eventi e solo 3 wallet. Armageddon pesa 24/37
  (64,9%), AnonymousUsername 9 e denizz 4.
- Il drift è calcolato contro `avg_price` della posizione aggregata, non contro
  `source_trade_price` del BUY appena recuperato. Inoltre il journal non salva
  `source_trade_price/source_trade_size`, sebbene il lookup li produca. Non è
  quindi possibile verificare a posteriori il vero drift del trade sorgente.
- Anche CLOB è bloccato localmente da certificato proxy + HTTP 403. Gli esiti
  correnti non sono recuperabili da questa macchina; servirà, se desiderato,
  uno snapshot pubblico generato dalla VPS che già accede ai feed.

### Continuità e superficie dashboard

- `equity_curve.json` è un buffer degli ultimi 10.000 cicli (01/08 02:30 UTC →
  03/08 15:28 UTC), tutto a $300. Intervallo massimo 28,65s e zero gap >60s:
  conferma operatività continua nell'ultima finestra, non solo al prelievo.
- Il primo comando di analisi dashboard è uscito con codice 1 solo perché `rg`
  non trovava traceback/500; i dati precedenti erano validi. Parser alternativo
  conferma zero 500 e zero traceback.
- La dashboard è esposta pubblicamente e ha ricevuto almeno 546 richieste 404,
  molte chiaramente ostili/scanner (`/.git/config`, path traversal `/etc/passwd`,
  `/v2/_catalog`, `/login`, `/sdk`). Non risultano successi su tali path, ma API
  e dati operativi sono raggiungibili senza autenticazione. Raccomandazione:
  bind localhost + tunnel SSH, oppure firewall/reverse proxy autenticato.

### Completezza e produttività wallet

- Nei 48 eligible, book/top depth/VWAP/size/entry/costi sono completi 48/48;
  source trade solo 37/48. `event_slug` è completo ma `event_title` è vuoto in
  tutte le 4.484 righe. Il journal non conserva end date né livelli completi del
  book, quindi non permette di ricostruire integralmente scadenza e VWAP.
- Il massimo spread eligible 3,6 cent è conforme alla configurazione COPY
  (`max_spread_ticks=4`), non una violazione.
- Tolto il rumore crypto intenzionalmente escluso, il tasso verified eligible è
  37/744 = 4,97%.
- Produttività verified: AnonymousUsername 9/11 (81,8%, soprattutto MLB), denizz
  4/45 (8,9%), Armageddon 24/477 (5,0%); gli altri nove wallet zero. Le percentuali
  alte su n piccoli non provano edge, ma mostrano forte squilibrio del cohort.
- ChetterHummin (130/131 source mancanti), Logan (48/55), Treadmilled (10/11) e
  Armageddon (125/477) evidenziano l'effetto timeout/flapping: i source mancanti
  arrivano in blocchi, non come rumore casuale.
- In `paper_validation` lo stesso timeout sarebbe più grave: `reconcile()` vede
  l'asset assente, considera ancora monitorato il source wallet e chiude la
  posizione come `exit`, scambiando un feed failure per una vendita reale.
  Quindi il bug può falsare sia ingressi sia uscite/P&L ed è bloccante.
- La dashboard Flask usa davvero `host="0.0.0.0"`; `start_all.sh` stampa
  “localhost” ma non limita il bind. La superficie pubblica osservata nei log
  deriva quindi dalla configurazione corrente, non da un falso positivo.

### Verdetto Phase CM

**NON promuovere ancora COPY a `paper_validation`.** Il bot è stabile e i
prezzi pre-trade sono realistici, ma il contratto di identità del segnale non è
sicuro: 22,9% degli eligible non ha trade sorgente e un timeout può produrre sia
falsi ingressi sia false uscite. L'OBSERVE corrente resta evidenza diagnostica,
non campione valido di edge.

Fix obbligatori prima del paper:
1. propagare success/error per wallet da `/positions` e preservare holdings
   precedenti dei wallet falliti; mai interpretare errore come vendita;
2. richiedere BUY sorgente con tx hash e timestamp recente (es. <=60s), con
   reject distinti `source_trade_unavailable`/`source_trade_stale`;
3. calcolare drift da `source_trade_price`, non da `avg_price` aggregato;
4. journalizzare source price/size, end date e dati sufficienti a ricostruire
   il VWAP; mantenere compatibilità v2 introducendo una versione successiva;
5. mettere dashboard dietro localhost/firewall/auth.

Dopo il fix: archiviare il run, nuovo OBSERVE con lo stesso cohort (senza scan
adattivo) per almeno 48 ore. Gate regressione: zero eligible senza source, zero
falsi delta/exit dopo timeout mockato, zero errori ciclo, source latency p95
coerente. Solo allora avviare un nuovo `paper_validation` da $5/max 2.

Il sample OBSERVE non contiene lifecycle/exit delle posizioni virtuali, quindi
non può dimostrare P&L o EV. I criteri 100 chiuse/30 eventi/14 giorni si applicano
al successivo run paper; al ritmo osservato (~3,59 segnali verified/giorno), 100
segnali richiederebbero teoricamente ~27,8 giorni prima dei limiti portfolio.

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

## Paper run 2026-08-07 - verifica prima apertura

- Run `run-20260807T141814-a65fb998` correttamente in `paper_validation`.
- Prima posizione: Rio Ferdinand wedding, ask 0.46, bid 0.45, feeSchedule
  rate 0.05/exponent 1, entry netta 0.47242, size $5, 10.58 shares.
- Entry fee $0.13145 corretta. Equity mostrata $299.76 include entry fee e
  spread, ma non la fee di uscita implicita: mark liquidabile netto 0.437625
  ed equity coerente circa $299.63.
- Bug contabile aggiuntivo: `Portfolio.close_position` accredita
  `pos.current_value`, mentre i call-site passano `exit_eff` soltanto a
  `Position.close`. Il P&L chiuso usa il prezzo netto, ma il cash riceve ancora
  il prezzo lordo memorizzato in `current_price`.
- Fix locale: per COPY con fee metadata noto, `current_price` rappresenta ora il
  ricavo liquidabile al bid netto della fee di uscita; `Position.close` allinea
  il mark all'exit netto prima dell'accredito cash. Ledger portato a state v3.
- Migrazione state v2 verificata sul caso reale: bid lordo 0.45 -> mark netto
  0.437625 una sola volta, preservando `run_id`, posizione e cash $295.
- Se la posizione viene chiusa prima del deploy, la migrazione v2 ricostruisce
  anche il cash da size aperte e exit netti; si attiva solo su run fee-v4 puri.
- Verifica completa: 46/46 unittest, `compileall` e `git diff --check` OK.

## Audit OBSERVE 24h - bundle 2026-08-07

- Archivio: `logs/exports/polymarket-observe-20260807T135646Z.tar.gz`.
- SHA-256: `848E35E8CCB1D0AF61B9C6B5A84E64ED50046E815980969E9A3E1D061F564C4E`.
- Bundle integro e sostanziale: portfolio, journal, manifest wallet, runtime/API,
  bot/dashboard log e metadati git presenti; file error API vuoti.
- Snapshot creato 2026-08-07 15:56 locale VPS; journal aggiornato fino alle 15:52.
- Commit deployato corretto: `8c9d3f98e4766e477874c147c06033678c924117`.
- Run corrente: `run-20260806T135444-8c20a465`, `observe`, capitale/cash $300,
  zero posizioni aperte o chiuse; wallet manifest congelato.
- Durata dati preliminare: dal 2026-08-06 14:01:24 UTC al 2026-08-07
  13:52:36 UTC (quasi 24 ore).
- 191 candidati tutti unici: 46 eligible (24.08%), 145 rejected; 176/191
  lookup sorgente `ok`, 14 `not_found`, 1 `error`.
- Al momento dell'export bot e dashboard erano attivi, latency-arb fermo,
  runtime al ciclo 3711 in fase idle, stato vecchio di 14.34 secondi.
- Journal v3 coerente: 46/46 eligible hanno sorgente verificata, evento/asset,
  bid/ask, VWAP, profondita, fee e latenza validi; nessun book crossed, fill
  insufficiente o latenza fuori 0-60 secondi tra gli eligible.
- Latenza eligible: mediana 11.38s, p95 27.14s, massimo 30.94s. Spread mediano
  1 cent, p95 2 cent, massimo 3 cent. Size $5 interamente fillabile al primo
  livello in tutti i 46 casi; nessun slippage VWAP aggiuntivo nel campione.
- Concentrazione rilevante: 36/46 eligible (78.3%) provengono da un wallet e
  10/46 dall'altro; soltanto 2 dei 12 wallet generano eligible. Gli eligible
  coprono 28 eventi, 36 condition e 42 asset; categorie: 36 sport, 10 other.
- Salute feed buona ma non perfetta: un timeout `/positions` su un wallet,
  correttamente trattato come unknown con baseline preservata, e un HTTP 429
  sul lookup `/activity`, correttamente registrato come lookup error. Zero
  traceback, zero HTTP 400, zero halt e recupero al ciclo successivo.
- Dashboard: zero risposte 500. Il log mostra scansioni automatiche da IP esterni
  sulla porta pubblica; rischio noto, non bloccante per il paper su richiesta.
- BLOCCANTE scoperto prima del paper: il modello locale applica fee solo a
  `sport` (rate 0.03) e zero a tutte le altre categorie. La documentazione
  Polymarket aggiornata al 2026 indica fee anche su crypto 0.07, finance/politics
  0.04, economics/culture/weather/other 0.05, mentions/tech 0.04; geopolitics
  resta fee-free. Le fee sono per-market (`feesEnabled`/`feeSchedule`).
- Conseguenza: 10/46 eligible `other` del run sono stati valutati con fee zero;
  un paper run avviato ora sottostimerebbe costi di ingresso e uscita. Il gate
  tecnico non puo ancora essere promosso finche il fee model non e corretto e
  verificato per mercato.
- La pagina ufficiale letta il 2026-08-07 riporta ora sports rate 0.05 (non 0.03),
  confermando che anche il fallback sport hardcoded e obsoleto. La formula
  ufficiale e `shares * rate * p * (1-p)`; il codice corrente usa come frazione
  del notional `rate * min(p,1-p)`, che coincide solo a p=0.5 e sottostima le
  fee soprattutto sotto 0.5.
- API autorevoli disponibili: Gamma `feesEnabled` + `feeSchedule`, oppure CLOB
  `/clob-markets/{condition_id}` con `fd.r`, `fd.e`, `fd.to`. Il changelog
  prescrive esplicitamente di usare `feeSchedule` per-market dal 31 marzo 2026.
- Stress test conservativo sui 46 eligible, assumendo rate corrente 0.05 per
  tutti: fee ingresso totale $5.70 invece dei $2.36 registrati dal vecchio
  modello. Costo round-trip immediato medio $0.392 per trade da $5 (7.84%),
  mediana 7.86%, p95 10.48%. Questo non prova perdita futura, ma mostra che il
  paper deve superare un hurdle di costi molto piu alto di quello precedente.
- Implementazione locale: journal v4, metadati fee Gamma per-market obbligatori,
  reject fail-closed se assenti, fee ingresso/uscita persistite sulla Position,
  formula ufficiale generalizzata con exponent. Suite finale: 44/44 test pass,
  compileall e `git diff --check` puliti.
# Audit shadow 2026-08-10 — riscontri documentazione ufficiale

- L'endpoint ufficiale batch `POST /books` restituisce i book con livelli `bids` e `asks` per gli asset richiesti: il journal sta quindi acquisendo la sorgente corretta per prezzi eseguibili, non un midpoint teorico.
- La documentazione ufficiale definisce le fee come dipendenti dal mercato, lato taker, con formula proporzionale a `C × feeRate × p × (1-p)`. Il replay deve pertanto continuare a usare il `fee_schedule` persistito per ciascun mercato; non va sostituito con una percentuale globale per categoria.
- I valori economici dell'export sono riconciliati con i parametri fee salvati nel journal. La perdita non è soltanto una conseguenza delle fee: sui 32 trade chiusi il movimento lordo ask→bid è già negativo per circa $8,48, a cui si sommano circa $7,52 di fee.
