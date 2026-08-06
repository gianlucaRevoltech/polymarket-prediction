# Protocollo di validazione prospettica

Il bot parte in `observe`: scansiona COPY e registra i candidati, ma non apre
posizioni. HARVEST, arb-cross, le altre strategie e latency-arb restano spenti.
Ogni candidato viene comunque valutato con book eseguibile, spread, profondità,
scadenza, drift e fee. `eligible` significa soltanto che i controlli pre-trade
sono stati superati; non è un trade e non implica profitto.

Dal journal v3 un candidato COPY è valutabile solo se `/activity` conferma un
BUY sorgente con `transactionHash`, prezzo valido e timestamp non più vecchio di
60 secondi. Il drift usa quel prezzo, non il prezzo medio storico del wallet.
Ask, bid, profondità e VWAP sono derivati dallo stesso snapshot CLOB e il journal
salva i livelli consumati, la scadenza e lo stato del lookup sorgente.

Gli errori `/positions` non vengono interpretati come wallet vuoti: baseline e
posizioni restano invariate. Solo uno snapshot riuscito che dimostra l'assenza
dell'asset può produrre un'uscita COPY. Anche il primo successo di un wallet
dopo un timeout viene usato come baseline, senza copiare il bag preesistente.
Lo snapshot pagina fino a 500 posizioni per richiesta e non usa mai pagine
parziali. Dopo tre errori transitori consecutivi, i wallet restanti vengono
rinviati al ciclo successivo come stato sconosciuto per evitare outage seriali.
Il lookup BUY considera fino a 500 attività recenti con filtri server-side.

I wallet sono congelati per l'intero run. Lo scan e le sostituzioni si eseguono
solo tra run con `new-run scan`, così il campione non cambia adattivamente.

La modalità `paper_validation` richiede
`POLYMARKET_EXECUTION_MODE=paper_validation`. Usa size fissa $5, massimo due
posizioni, una per evento, wallet congelati per il run, Kelly/compounding e
trailing disabilitati.

COPY è promuovibile a un secondo run paper indipendente solo se, nello stesso
run, supera tutti i criteri:

- almeno 100 trade COPY chiusi, 30 eventi distinti e 14 giorni;
- P&L netto positivo dopo i costi;
- limite inferiore bootstrap CI95 dell'EV/trade maggiore di zero;
- drawdown massimo non superiore al 3%;
- nessun evento o wallet oltre il 20% del P&L positivo;
- almeno 30 trade per ogni dominio che si intende abilitare.

`src/validation.py` calcola il verdetto. Il verdetto non autorizza denaro reale:
qualsiasi passaggio reale resta fuori scope e richiede una decisione separata.

Operazioni VPS:

```bash
./start_all.sh restart        # conserva sempre stato e run
./start_all.sh new-run        # archivia ledger/config, poi crea un nuovo run
./start_all.sh new-run scan   # nuovo run + nuova selezione wallet (raccomandato)
./start_all.sh reset --force  # archivia prima di cancellare; non riavvia
```

Rollout del hardening pre-paper mantenendo lo stesso cohort già auditato:

```bash
git pull --ff-only
unset POLYMARKET_EXECUTION_MODE LATENCY_ARB_ENABLED
./start_all.sh new-run
./start_all.sh status
```

Il nuovo OBSERVE deve girare almeno 48 ore senza traceback, false riaperture o
`eligible` privi di sorgente verificata. Solo dopo la revisione di quel journal
si crea un run paper separato:

```bash
export POLYMARKET_EXECUTION_MODE=paper_validation
unset LATENCY_ARB_ENABLED
./start_all.sh new-run
./start_all.sh status
```

L'export va mantenuto nell'ambiente usato per i successivi restart del run paper;
in sua assenza il default torna intenzionalmente a `observe`.

Il dashboard espone il riepilogo candidati in `/api/status` e le righe recenti
in `/api/candidates?limit=50`. Tutti i timestamp nuovi sono UTC con offset; lo
stale viene calcolato sul server e scatta dopo 60 secondi senza ledger.

Una quarantena per tre perdite consecutive si rimuove solo esplicitamente:

```bash
venv/bin/python tools/reactivate_strategy.py copy --confirm
```
