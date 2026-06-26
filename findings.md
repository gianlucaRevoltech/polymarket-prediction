# Findings & Decisions

## Requirements
- Studiare e analizzare Polymarket come piattaforma
- Analizzare wallet profittevoli per capire le strategie
- Sviluppare un bot che fa copy-trading di wallet di successo
- Identificare opportunità di arbitraggio
- Calcolare profitto potenziale

## Research Findings

### Polymarket Overview (COMPLETATO)
- Piattaforma di prediction market basata su Polygon (L2 Ethereum)
- Usa USDC come valuta di scambio
- CLOB (Central Limit Order Book) per matching ordini
- Mercati binari Yes/No: prezzo $0-$1 (probabilità implicita)
- Settlement: mercato risolve a $1 (win) o $0 (lose)
- Fee: 5% taker-only, rebate 25% per maker
- Tick size: $0.01, Min order: $5

### Architettura Tecnica (VERIFICATO)
- Smart contracts: Conditional Token Framework (CTF) su Polygon
- CLOB off-chain matching, settlement on-chain
- API endpoints funzionanti:
  - Gamma API: https://gamma-api.polymarket.com/markets
  - CLOB API: https://clob.polymarket.com/book?token_id={id}
  - Leaderboard: Embeddato in HTML pagina (no API pubblica diretta)

### Order Book Structure (VERIFICATO)
Esempio mercato "New Rihanna Album before GTA VI":
- Best bid: $0.51 (size: 254)
- Best ask: $0.54 (size: 31.09)
- Spread: $0.03 (3 cents)
- Liquidità profonda: ordini da $0.01 a $0.99
- Last trade: $0.51

### Leaderboard & Wallet Tracking (COMPLETATO)
**Top 10 Trader per Profitto (30 giorni):**
1. mintblade: $9,238,344 PnL | $17,759,922 volume
2. fishalive: $9,063,378 PnL | $13,281,460 volume
3. frostrizz: $8,928,561 PnL | $23,091,318 volume
4. sparklingwater123: $8,474,966 PnL | $19,001,698 volume
5. GRIMDRIP: $7,602,742 PnL | $13,603,969 volume
6. endlessFate: $7,409,836 PnL | $26,282,164 volume
7. swisstony: $5,652,377 PnL | $370,232,331 volume (alto volume!)
8. BAREFLUX: $4,761,593 PnL | $21,662,777 volume
9. BreakTheBank: $4,261,885 PnL | $77,800,799 volume
10. Inaccuratestake: $3,947,667 PnL | $19,153,226 volume

**Top 10 per Volume (30 giorni):**
1. swisstony: $370M volume | $5.6M PnL
2. 0x2c33...0563: $285M volume | $2.5M PnL
3. suntori: $222M volume | -$2.5M PnL (LOSS!)
4. asjabaasj: $209M volume | $547 PnL
5. ferrariChampions2026: $173M volume | -$1.7M PnL

**Biggest Wins (30 giorni) - FIFA World Cup 2026:**
1. GRIMDRIP: $7.6M profit (Czechia vs South Africa)
2. mintblade: $7.3M profit (IR Iran vs New Zealand)
3. frostrizz: $5.8M profit (Türkiye vs Paraguay)
4. endlessFate: $5.6M profit (Saudi Arabia vs Uruguay)
5. fishalive: $4.7M profit (Spain vs Cabo Verde)

**Wallet Address Esempio:**
- mintblade: 0x96cfcb0c30942cfcd1cdf76c7d408794d66b1acb
- fishalive: 0xed64a7bf029040aa331abc87902434d815ef217d
- swisstony: 0x204f72f35326db932158cba6adff0b9a1da95e14
- frostrizz: 0xbc11a64ab34a03a043fbe80598fa065ee87eeec6

### Opportunità di Arbitraggio Identificate
1. **Arbitraggio intra-mercato**: Yes + No ≠ $1 (raro, mercati efficienti)
2. **Arbitraggio cross-mercato**: Mercati correlati con pricing inconsistente
3. **Arbitraggio temporale**: Reazione lenta a notizie (news trading)
4. **Copy-trading**: Replicare mosse wallet profittevoli (FATTIBILE!)
5. **Market making**: Fornire liquidità e catturare spread

### Strategia Sportiva (OSSERVAZIONE CHIAVE)
- FIFA World Cup 2026 domina i profitti
- Trader specializzati in sport fanno $4-9M in 30 giorni
- Pattern: entrano prima delle partite, escono dopo risultati
- Mercati sportivi hanno alta liquidità e volatilità

### Categorie Mercato
- Sports: 2651 eventi attivi
- Politics: 1397 eventi attivi
- Crypto: 3507 eventi attivi
- Geopolitics: 383 eventi attivi
- Economy: 150 eventi attivi
- Tech: 252 eventi attivi

### Polymarket Overview
- Piattaforma di prediction market basata su Polygon (L2 Ethereum)
- Usa USDC come valuta di scambio
- Ha un CLOB (Central Limit Order Book) per il matching degli ordini
- I mercati sono basati su eventi binari (Yes/No): politica, sport, crypto, etc.
- Le quote vanno da $0 a $1 (rappresentano la probabilità percepita)
- Settlement: il mercato risolve a $1 (win) o $0 (lose)
- Fondata nel 2020, cresciuta enormemente nel 2024 (elezioni USA)

### Architettura Tecnica
- Smart contracts su Polygon (CTF - Conditional Token Framework)
- CLOB off-chain matching, settlement on-chain
- API REST per trading e data
- WebSocket per real-time data
- Gamma Markets API per dati di mercato

### Opportunità di Arbitraggio
1. **Arbitraggio intra-mercato**: Se Yes + No < $1 o > $1 (raro su mercati liquidi)
2. **Arbitraggio cross-mercato**: Mercati correlati con pricing inconsistente
3. **Arbitraggio temporale**: Reazione lenta del mercato a notizie
4. **Copy-trading**: Replicare le mosse di wallet profittevoli

### Wallet Tracking
- Tutti i dati on-chain sono pubblici su Polygon
- Polygonscan permette di tracciare transazioni
- Polymarket ha un leaderboard pubblico
- Strumenti terzi: Polymarket tracker, Dune Analytics dashboards

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Python | Ecosistema ricco per trading/crypto |
| py-clob-client (SDK ufficiale) | Client Python ufficiale per Polymarket |
| Web3.py | Interazione diretta con smart contracts Polygon |
| Polygon RPC | Per leggere transazioni on-chain |

## Resources
- Polymarket Docs: https://docs.polymarket.com
- CLOB API: https://docs.polymarket.com/#introduction
- Polymarket GitHub: https://github.com/Polymarket
- py-clob-client: https://github.com/Polymarket/py-clob-client
- Polygon Scan: https://polygonscan.com
- CTF Contract: Conditional Token Framework
- Polymarket Leaderboard: https://polymarket.com/leaderboard
