# Task Plan: Fix Polymarket Copy-Trading Bot (strategia in perdita)

## Goal
Trasformare il bot da "mirroring di portafoglio in perdita (-2.09%)" a un sistema
di copy-trading puntuale con aspettativa positiva, SENZA stravolgere la lista dei
wallet monitorati (vincolo esplicito dell'utente). S2O: P&L > 0 su 30 trade paper
con win-rate >= 50%.

## Current Phase
Finito (B-G implementati), in attesa di paper run

## Contesto partenza (snapshot 2026-06-30 07:40)
- Equity $293.73 / $300 -> P&L -$6.27 (-2.09%)
- Realizzato -$4.10 | Non realizzato -$2.17
- 6 aperte, 25 chiuse, Win rate 40% (10W / 15L)
- Strategia: COPY (mirroring snapshot posizioni)
- Budget $300, max 6 posizioni, sizing 5%, reserve 15%
- 9 wallet monitorati (tugator, mombil, CoffeeLover, COMESEECOMESAW, VeeFriends,
  KnightDasCapital, Baosen0412, cocococococo, 0xB8865806)

## Phases

### Phase A: Diagnosi dati (no codice)
- [ ] Ricalcolare realized P&L per categoria, wallet sorgente, banda entry-price
- [ ] Verificare se le perdite vengono da longshot, favorite-selling, o entrata tardiva
- [ ] Statistiche win/loss per wallet sorgente (chi sta trascinando il portafoglio)
- [ ] Capire path codice che ha riempito scan_results con wallet win-rate < 0.55
- [ ] Documentare in findings.md
- **Status:** in_progress

### Phase B: Fix filtri wallet (no replace lista)
- [ ] Hard-enforce min_win_rate 0.55 + min_decided 10 su TUTTI i path (anche legacy)
- [ ] Soft-disable wallet attuali che non passano (size dimezzata, non rimozione)
- [ ] Aggiungere metrica "recency": ROI realizzato posizioni decise ultime 8 sett
- [ ] Non toccare la lista wallet esposta (vincolo utente)
- **Status:** pending

### Phase C: Riprogettare entrata -> copy-trade puntuale (non mirror)
- [ ] Rilevare delta snapshot: nuove posizioni wallet NON presenti al ciclo prima
- [ ] Baseline completa al primo ciclo (NON copiare il bag preesistente)
- [ ] Copiare SOLO ingressi nuovi verificatisi da ultimo ciclo
- [ ] Filtro entry freshness: copia solo se wallet entrato entro ultime N ore
- [ ] Stringere max_entry_drift 12% -> 5%
- **Status:** pending

### Phase D: Filtri mercato / qualita ingresso
- [ ] Banda prezzo entry 0.30-0.70 (out: favorite-selling >0.70 e longshot <0.30)
- [ ] Filtro scadenza: solo mercati endTime nei prossimi 60gg (no 2028 elections)
- [ ] Filtro liquidita: best bid/ask size >= soglia, spread <= 2 tick
- [ ] Evita mercati gia risolti / redeemable al primo check
- **Status:** pending

### Phase E: Risk management rewrite
- [ ] SL/TP simmetrici/favorevoli: SL -8% / TP +20% (era -30/+50 asimmetrico negativo)
- [ ] Opzione B: chiudi SOLO quando wallet sorgente esce + hard SL -15% protezione
- [ ] Max 1 posizione per wallet sorgente (no 3 posizioni stesso wallet)
- [ ] Max 1 posizione per cluster correlato (no 2 bet politica 2028 US insieme)
- **Status:** pending

### Phase F: Sizing adattato
- [ ] Sizing 5% -> 3% finche win rate paper non sale > 50%
- [ ] Max posizioni 6 -> 4 (concentra capitale su segnali migliori)
- [ ] Cash reserve 15% -> 25% durante la fase di fix
- **Status:** pending

### Phase G: Backtest + validazione paper
- [ ] Backtest strategia "copy-trade puntuale + filtri D/E" su 60gg storico
- [ ] Comparare P&L vs strategia attuale su stessi dati
- [ ] Paper 2 sett; graduazione size 4% se P&L > 0 dopo 30 trade
- **Status:** pending

## Key Questions
1. Da quale path e entrato scan_results con wallet win_rate < 0.55? (ipotesi: fallback legacy scan_all in main.run_initial_scan)
2. Il feed /positions offre un timestamp di ingresso per filtro freshness, o serve /activity?
3. Quanto delle 6 posizioni aperte sono mercati a lunga scadenza (>60gg)?
4. Esiste correlazione nel campione chiuso 25: perdite concentrate su favorite-selling (>0.70) o longshot (<0.30)?
5. Il bid-ask size e recuperabile via CLOB /book per il filtro liquidita?

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Non sostituire wallet | Utente esplicito: non creare casini cambindo lista |
| Passare a copy-trade puntuale | Mirroring di snapshot = entrate tardive croniche (P1) |
| Banda 0.30-0.70 | Backtest mostrava <0.25 e >0.85 con ROI mediano -100% / ~0 |
| SL/TP simmetrici | Asimmetria -30/+50 con WR 40% e' attesa negativa |
| Filtro scadenza 60gg | Evita capital lock su 2028 elections (P8) |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| Win-rate filtro non enforceato | 1 | TBD in Phase A (verificare path legacy) |

## Notes
- Polymarket /positions snapshot NON ha timestamp ingresso affidabile; serve /activity per delta-trade.
- Confirmare il path diffuso: main.run_initial_scan chiama scan_all (legacy) SOLO se scan_results assente/dato e fallback. Capire quando e scattato.
- Con budget $300, slippage ~1-3% round-trip domina: ogni errore di timing e insuperabile.