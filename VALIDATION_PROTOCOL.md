# Protocollo di validazione prospettica

Il bot parte in `observe`: scansiona COPY e registra i candidati, ma non apre
posizioni. HARVEST, arb-cross, le altre strategie e latency-arb restano spenti.
Ogni candidato viene comunque valutato con book eseguibile, spread, profondità,
scadenza, drift e fee. `eligible` significa soltanto che i controlli pre-trade
sono stati superati; non è un trade e non implica profitto.

Dal journal v6 un candidato COPY è valutabile solo se `/activity` conferma un
BUY sorgente con `transactionHash`, prezzo valido e timestamp non più vecchio di
60 secondi. Il drift usa quel prezzo, non il prezzo medio storico del wallet.
Ask, bid, profondità e VWAP sono derivati dallo stesso snapshot CLOB e il journal
salva i livelli consumati, la scadenza e lo stato del lookup sorgente. I costi
usano inoltre `feesEnabled` e `feeSchedule` Gamma del singolo mercato; metadati
fee mancanti o invalidi rendono il candidato non eleggibile, senza fallback a
costo zero. Il ledger conserva rate/exponent per applicare la stessa curva fee
anche all'uscita. Il journal v6 salva inoltre `num_holders`, la lista ordinata
degli holder osservati, il notional del BUY sorgente e il costo certo di
liquidazione immediata.

Il BUY sorgente deve avere notional almeno pari alla size paper da $5: il bot
non assume più rischio del wallet copiato. Dopo ask VWAP e fee d'ingresso viene
calcolato anche il bid VWAP netto della fee d'uscita; se lo spread round-trip
immediato supera il 2,5% della size il candidato viene respinto come
`immediate_roundtrip_cost_too_high`. Questo limita la frizione certa ma non
dimostra alcun edge.

Gli errori `/positions` non vengono interpretati come wallet vuoti: baseline e
posizioni restano invariate. Solo uno snapshot riuscito che dimostra l'assenza
dell'asset può produrre un'uscita COPY. Anche il primo successo di un wallet
dopo un timeout viene usato come baseline, senza copiare il bag preesistente.
Lo snapshot pagina fino a 500 posizioni per richiesta e non usa mai pagine
parziali. Dopo tre errori transitori consecutivi, i wallet restanti vengono
rinviati al ciclo successivo come stato sconosciuto per evitare outage seriali.
Il lookup BUY considera fino a 500 attività recenti con filtri server-side.
Le richieste Data API condividono un pacing di 100 ms e, dopo 429/5xx/timeout,
un cooldown esponenziale fino a 30 secondi. Contatori, status HTTP, cicli
parziali e backoff residuo sono esposti in `bot_health.feed_health`. Il limite
ufficiale `/positions` è 150 richieste ogni 10 secondi:
https://docs.polymarket.com/api-reference/rate-limits

I wallet, i loro domini specialistici e l'insieme dei domini da validare sono
congelati nel manifest per l'intero run. Un segnale fuori dai domini qualificati
del wallet viene respinto come `wallet_domain_mismatch`. Lo scan e le
sostituzioni si eseguono solo tra run con `new-run scan`, così il campione non
cambia adattivamente.

Il preflight richiede almeno 5 wallet unici gia nel cohort congelato. Lo scan
pagina 600 mercati Gamma in blocchi da 100 e mantiene invariati ROI, win rate, overlap e
quarantene; se trova meno di 5 wallet, `start_all.sh` termina con errore e non
avvia bot o dashboard. `scan_results.json` conserva `scan_diagnostics` con
richieste holder, errori, esclusioni e stato `validation_ready`. Il limite
documentato di `/holders` e 20 per token e viene sempre rispettato.

Tre perdite shadow consecutive dello stesso wallet lo registrano in
`wallet_validation_registry.json`. Il wallet resta nel cohort corrente per non
alterare il campione, ma viene escluso da tutti gli scan e seed dei run
successivi. Il registro sopravvive a `new-run`; ogni archivio ne conserva una
copia insieme allo `scan_results.json` usato dal run.

## Validazione shadow

In `observe`, ogni segnale che supera i controlli pre-trade viene sottoposto a
un portafoglio shadow separato da $300. Lo shadow usa size fissa $5, massimo due
posizioni, una posizione per evento, cap evento 3% e dedup asset/condition. Un
segnale bloccato viene journalizzato come `rejected` con il vero portfolio gate
e non viene aperto più tardi. Cash e rischio shadow non modificano portfolio,
cooldown, circuit breaker o quarantene del paper.

L'ingresso shadow usa ask VWAP e fee per-market; mark e uscita usano bid VWAP e
fee. Le chiusure seguono vendita del wallet, stop/target o risoluzione esplicita.
I book vengono richiesti in batch con `POST /books`; un errore conserva i mark
precedenti e non viene interpretato come uscita o risoluzione. Il CI95 usa un
bootstrap a cluster evento, così segnali correlati non gonfiano la confidenza.
Peak, equity, cash e drawdown shadow sono mark-to-market e persistono nel ledger
v3. Le nuove aperture si fermano a -$3 giornalieri, -$6 sul run o dopo tre
perdite consecutive; le posizioni già aperte continuano a essere gestite.
Un ledger shadow v1 viene preservato e chiuso normalmente ma non accetta nuovi
ingressi: serve `new-run scan` per iniziare un campione v3 confrontabile.

Ogni wallet può contribuire al massimo 20 aperture shadow e 20 aperture paper
nello stesso run.
Con la soglia minima di 100 trade, la promozione richiede quindi almeno cinque
fonti produttive; il valutatore verifica anche che nessun wallet rappresenti
più del 20% di tutte le chiusure. Questo impedisce che un unico wallet costituisca
il campione promozionale, anche se il suo P&L fosse positivo.

Ogni dominio destinato alla validazione richiede almeno 30 trade. Lo scanner
riserva quindi almeno due specialisti distinti per dominio (cap 20 trade per
wallet) e assegna ciascun wallet a un solo dominio del run. Domini con capacita
insufficiente vengono esclusi prima del congelamento, non lasciati fallire dopo
14 giorni; l'elenco e persistito in `scan_diagnostics.validation_domains`.

Il gate shadow può autorizzare soltanto un nuovo run `paper_validation`
indipendente. Anche con tutti i gate verdi, `real_money_authorized` resta sempre
`false`; il repository non contiene un percorso di invio ordini reali.

## Paper sperimentale — procedura corrente (31 agosto 2026)

La modalità è persistita nel run manifest: `restart` conserva modalità, coorte,
domini e storico anche senza export o con ambiente discordante. Il commit
origine resta immutabile; il runtime dichiara separatamente commit e identità
del processo. Gli aggiornamenti tecnici sono registrati nella provenienza.

L'avvio sperimentale non richiede un edge già dimostrato: volume basso, zero
candidati e campione economico piccolo sono warning, non autorizzazioni al reale.
Preflight tecnico, coorte e circuit breaker restano obbligatori. Non si forzano
aperture abbassando i filtri. Nessun nuovo periodo OBSERVE a durata prefissata.

Dopo il deploy della patch, preservando l'OBSERVE corrente:

```bash
git pull --ff-only
./start_all.sh restart
./start_all.sh preflight-paper --wait 120
# Solo se READY:
./start_all.sh paper-start
./start_all.sh status
./start_all.sh paper-report
```

Non eseguire new-run, scan o reset per questo aggiornamento. I comandi
operativi mutanti sono protetti da lock. I servizi non ereditano il lock.

Il preflight richiede due snapshot completi consecutivi dell'intera coorte,
due cicli riconciliati e salvati, ledger/heartbeat/ultimo ciclo entro 60 secondi,
schema e commit runtime corretti, assenza di traceback e breaker, journal v6
e contabilità validi. I contatori di errori storici non rappresentano un outage
attuale. Due snapshot completamente falliti consecutivi o tre incompleti
indicano outage; una risposta HTTP riuscita non prova il recupero della coorte.

`paper-start` verifica il run prima di fermarlo e dopo l'arresto, archivia con
hash verificati, prepara un nuovo paper con la stessa coorte e capitale $300.
Stati attivazione: pending, active, failed. Nessuna apertura prima della verifica
di due cicli del nuovo processo (timeout 120 secondi). Errori lasciano i servizi
fermi e lo stato conservato. Ripetere paper-start riprende una creazione/verifica
incompleta senza azzerare il nuovo campione; un paper attivo non viene ricreato.
Anche restart richiede due cicli sani prima di nuove aperture. Le posizioni
esistenti restano gestite con feed validi; nessuna riattivazione automatica
dei breaker economici.

Rischio invariato: COPY soltanto, size $5, massimo due posizioni, una per evento,
cap evento 3%, massimo 20 aperture per wallet; costo round-trip massimo 2,5%;
halt giornaliero -$3, run -$6 e quarantena dopo tre perdite. Fee per-market,
ask VWAP in ingresso e bid VWAP netto in uscita. Kelly, compounding, trailing,
altre strategie e latency-arb restano disabilitati.

`paper-report` salva data/paper_report.json e stampa la sintesi del solo run
corrente. P&L realizzato/non realizzato, cash, equity e fee sono riconciliati;
stime fee dei candidati non eseguiti sono escluse. Dati mancanti sono errori
di qualità, non zeri. La concentrazione positiva senza vincite è non disponibile.
Il massimo drawdown viene persistito nei nuovi snapshot anche oltre la finestra
della curva equity.

Controllo tecnico dopo 24 ore; prima revisione economica dopo sette giorni,
senza tuning o sostituzioni della coorte. Nessun guadagno garantito.
Per avanzare restano obbligatori, nello stesso run:

- 100 chiusure COPY, 30 eventi distinti e almeno 14 giorni;
- P&L netto positivo e limite inferiore bootstrap CI95 EV/trade > 0;
- drawdown massimo <=3%;
- nessun evento o wallet oltre il 20% del P&L positivo;
- almeno cinque wallet sorgente, nessuno oltre il 20% delle chiusure;
- almeno 30 trade per ogni dominio congelato.

Serve comunque un secondo paper indipendente prima di discutere denaro reale.
`real_money_authorized` resta sempre false. Nessun wallet reale, deposito o
ordine reale rientra in questa fase.

Il dashboard espone il riepilogo candidati e shadow in `/api/status`, le righe
recenti in `/api/candidates?limit=50` e il lifecycle shadow in
`/api/shadow?limit=50`. Tutti i timestamp nuovi sono UTC con offset; lo stale
viene calcolato sul server e scatta dopo 60 secondi senza ledger.
`/api/status` espone anche `cohort_health`; una coorte insufficiente compare in
rosso e non puo essere confusa con un run validabile.

La quarantena della strategia paper nel run corrente si rimuove solo
esplicitamente; questo comando non cancella la quarantena cross-run dei wallet:

```bash
venv/bin/python tools/reactivate_strategy.py copy --confirm
```
