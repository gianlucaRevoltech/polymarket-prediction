# Task Plan: Polymarket Bot — FIX EMERGENZA PERFORMANCE (-5.63%, WR 24%)

## Goal
Fermare l'emorragia di capitale ($300→$283, -5.63%, WR 24%) e trasformare il bot
in un sistema profittevole. ROOT CAUSE: il bot entra a prezzi estremi (0.999, 0.036,
0.026) dove lo stop-loss percentuale viene triggerato dal RUMORE di mercato, non
dal fallimento del segnale. Risk/reward invertito: gain minuscolo, loss enorme.

## Current Phase
Phase CH (Re-validazione e deploy)

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
1. **9 strategie attive, nessuna validata.** Whale/momentum/contrarian non sono
   MAI state backtestate. Solo copy ha backtest 89% WR (in banda 0.30-0.70).
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
