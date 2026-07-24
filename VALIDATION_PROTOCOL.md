# Protocollo di validazione prospettica

Il bot parte in `observe`: scansiona COPY e registra i candidati, ma non apre
posizioni. HARVEST, arb-cross, le altre strategie e latency-arb restano spenti.
Ogni candidato viene comunque valutato con book eseguibile, spread, profondità,
scadenza, drift e fee. `eligible` significa soltanto che i controlli pre-trade
sono stati superati; non è un trade e non implica profitto.

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

Il dashboard espone il riepilogo candidati in `/api/status` e le righe recenti
in `/api/candidates?limit=50`. Tutti i timestamp nuovi sono UTC con offset; lo
stale viene calcolato sul server e scatta dopo 60 secondi senza ledger.

Una quarantena per tre perdite consecutive si rimuove solo esplicitamente:

```bash
venv/bin/python tools/reactivate_strategy.py copy --confirm
```
