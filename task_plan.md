# Task Plan: Polymarket Multi-Strategy Bot → obiettivo "raddoppio capitale/settimana"

## Goal
Far diventare il bot (dopo deploy 01/07: -$0.81, 4 trade/24h, sizing 3%=$9) un
sistema AGGRESSIVO e profittevole che possa raddoppiare $300 → $600 in 1-2 settimane.
Step intermedio: +30-50%/sett prima settimana. Raddoppio realistico in 10-14gg
con sizing aggressivo + alta frequenza + multi-strategy + wallet rotation.

## Snapshot post-deploy 01/07 (dashboard 02/07 07:12)
- Equity $299.19 / $300 → P&L -$0.81 (-0.27%)
- Realizzato -$0.73 | Non realizzato -$0.09
- **2 aperte** (Spain No @0.909, England No @0.918 = HARVEST), **2 chiuse**, WR 50%
- 4 trade recenti: 2 Alphabet Yes (COPY, size $7.20) + Spain/England No (HARVEST $9)
- 10 wallet monitorati (auto-rescan DISABLED, lista fissa)
- ARB BINARY: 0 opp (mercato efficiente) | ARB CROSS: 0 opp (raro)

## Diagnosi: perché risultati scadenti
1. **Sizing 3% = $9/trade** → troppo piccolo per doubling. 100 trade vincenti a $9
   con +18% = +$162 totale. Servono sizing 8-15% per impatto reale.
2. **Tier 0 bloccato fino a 30 trade** → sizing resta 3% per giorni. Troppo lento.
3. **Solo 4 trade/24h** → frequenza bassissima. Copy trova poco (filtri stretti +
   pochi wallet), harvest capped a 2 pos, arb 0.
4. **Harvest cap 2 posizioni, cap_pct 12%** → solo $36 deployabili su harvest.
   Harvest è la strategia più profittevole (APR 200%+ su WC) ma strozzata.
5. **10 wallet fissi, no rotation** → copy vede pochi ingressi nuovi. Wallet
   migliori cambiano nel tempo; lista statica perde segnali freschi.
6. **Arb binary 0** → mercato efficiente su top-80. Ma con scan più ampio + profit
   min più basso, qualche arb appare (es. mercati meno liquidi).
7. **Niente strategia momentum/value** → copy è l'unica con edge reale ma frequenza
   bassa. Serve almeno una strategia non-correlata attiva.

## Piano aggressivo (Phase R-Y, sessione 2026-07-02)

### Phase R: Sizing aggressivo + tier progression veloce
Obiettivo: sizing partenza 6% (non 3%), scala a 10-15% in poche decine di trade.
- [x] Tier 0: 3% → **6%** (backtest conferma edge, non serve attendere 30 trade)
- [x] Tier thresholds: 0/30/60/120 → **0/10/25/50** (scale up in 1-2 giorni)
- [x] Tier fracs: 6% → **10% → 13% → 15%** (massimo aggressivo ma non blow-up)
- [x] max_position_size floor: 3% → 6%
- [x] reserve_ratio: 20% → **15%** (più capitale operativo)
- [x] max_open_positions: 6 → **12** (più slot per tutte le strategie)
- [x] max_positions_per_wallet: 1 → **2** (copy: anche 2 pos/wallet)
- [x] max_positions_per_category: 2 → **4** (più diversificazione categoria)
- **Status:** complete

### Phase S: Copy più aggressivo (più aperture)
Obiettivo: aumentare frequenza aperture copy senza perdere edge.
- [x] entry banda soft: 0.25-0.75 → **0.20-0.80** (consenso>=2)
- [x] min_book_size_usdc: 50 → **25** (mercati meno liquidi ma ancora tradabili)
- [x] max_spread_ticks: 3 → **4** (accetta spread leggermente più larghi)
- [x] min_days_to_expiry: 0.5 → **0.25** (sport intraday 6h+)
- [x] soft_requires_consensus: 2 → **1** (banda soft anche con 1 wallet fresco)
  → ATTENZIONE: questo allarga molto. Teniamo 2 ma amplifichiamo altrove.
  RIMOSSO: resta soft_requires_consensus=2 per protezione edge.
- [x] max_entry_drift: 0.05 → **0.08** (accetta ingressi un po' più tardivi)
- **Status:** complete

### Phase T: Harvest aggressivo (engine principale per doubling)
Obiettivo: harvest è la strategia più profittevole (APR alta, alta hit-rate).
Sbloccarla: più slot, più sizing, banda più larga, early TP per turnover.
- [x] cap_pct: 12% → **30%** (harvest diventa engine primario)
- [x] max_single: 8% → **15%**
- [x] max_positions: 2 → **6**
- [x] fav_min: 0.85 → **0.78** (cattura anche 0.78-0.85, più opportunità)
- [x] fav_max: 0.975 → **0.985** (cattura anche 0.975-0.985, juice residuo piccolo)
- [x] max_days_to_expiry: 21 → **30** (cattura più mercati a lunga risoluzione)
- [x] min_book: 20 → **15**
- [x] scan_markets: 80 → **150**
- [x] scan_every_cycles: 2 → **1** (scan ogni ciclo, 30s)
- [x] **Early TP su harvest**: se prezzo +4% dall'entry → chiudi (scalp mode)
  → Aumenta turnover: capital lock breve invece di attendere resolution
  → Config: harvest_take_profit_pct = 0.04
- **Status:** complete

### Phase U: Arb binary/cross più aggressivi
Obiettivo: catturare arb rari ma con scan più ampio e profitto min più basso.
- [x] arb_binary cap_pct: 25% → **30%**
- [x] arb_binary max_positions: 1 → **3**
- [x] arb_binary min_profit_abs: 0.50 → **0.20** (micro-arb worthwhile se risk-free)
- [x] arb_binary scan_markets: 80 → **150**
- [x] arb_binary scan_every_cycles: 2 → **1**
- [x] arb_cross scan_events: 12 → **25**
- [x] arb_cross scan_every_cycles: 5 → **2**
- [x] arb_cross min_profit_abs: 1.00 → **0.50**
- **Status:** complete

### Phase V: Wallet rotation + più wallet
Obiettivo: copy vede più segnali freschi. Rotazione automatica wallet top.
- [x] auto_rescan_enabled: False → **True**
- [x] auto_rescan_interval_sec: 6h → **3h** (refresh frequente)
- [x] top_wallets: 20 → **30** (più wallet monitorati)
- [x] SCANNER min_profit: 1000 → **500** (cattura wallet mid-cap più attivi)
- [x] SCANNER min_volume: 10000 → **5000**
- [x] SCANNER min_trades: 10 → **8**
- [x] markets_to_scan: 200 → **300** (più mercati per scoprire wallet)
- **Status:** complete

### Phase W: Nuova strategia MOMENTUM (trend-following)
Obiettivo: strategia non-correlata con copy/arb. Compra mercati con forte trend
di prezzo recente (es. YES salito da 0.30 a 0.55 in 24h → momentum continuation).
- [x] PriceHistory tracker: memorizza prezzi per market across cicli (in-memory + persist)
- [x] MomentumStrategy.scan: rileva mercati con move >X% in finestra N cicli
- [x] Compra lato trending (YES se salita, NO se discesa)
- [x] TP +6% / SL -5% (trend-following, esci se inversione)
- [x] Sizing cap 12%, max_positions 3
- [x] Cap_pct 20% (strategia ad alto turnover)
- **Status:** complete

### Phase X: Faster polling + monitoring
- [x] poll_interval: 30s → **20s** (capture più rapido)
- [x] Monitoraggio: alert se <10 trade/giorno (frequenza insufficiente)
- **Status:** complete

### Phase Y: Deploy + verifica
- [ ] Test live locale 5min (no crash, strategie girano)
- [ ] Deploy su VPS (utente copia folder + restart reset)
- [ ] Verifica post-deploy: sizing 6%, harvest 6 slot, momentum attivo
- [ ] Monitorare 24h: target +5-10% primo giorno, +30-50% settimana
- **Status:** pending

## Phases vecchie (A-Q completate, vedi progress.md)

### Phase H: Allinea deploy VPS ↔ locale — COMPLETE
### Phase I: Fix delta-snapshot per-wallet — COMPLETE
### Phase J: Frequenza aperture — COMPLETE
### Phase K: Sizing compounding — COMPLETE (rivisto Phase R)
### Phase L: Monitoraggio balance — COMPLETE
### Phase M: Strategy router — COMPLETE
### Phase N: Arb binario — COMPLETE (rivisto Phase U)
### Phase O: Harvest — COMPLETE (rivisto Phase T)
### Phase P: Arb cross — COMPLETE (rivisto Phase U)
### Phase Q: Value-betting — gated (sostituito da Phase W momentum)

## Key Questions
1. Harvest early TP +4%: i prezzi near-certain si muovono abbastanza per triggerare?
   (Test live: Spain No 0.909→0.900 = -1%, non ha ancora triggerato TP. Normale,
   TP si attiva sui mercati dove confidence aumenta verso resolution)
2. Momentum: qual è la finestra ottimale? Iniziare con 6 cicli (~2min a 20s poll)
   e move threshold 5%. Tunare dopo 24h live.
3. Wallet rotation 3h: il rescan impatta performance? Scanner fa ~200 richieste,
   3h = 8 rescans/giorno. Accettabile.
4. Sizing 6% = $18/trade. 50 trade vincenti/sett a +10% = +$90 = +30%. OK per
   target +30-50%/sett. Con sizing 10% (tier1) → 50 trade = +$150 = +50%.
5. Risk: 12 posizioni simultanee con sizing 6% = $216 deployati (72% portafoglio).
   Reserve 15% = $45. Cash operating = $255. OK se non tutte sizing-piena.

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Sizing 6% partenza (non 3%) | Backtest 89% WR valida edge; 3% troppo timido per doubling |
| Tier progression 0/10/25/50 | Scale up in 1-2 giorni invece di settimane |
| Harvest early TP +4% | Turnover capitale vs attesa resolution; compounding più veloce |
| Harvest cap 30% + 6 pos | Engine primario: APR alta, hit-rate alta, risk可控 |
| fav_min 0.78 (era 0.85) | Più opportunità; rischio reversal accettabile con SL -10% |
| Momentum strategy nuova | Strategia non-correlata, alto turnover, sfrutta trend Polymarket |
| Wallet rotation 3h + 30 wallet | Segnali copy freschi; wallet top cambiano nel tempo |
| poll 20s | Capture real-time; +load API trascurabile |
| max_open 12 + per-wallet 2 | Più slot = più posizioni simultanee = più P&L |
| Reserve 15% | Più capitale operativo; floor comunque protetto |
| Arb min_profit $0.20 | Micro-arb risk-free worthwhile su cash idle |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| Risultati scadenti post-deploy (-$0.81, 4 trade) | 1 | Phase R-Y: sizing aggressivo + harvest + momentum + wallet rotation |
| PriceHistory.cleanup_stale cancellava tutto dopo ciclo 1 | 1 | Fix: rimuovi solo entry stale (>200 cicli), non entry fresche con 1 punto |
| _should_scan con every=1 non scansionava MAI ((cycle%1)==1 sempre False) | 1 | Fix: special-case every<=1 return True |

## Notes
- Doubling $300→$600 in 7gg con sizing 6-10% + 50-80 trade/sett + WR 65% + harvest
  APR + momentum = **matematicamente possibile ma aggressivo**. Realistico 10-14gg.
- Beta risk: sizing 10% + 12 posizioni = drawdown possibile -15% in giornata se
  tutto va male. Equity floor -5% blocca nuove aperture; drawdown halve -12% riduce
  sizing. Reserve 15% = floor $45. Hard ruin -20% = stop totale.
- Harvest early TP cambia profilo: da "lock capital until resolution" a "scalp
  near-certain moves". Aumenta turnover 3-5x. Rischio: lascia juice su tavolo se
  market poi resolve a $1. Tradeoff accettabile per obiettivo doubling.
- Momentum strategy è sperimentale: primo ciclo non ha history (baseline), serve
  ~6 cicli (2min) per primo signal. Tunare threshold dopo 24h live.
