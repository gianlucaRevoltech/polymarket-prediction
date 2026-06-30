# Findings & Decisions — Polymarket Copy Bot (debug perdita)

## Requirements
- Analizzare perche il bot e in perdita (-2.09%, WR 40%)
- Pianificare una strategia corretta e profittevole
- NON stravolgere la lista wallet monitorati (vincolo utente)
- Gestire wallet con ROI "troppo basso"

## Diagnosi — Cosa stiamo sbagliando

### Sintesi stato (screenshot 2026-06-30 07:40)
- Equity $293.73 / $300 -> -$6.27 (-2.09%)
- Realizzato -$4.10 | Unrealizzato -$2.17
- 6 aperte, 25 chiuse, WR 40% (10W / 15L)

### P1 — Mirroring copia lo snapshot, non il trade (entrate tardive)
Il bot NON replica l'istante di ingresso del wallet: legge `/positions` ed entra
al prezzo CORRENTE del mercato, spesso settimane dopo l'ingresso reale del wallet.
- Esempio dashboard: "France World Cup No" entry $0.778 -> $0.711 (-8.7%)
- "Argentina World Cup No" entry $0.818 -> $0.806 (-1.4%)
- Il guardrail max_entry_drift (12%) confronta cur vs avg_price del wallet, ma
  avg_price aggregato e poco affidabile e comunque entrare a 0.78 su esito che paga
  $1 max = upside $0.22 (28%) vs downside $0.78 (-77%) -> attesa negativa.

### P2 — Dump intero portafoglio al primo snapshot
portfolio_state.json: TUTTE 10 posizioni aperte allo STESSO secondo
(2026-06-27 15:22:33). Copiamo il bag intero del wallet in un colpo, non i singoli
trade nel tempo. Risultato: esposizione a 10 mercati slegati, molti a scadenza
lunghissima (Newsom 2028, Rubio 2028, Bolsonaro 2026, Djokovic Wimbledon) che
immobilizzano capitale per mesi/anni. Su budget $300 e deleterio.

### P3 — Posizioni correlate, nessun filtro direzionale
Stesso wallet copia Newsom-nomination (Yes@0.21) E Newsom-presidenza (Yes@0.21):
rischio concentrato, finta diversificazione. Inoltre copiamo Yes su longshot
politiche 0.10-0.22 (lungo la banda longshot che il backtest del config definiva
ROI mediano -100%).

### P4 — SL/TP asimmetrici -> attesa negativa su random walk
Config: SL -30% / TP +50%. Con WR 40% serve vincite > 1.5x perdite perpareggiare.
Invece TP +50% taglia i vincenti presto, SL -30% lascia correre i perdenti.
Realizzato -$4.10 = conferma empirica. Il vero segnale informato (exit quando il
wallet sorgente esce) viene eclissato dalle soglie meccaniche.

### P5 — Filtro win-rate NON enforceato (bug)
scan_results.json contiene wallet con win_rate SOTTO la soglia 0.55 del config:
- Q96s3kwo: 0.428
- Logan: 0.444
- yupiiiiiiiii: 0.444
Ipotesi causa: `main.run_initial_scan` usa `scan_all` (legacy leaderboard) come
fallback quando scan_results manca. scan_all filtra solo per ROI leaderboard e
min_trades, NON per win_rate ne min_decided. Salva scan_results senza filtro
qualita -> e per questo che copiamo wallet con bassa frequenza di vittoria.
DRILL DOWN: quando il bot parte a freddo e scan_results assente, assicurarsi
che il path sia scan_categories (che ha _qualify_wallets con min_win_rate) e
NON scan_all.

### P6 — ROI "troppo basso" e un problema di WIN-RATE e campione, non di ROI
ROI leaderboard = PnL / volume aggregato, NON per-trade. Wallet con ROI 24% ma
WR 44% e 34 trade e statisticamente negativo (perdite frequenti, una vincente
fortunata sostiene il ROI aggregato). Per copy con 6 posizioni e budget $300,
contano win-rate e consistenza recenti, non il ROI-whale lifetime.Panelelow ROI
non va "tolto" (vincolo utente) ma soft-penalizzato.

### P7 — Sizing/allocazione subottimale
max_position 5% = $15, max 6 pos -> <= $90 deployato, $210+ cash a rendimento zero
mentre le 6 aperte perdono. Con $300 e spread Polymarket $0.01-0.03 ogni trade
paga ~1-3% slippage round-trip: margine minuscolo, ogni errore di timing e
insuperabile (P1 amplifica).

### P8 — Mercati a lunga scadenza immobili
Newsom/Rubio/Vance 2028, Bolsonaro 2026, Wimbledon: capital-lock 6-24 mesi.
Mark-to-market mostra solo -1% (drift lungo) ma capitale bloccato. Per budget
ridotto sono inutili. Nella dashboard corrente: France WC, Argentina WC,
Messini top-scorer, Iran enrichment, Hormuz: alcuni prox-scadenza (buoni), altri
lunghi (pessimi).

### P9 — Nessun filtro liquidita all'ingresso
Si entra a cur_price senza controllare depth del book. Su longshot illiquidi lo
slippage reale >> 1% modellato. Servono best bid/ask size e spread.

## Verify-still-true (controllare o confermare con dati)
- [ ] Path esatto che scrive scan_results con wallet WR<0.55
- [ ] Presenza timestamp ingresso nel feed /positions (serve /activity per delta)
- [ ] Quante delle 6 posizioni aperte hanno scadenza >60gg
- [ ] Spread reale CLOB sui token entryPrice >0.70 del portafoglio

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Copy-trade puntuale via delta-snapshot | Mirroring snap = entrate tardive croniche |
| Soft-disable (non remove) wallet WR<0.55 | Rispetta vincolo "non stravolgere lista" |
| Banda 0.30-0.70 | Backtest config: <0.25 ROI mediano -100%, >0.85 ~0 |
| SL -8% / TP +20% (simmetrico+) | Asimmetria -30/+50 + WR40% = attesa negativa |
| Filtro scadenza 60gg | Evita capital-lock 2028 elections |
| Max 1 pos / wallet sorgente | Vera diversificazione, non same-wallet multi-pos |
| Sizing 3% + reserve 25% | Con WR<50% dimezza rischio capitale |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| Win-rate filtro non enforceato su path legacy | TBD Phase A/B |
| avg_price aggregato unreliable per drift check | Usarlo solo come guardrail molle |

## Resources
- Codice: src/main.py (orchestrator), src/simulator.py (reconcile/sizing),
  src/scanner.py (scan_all legacy vs scan_categories), src/portfolio_sync.py
  (snapshot), src/config.py (BUDGET/STRATEGY/CATEGORIES/ANALYZER)
- Dati: data/portfolio_state.json, data/trades_log.json, data/scan_results.json,
  data/equity_curve.json
- API: gamma (markets), data-api /positions, /holders, /activity, clob /midpoint /book

## Visual/Browser Findings
- Screenshot dashboard 2026-06-30 07:40 mostra 6 posizioni e 9 wallet
- Posizioni aperte tutte "No" su favorite WC + Yes su geopolitici prox-scadenza
- Wallet ROI range 24%-93% (Baosen0412 min, tugator max)