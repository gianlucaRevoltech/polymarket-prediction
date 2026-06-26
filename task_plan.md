# Task Plan: Polymarket Arbitrage Bot

## Goal
Studiare Polymarket, analizzare wallet profittevoli, identificare pattern di trading e sviluppare un bot di copy-trading/arbitraggio che replica le strategie dei wallet di successo.

## Current Phase
Phase 2

## Phases

### Phase 1: Research & Discovery
- [x] Capire cos'è Polymarket e come funziona
- [x] Studiare l'API di Polymarket e la documentazione tecnica
- [x] Capire la struttura dei mercati (CLOB, order book, liquidity)
- [x] Identificare le opportunità di arbitraggio
- [x] Studiare i wallet pubblici e il tracking on-chain
- **Status:** complete

### Phase 2: Analisi Wallet & Strategie
- [ ] Identificare wallet profittevoli su Polymarket
- [ ] Analizzare pattern di trading (entry/exit, sizing, timing)
- [ ] Capire i margini di profitto reali
- [ ] Identificare le strategie più comuni (market making, arbitraggio, directional bets)
- [ ] Documentare le strategie replicabili
- **Status:** pending

### Phase 3: Architettura del Bot
- [ ] Definire stack tecnologico (Python, libraries)
- [ ] Progettare architettura del sistema
- [ ] Definire flussi di dati (market data, wallet tracking, execution)
- [ ] Progettare sistema di segnali e alert
- [ ] Definire risk management
- **Status:** pending

### Phase 4: Implementazione - Data Layer
- [ ] Connettere API Polymarket
- [ ] Implementare tracking wallet on-chain (Polygon)
- [ ] Implementare raccolta market data real-time
- [ ] Implementare analisi storica dei wallet
- **Status:** pending

### Phase 5: Implementazione - Trading Engine
- [ ] Implementare logica di copy-trading
- [ ] Implementare logica di arbitraggio
- [ ] Implementare risk management
- [ ] Implementare execution engine
- **Status:** pending

### Phase 6: Testing & Backtesting
- [ ] Backtesting su dati storici
- [ ] Paper trading (simulazione senza soldi reali)
- [ ] Ottimizzazione parametri
- [ ] Analisi rischio/rendimento
- **Status:** pending

## Key Questions
1. Come funziona il CLOB (Central Limit Order Book) di Polymarket?
2. Quali sono i costi di transazione (gas fees, commissioni)?
3. Come si identificano i wallet profittevoli?
4. Qual è il minimo capitale necessario?
5. Ci sono limiti API o rate limiting?
6. Come funziona il settlement dei mercati?
7. Qual è la differenza tra arbitraggio e copy-trading su Polymarket?

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| Python come linguaggio | Ecosistema ricco per trading, API, crypto |
| Focus su copy-trading + arbitraggio | Due approcci complementari |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
|       |         |            |

## Notes
- Polymarket è basato su Polygon (L2 Ethereum)
- Usa USDC come valuta
- Mercati basati su eventi reali (politica, sport, crypto, etc.)
- CLOB per il trading (non AMM)
- I dati on-chain sono pubblici e tracciabili
