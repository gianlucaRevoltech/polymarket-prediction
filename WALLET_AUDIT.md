# Audit wallet indipendente dal paper

Il run corrente continua con la coorte congelata. L'audit non modifica manifest,
wallet monitorati, scan, ledger, parametri di esecuzione, quarantene o servizi.
La shortlist non viene importata automaticamente. Nessun ordine reale.

## Uso locale

Serve un export aggiornato della VPS, come directory o `.tar.gz`, contenente:
`run_manifest.json`, `monitored_wallets.json`, `wallet_validation_registry.json`,
`portfolio_state.json`, `candidate_journal.jsonl`, e facoltativamente
`scan_results.json`. I file possono essere nella sottocartella `data/`.
Il manifest determina la coorte; niente sostituzione con lo scan disponibile.
Export legacy senza manifest sono letti come tali, non come prova della coorte
attuale. Run discordanti, duplicati e file malformati vengono rifiutati.

```bash
python tools/wallet_audit.py --snapshot exports/NUOVO-EXPORT.tar.gz --output research/wallet-audits/coorte-20260831
```

Scrive soltanto nell'output esplicito: `report.json`, `report.md`, cache delle
risposte pubbliche, metadati e avanzamento parziale. Non estrae gli archivi.
Nel repository l'output deve essere sotto `research/wallet-audits/`, ignorato
da Git. Una directory non vuota appartenente ad altro lavoro è rifiutata.

`--max-new 0` analizza solo la coorte esportata; default 300 alternative.
`--offline` riusa esclusivamente la cache della stessa cartella. Ripetere il
comando con gli stessi input conserva l'istante di riferimento dell'audit;
per dati nuovi usare un'altra cartella. `--as-of` accetta un epoch UTC per
riproducibilità, ma non ricrea snapshot di posizioni storiche mancanti.
Il codice d'uscita 2 indica input/trasporto incompleti; leggere sempre i motivi
di esclusione nel report anche con codice 0 (feed completo non equivale a
contabilità riconciliata né a strategia redditizia).

## Metodo v2

- Activity nelle finestre 7/30/90 giorni, chiusure flat-to-flat ricostruite con
  costo medio, separando incrementi e riaperture. Non si contano i fill come
  posizioni chiuse. Le finestre selezionano il timestamp della chiusura, non
  frazioni arbitrarie del profitto maturato nei giorni precedenti.
- Vendite oltre inventario: attribuzione proporzionale alle sole quote note,
  inventario mai negativo, asset non qualificabile. Base mancante non è costo zero.
- Riscatti associati al proprio asset, payout zero ammesso; asset assente o
  operazioni non ricostruibili danno qualità unknown.
- Prezzi estremi non provano risoluzione. Snapshot redeemable con payout esatto
  0/1 può documentare settlement, ma senza timestamp rimane fuori dalle finestre.
- `totalBought` ufficiale confrontato con quote acquistate, non USDC spesi;
  P&L realizzato e inventario devono riconciliarsi. Totali ufficiali lifetime
  non sono riattribuiti a una finestra di 90 giorni con costo iniziale ignoto.
- Posizioni aperte senza acquisti ricostruiti, trasferimenti/split/merge/conversioni,
  incoerenze, errori API o limiti raggiunti impediscono la shortlist. Un wallet
  può quindi restare unknown anche se nella classifica ufficiale è in profitto.
- Cashflow wallet, incentivi e P&L netto della copia sono separati. Copertura delle
  fee storiche non garantita: nessuna applicazione retroattiva delle fee odierne.
  Book storici e prezzi ritardati della copia non sono inventati.
- Con errori contabili, i valori aggregati P&L/WR sono null. Il sottoinsieme
  verificabile rimane in `diagnostic_subset`; non è la performance del wallet.
- `original_scan` conserva le statistiche esportate; `paper` riporta solo il
  ledger del run esportato, con P&L realizzato/non realizzato netto e relativa data.

L'ordine di scoperta alterna categorie e fonti (leaderboard WEEK/MONTH,
PNL/VOL, più scan esportato), senza filtrare i wallet anonimi. Ogni sorgente
leaderboard fornisce i primi 50; non è una ricerca esaustiva di Polymarket.
La coorte attuale è sempre inclusa e non consuma il budget delle alternative.

Shortlist: 50 chiusure/20 eventi in 90 giorni, P&L delle chiusure positivo a
30/90 giorni, BUY >=$5 su 10 asset e tre giorni nell'ultima settimana,
ultimo BUY entro 48h, nessuna quarantena o errore irrisolto. Nessuna soglia WR.
Ordine: profit factor30, asset BUY qualificanti7, numero chiusure90, indirizzo
come tie-break deterministico. PF senza perdite è segnalato esplicitamente,
non serializzato come Infinity. Massimo 20, senza allentare le soglie.

WR con Wilson CI95 e statistiche per dominio/evento sono descrittivi: eventi
correlati riducono l'informazione del campione e lo scan è in-sample. Più BUY
non garantiscono più aperture copiabili: gli incrementi non sono nuovi delta.
Resta necessaria la successiva misura prospettica dei nostri prezzi e costi.

## Limiti API e risorse

Client GET pubblico, senza credenziali, una richiesta alla volta, massimo 2/s.
Timeout 20s, tre tentativi con backoff e Retry-After; budget globale 20.000
richieste, activity massimo 600 pagine/wallet, posizioni 400 pagine/endpoint.
Limiti esauriti diventano unknown, non successi troncati. Cache e avanzamento
consentono di ripetere il comando senza rifare le letture già completate.
Eseguire localmente, non accanto al feed live della VPS sullo stesso IP.

Activity limit500/offset5000: finestre dense divise temporalmente; oltre il
cap nello stesso secondo la copertura resta unknown. Closed positions limit50
ordinate per timestamp; current positions limit500 e offset<=10000.
Il paging di posizioni live non è atomico: cambiamenti durante l'audit possono
produrre mismatch, che non vengono ignorati per far passare un wallet.

Un errore di certificato TLS interrompe le nuove richieste dell'audit; la cache
rimane leggibile e il report segnala `tls_verification_failed`. Non usare
`verify=False`: correggere prima DNS/proxy/certificati dell'ambiente locale.

Fonti ufficiali:
[activity](https://docs.polymarket.com/api-reference/core/get-user-activity),
[posizioni chiuse](https://docs.polymarket.com/api-reference/core/get-closed-positions-for-a-user),
[leaderboard](https://docs.polymarket.com/api-reference/core/get-trader-leaderboard-rankings),
[limiti API](https://docs.polymarket.com/api-reference/rate-limits).

## Dashboard e verifica

Le schede mostrano statistiche storiche dello scan, metodo/data/finestra/qualità
quando disponibili. I vecchi manifest restano immutati: qualità legacy non
verificata e data non disponibile sono preferibili a inventare provenienza.
ACTIVE significa monitorato, non profittevole. Nessuna chiamata esterna nel refresh.

Test: `python -m unittest discover -s tests -v`,
`python -m compileall -q src tools tests`, `node tests/dashboard_smoke.cjs`,
`bash -n start_all.sh`, `git diff --check`.

Non serve `new-run`, scan o reset per l'audit. Il report è ricerca separata,
non un file di configurazione. Eventuali nuovi wallet richiederanno una decisione
successiva e un campione indipendente; non sostituire quelli del paper in corso.
