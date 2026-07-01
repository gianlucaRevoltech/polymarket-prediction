# Task Plan: Polymarket Multi-Strategy Bot → obiettivo "raddoppio capitale/settimana"

## Goal
Far diventare il bot (ora -$0.80, WR 20%, ~1 trade/12h) un sistema ATTIVO e
profittevole che possa tendere al raddoppio settimanale del capitale ($300 → $600
in 7gg) MANTENENDO edge positivo: almeno +20% settimana valida come step
intermedio, sizing progressivo (compounding), monitoraggio balance aggressivo.
NON stravolgere a mano i wallet monitorati.

## Architettura multi-strategy (学期nleme 2026-07-01)
Il copy-trading da solo ha un tetto (edge ~89% WR backtest ma sizing 3-12% +
slippage). Per puntare al doubling servono strategie con correlazione bassa che
operano in parallelo sullo stesso portafoglio virtuale. Allocazione capitale
iniziale (paper, rivedibile):
- **COPY** 50% — engine principale, delta per-wallet post Phase I
- **ARB binario** 25% — YES+NO <$1 stesso mercato, profitto certo a risoluzione
- **HARVEST** 15% — mercati quasi-risolti (prezzo 0.92-0.98) con scadenza <7gg
- **ARB cross** 10% — multi-outcome esaustivi somma <$1 (es. torneo, nominee)
Cash non allocato fluisce dove compare l'opportunita migliore a quel ciclo. Ogni
strategia ha suo sizing cap, attribution P&L separata in equity_curve/trades_log.
Skip market-making (adverse selection troppo alto per retail) e value-betting con
modello proprio (sforzo elevato, fase successiva se edge non basta).

## Current Phase
**H..Q IMPLEMENTATI in codice locale + test live 75s OK.** Pronto per deploy VPS:
l'utente copia la folder su VPS e lancia `./start_all.sh restart reset scan`.

## Snapshot dashboard VPS 2026-07-01 09:14
- Equity $299.20 / $300 → P&L -$0.80 (-0.27%)
- Realizzato -$0.62 | Non realizzato -$0.17
- **1 aperta** (Max: 10 su dashboard!) **5 chiuse**, WR **20% (1W/4L)**
- Aperta: Switzerland win 2026-07-02 / Yes @0.487 → $0.475, size $7.19, -2.39%
- 10 wallet monitorati: suntori, c0O0OLI0O03, neutralwave23, mombil, COMESEECOMESAW,
  tugator, VeeFriendsDownUnder, CoffeeLover, Zptml, ChetterHummin (ROI 21-444%)
- Trade recenti (6):
  - Switzerland Yes 07/02 @0.487 size $7.19  ← aperta, in banda 0.30-0.70 OK
  - Egypt Yes 07/03 @0.393 size $9.01  ← DOPPIONE stesso asset riaperto
  - MSI LCK Yes @0.611 size $9.02  ← OK
  - Egypt Yes 07/03 @0.393 size $7.22  ← prima istanza (19:44)
  - Wimbledon Shelton @0.618 size $7.20  ← OK
  - Wimbledon Bublik @0.708 size $7.20  ← **FUORI banda 0.70 → ANOMALIA**

## Phases (vecchie completate A-G, vedi progress.md backup)

### Phase H: Allinea deploy VPS ↔ locale (PRIORITA 1)
Obiettivo: eliminare divergenze che invalidano i filtri progettati.
- [ ] Verificare versione codice su VPS (md5 src/*.py config.py)
- [ ] Diff locale vs VPS: max_open_positions (locale=4 vs dashboard=10)
- [ ] Diff banda entry_price_max (locale=0.70 vs trade Bublik 0.708 PASSATO)
- [ ] Diagnosi: VPS probabilmente usa versione PRE-fix o config modificato
- [ ] Ri-deploy pulito: scp src/ + config.py → VPS; kill+restart bot; verifica BUDGET
- [ ] Verifica dopo restart: filtri SKIP su longshot/coinflip nel log, max 4 pos
- **Status:** in_progress

### Phase I: Fix bug delta-snapshot aggregato (P10) — piu aperture
Obiettivo: cattura ingressi multi-wallet stesso asset (frequenza aperture).
Problema P10: `new_assets = aggregate_keys - prev` aggregato per asset → cattura
SOLO asset che NESSUN wallet aveva, NON ingresso nuovo di un wallet in asset
gia visto. Frequenza aperture ~1/12h.
Soluzione: baseline PER-WALLET, non per-asset:
- [ ] `prev_holdings: Dict[wallet -> Set[asset]]` invece di `prev_assets: Set`
- [ ] `new_holdings: Set[(wallet, asset)] = holdings - prev_holdings`
- [ ] reconcile riceve new_holdings; criterio: se >=1 (wallet,asset) nuovo → open
- [ ] Mantiene il cap per-wallet (max 1 pos/wallet sorgente) e aggancia apertura
      al wallet "piu fresco" per nuovo delta (timestamp ingresso)
- [ ] Bonus: rimuove falsa duplicazione "asset entra/esce/rientra"
- [ ] Test live 1h: confrontare aperture post-fix vs pre-fix
- **Status:** pending

### Phase J: Frequenza aperture aggiuntiva (no edge loss)
- [ ] poll interval 60s → 30s (raddoppia capture, +load API trascurabile)
- [ ] dedup_window (TRACKING 3600s) attualmente INUSATO nel codice: implementare
      effettivo anti-reopen per stessa posizione entro N sec (utile vs Egypt dup)
- [ ] Softenal opportunistic: allentare banda 0.30-0.70 → 0.25-0.75 SOLO per
      asset con consenso >= 2 wallet (WR backtest >0.89 anche li)
- [ ]min_days_to_expiry 1.0 → 0.5: apre sport da 12-24h residui (piu cattura)
      MA resta anti coin-flip 5min crypto
- [ ] max_open_positions 4 → 6 (solo dopo Phase I cattura molto piu signal)
- [ ] Consenso SOLO quando >=2 wallet: per copy puro resta min 1
- **Status:** pending

### Phase K: Sizing compounding verso doubling (onesto)
Obiettivo: avvicinarsi a doubling SENZA blow-up (max size 12% = $36 per trade).
- [ ] Sizing base 3% → 5% (confermato edge live/backtest)
- [ ] After 30 trade paper con WR > 60%: sizing → 8%
- [ ] After 60 trade paper con WR > 55%: sizing → 12%
- [ ] Cap hard sizing <= 15% (anti blow-up su $300)
- [ ] Reserve 25% → 20% (piu capitale operativo, mantieni floor)
- [ ] Compounding: size basato su `total_value` (cresce con equity) non
      `initial_capital`
- [ ] Max posizioni 6 → 8 quando equity > $400
- [ ] Kelly fractional (1/4 Kelly) come upper bound matematico per sizing
- [ ] Clamp: dopo drawdown -10% dal peak, sizing auto -50% per ripristinare floor
- **Status:** pending

### Phase L: Doubling feasibility + monitoraggio balance
Obiettivo: target doubling $300→$600/settimana. Onesto con matematica.
- [ ] Calcolare ROI/trade richiesto: doubling richiede +100% settimana
- [ ] Sizing 5% + WR 70% + 30 trade/sett ≈ +28% settimana (realistico max)
- [ ] Doubling richiede o sizing ~12% WR>70% con 30 trade/sett, o ~50 trade/sett
      con sizing 8%. Documentare trade-off rischio/rendimento.
- [ ] Stretching plan: esempio scenario $300 → $600 in 14 giorni (2 sett, +41%/sett)
      è piu probabile di doubling in 7 giorni
- [ ] Implementare monitoraggio balance VPS:
  - [ ] Alert se equity < floor (-5% da initial): auto-stop aperture nuove
  - [ ] Alert Telegram/log se drawdown -10% dal peak: halve sizing
  - [ ] Metrica settimanale: se +% < target 20% → log alert
- [ ] Dashboard: refresh Equity/Cash/P&L ogni 5min trasmessa a nacho locale
- [ ] Snap report ogni 30 min nel file dashboard: aperture/chiuse/WR/balance
- **Status:** pending

### Phase M: Strategy router architecture
Obiettivo: eseguire N strategie in parallelo sullo stesso portafoglio, allocation.
- [ ] Definire interfaccia `Strategy` unificata: `scan(fetcher) -> List[Opportunity]`,
      `execute(opp, portfolio, fetcher) -> bool`, attribution a `strategy_name`
- [ ] Portfolio: campo `allocation` per strategy con cap % e floor di cash riservato
- [ ] Circolo principale: per ogni strategia, raccoglie opportunita, ordina per
      EV/score, esegue fino al cap-allocazione della strategia (non piu solo copy)
- [ ] trades_log + equity_curve: campo `strategy` (gia parziale per copy)
- [ ] Dashboard: breakdown P&L per strategia + allocation vs utilizzo
- [ ] Risk: drawdown halve sizing si aplica per-strategia (non solo copy)
- [ ] Non bloccare copy quando arb idle: se arb non trova opportunita, cash resta
      disponibile a copy (no silos rigidi, soft caps)
- **Status:** pending

### Phase N: Arbitraggio binario YES+NO <$1 (stesso mercato)
Obiettivo: profitto certo a risoluzione, deploy su cash idle, basso rischio.
Meccanismo: ogni mercato ha 2 outcome token (YES/NO); a settlement uno paga $1
altro $0 → YES+NO=$1 sempre. Se best_ask(YES)+best_ask(NO) < $1 - fees - safety,
compra entrambi → payout $1 garantito qualunque esito. Profitto = $1 - costo.
- [ ] Scanner su tutti i mercati attivi (gamma markets) NON redeemable
- [ ] Per ogni conditionId: recupera i 2 asset_id (outcome YES/NO via gamma
      `markets?slug=...` o clob `markets?condition_id=...`), get_book di entrambi
- [ ] Calcola spread_arb = 1 - (ask_yes + ask_no) - 2*fee_yes - 2*fee_no - safety(0.5c)
- [ ] Fee modellate: sport = rate*min(p,1-p) per leg, altri = 0 → sport quasi
      mai arbabile (fee mangia spread), ma crypto/politics/other 0% → fertile
- [ ] Sizing: min(book_size_yes, book_size_no) - fees, cap a 15% portafoglio per
      singolo arb (concentration risk); rispettare reserve ratio
- [ ] Lock capital-model: profitto solo a resolution → valuta APR non % per-trade;
      filtra endTime < 14gg (no capital-lock lungo → APR basso)
- [ ] Esecuzione simultanea simulata: compra YES e NO allo stesso ciclo,持仓 a
      settlement (close_position resolved). No SL/TP (risk-free se fee calcolata OK)
- [ ] Rischio residuo: book cambia fra get_book e fill (paper OK), mercato
      annullato/refund (raro). Safety margin 0.5c + min profitto $0.50 in assoluto.
- [ ] Attribution: `strategy="arb_binary"` in trade log
- [ ] Backtest 60gg: scanner storico non disponibile (books real-time only) →
      paper-execute live per 7gg, calcolare hit-rate e APR osservata
- **Status:** pending

### Phase O: Harvest risoluzione (near-certain)
Obiettivo: low-risk booster, alta hit-rate, profitto piccolo ma ripetitivo.
Meccanismo: mercati dove esito virtualmente deciso e scadenza <7gg, prezzo
vincente 0.92-0.98. Compra lato vincente → a settlement riscuote $1.
- [ ] Scanner su gamma markets: endTime <7gg, NON redeemable, domanda binaria,
      best_ask sul lato favorito <0.98 con book size sufficiente
- [ ] Score: profitto_assoluto = (1 - ask) * size_fillabile; APR = profitto/days_lock
- [ ] Filtro: ask <= 0.97 (non tutto il juice gia' prezziato), spread <= 2 tick,
      book size >= $20, niente longshot lato perdente (anti reversal rischio)
- [ ] Filtro kampione:evita mercati "sorprendibili" (es. sport blowout ok,elezioni
      referendum NO). Heuristic: categoria sport/yes-or-no affidabile, politics NO
- [ ] Sizing: cap 8% portafoglio per singolo harvest, reserve rispettata
- [ ] SL: NO standard (vincente gia'); hard SL -3% se prezzo crolla sotto 0.90
      → esci (esito NON certo come pensavamo)
- [ ] Attribution: `strategy="harvest"`
- [ ] Rischio: black-swan reversal (cap sizing limita), capital lock breve (OK),
      refund annullamento (raro). Documentare hit-rate su 30 pos paper.
- **Status:** pending

### Phase P: Arbitraggio cross-market (multi-outcome esaustivo)
Obiettivo: mispricing su mercati multi-outcome esaustivi (es. "2028 GOP nominee"
      con A/B/C/D...). Sum best_asks <$1 - fees → compra TUTTI.
- [ ] Trova mercati multi-outcome: gamma `markets?eventId=...` con n outcomes
- [ ] Recupera tutti asset_id per evento, get_book di ciascuno
- [ ] Calcola sum_ask; profitto = 1 - sum_ask - sum_fees
- [ ] n-leg arb: sizing = min(book_size_i) - fees; cap 10% per trade
- [ ] Casi fertili: tornei finite bracket (NOMS候选人), "Top goalscorer WC",
      "Chi vince championship" con campo chiuso. Esclude eventi infiniti.
- [ ] Complessita: n legs → n get_book + n fill; slippage leg × n. Safety 1c
- [ ] Attribution: `strategy="arb_multi"`
- [ ] Vergogna: raro ma quando compare percentuale grande. Paper 30gg per misurare
      frequenza e dimensione media.
- **Status:** pending

### Phase Q (opzionale, fase successiva): value-betting con modello
Obiettivo: edge proprio superiore a copy, scaling sizing maggiore quando WR>70%.
- [ ] Categoria weather: NOAA pubblica probabilita → confronta vs prezzo Polymarket
- [ ] Categoria sport: odds aggregatori (es. the-odds-api) → implied prob vs
      prezzo Polymarket. Bet quando |model_prob - price| > 2*(spread+fee)
- [ ] Kelly fractional 1/4: sizing = 0.25 * (b*p - q)/b con b=payoff, p=model_prob
- [ ] Backtest storico richiede dati prezzi-odds: raccogli 30gg prima di valutare
- [ ] Sforzo alto (mantenimento modello, API odds). SOLO se Phase N/O non bastano
      al target +20-40%/sett dopo 4 settimane paper.
- **Status:** pending (gate)

## Key Questions
1. La VPS sta usando codice pre-fix? Bublik 0.708 e Max 10 posizione lo indicano
2. Quanto recuperato in aperture con delta per-wallet? Stima 3-5x per Phase I
3. WR reale dopo fix P10: 20% e' su 5 trade (pochissimo campione), ma trend?
4. Slippage round-trip su sizing 5-12%: dominato quanto?
5. Kelly fractional richiede P(win) e payoff: conosciuti da backtest → calcolabile
6. L'endpoint gamma `markets?slug=` ritorna entrambi gli asset_id YES/NO per Phase N?
7. Quanto spesso compare arb YES+NO<$1 su crypto/other (fee 0%)? Stima empirica 7gg
8. Harvest: hit-rate real (reversal %)? Rischio black-swan su sport blowout?

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Non sostituire wallet | Vincolo utente |
| Copy-trade puntuale + delta per-wallet | Risolve P10 (aperture scarse) |
| Banda 0.30-0.70 + softenal 0.25-0.75 se >=2 wallet | Edge massimo in banda, extra consenso |
| SL -8% / TP +20% | Breakeven WR 29%, real WR ~50-89% profittevole |
| Sizing compounding 3→5→8→12% | Avvicina doubling senza blow-up |
| Reserve 25→20% + drawdown halve | Protezione capitale in scaling |
| poll 30s + dedup implementato | Cattura real-time ingressi wallet |
| **Multi-strategy router** (COPY+ARB+HARVEST+ARBcross) | Singola strategia ha tetto; bassa correlazione → meglio doubling |
| **ARB binario YES+NO<$1** | Risk-free post-fees, deploy cash idle, focus crypto/other (fee 0%) |
| **HARVEST near-certain 0.92-0.98 <7gg** | Alta hit-rate, capital lock breve, APR alto, sizing limitato |
| **ARB cross multi-outcome esaustivo** | Mispricing grande occasionale, n-leg, sizing piccolo |
| Skip market-making | Adverse selection retail → perditaструк sensibile |
| Value-betting gated a Phase Q | Sforzo modello elevato; solo se altre strategie insufficienti |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| Bublik 0.708 aperto fuori banda | 1 | Phase H: ri-deploy codice locale |
| Egypt Yes riaperto stesso asset (doppione) | 1 | Phase I: delta per-wallet + dedup |
| Dashboard Max 10 vs config 4 | 1 | Phase H: probabile config VPS alterato |
| WR 20% su 5 trade | - | Campione piccolo: attendere 30 trade post-Phase H+I |

## Notes
- Doubling $300→$600 in 7gg e' MATEMATICAMENTE estremo a sizing 3%: serve compounding
  + sizing aggressivo + alta frequenza + alto WR. Realistico STEP: 20-40% settimana
  per prime 2-3 settimane, poi scaling se edge confermato. Obiettivo "duplicare" va
  inquadrato come traguardo pluri-settimanale, di solito 2-4 settimane, non 7 giorni.
- **Multi-strategy aumenta probabilita di doubling**: ARB+HARVEST aggiungono +5-15%/sett
  con rischio molto piu basso del copy, quindi il sizing del copy puo' salire a 12%
  con minor strain complessivo. Rischio-systemic pero' cresce: correlation breakdown
  (mercato illiquido generale) non e' eliminato, mantieni cap per-strategy.
- Anomalie dashboard (Bublik fuori banda, Max 10) suggeriscono codice VPS divergente:
  PRIORITA 1 riallineare deploy. NESSUNA modifica ha senso se la VPS non esegue il codice giusto.
- Sequenza implementativa consigliata: H→I (riparte copy corretto) → N (arb binario,
  il piu' semplice e redditizio per cash idle) → M (router) → O (harvest) → K (sizing
  scaling gated) → L (monitoraggio) → P (arb cross, raro) → Q (solo se serve).